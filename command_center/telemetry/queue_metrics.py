"""Real series for the queue, its leases and its workers (SRV-08-WORKER-METRICS).

This is the module that turns the contract in `contract.py` from names into
timeseries. It is read-only by construction, in the same sense and for the same
reason as `db/work_queue_read.py`: no INSERT, no UPDATE, no protocol function
call. An exporter is the last thing that should be able to mutate the queue it
observes, and the way to guarantee that is to have nothing here that could.

WHERE THE NUMBERS COME FROM
===========================
Four aggregate queries over `work_item_public` and `work_attempt_public` — the
redacted views `aicc_app` already holds SELECT on. `work_attempt` itself is
granted to nobody because it carries `claim_token_hash`; the public view drops
that column, so this collector never sees a credential it would then have to be
trusted not to log.

EVERY AGE IS DIFFERENCED IN SQL. `now() - heartbeat_at` is computed by
PostgreSQL and arrives as a number of seconds, rather than a timestamp this
process subtracts from its own clock. `contract.py` states the reasoning at
length; the short form is that lease expiry is decided by the database's clock,
so an alert about lease health measured by any other clock is measuring a
different quantity and fires on skew.

WHY FOUR QUERIES AND NOT ONE
============================
They group differently — by (queue, state), by queue, by (role, queue) — and a
single query producing all three shapes would be a UNION of dissimilar rows
reassembled in Python, which is the same work with an extra decoding step and
no transactional benefit. There is no consistency requirement between them
worth a REPEATABLE READ snapshot either: these are gauges sampled every 30s,
and a claim landing between query two and query three moves a number by one for
one scrape. Pretending otherwise would be false precision.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from command_center.telemetry import contract
from command_center.telemetry.exposition import Family, Sample, render

logger = logging.getLogger(__name__)

__all__ = ["QueueMetricsCollector", "render_exposition"]


# Items per queue and state. Cheap: a grouped count over an indexed column.
_ITEMS_SQL = """
SELECT queue, state, count(*)::bigint
  FROM work_item_public
 GROUP BY queue, state
"""

# Dispatch lag, over the CLAIMABLE subset only.
#
# `ready` and `claimable` are not the same population: queue_fail() pushes
# available_at into the future for its backoff, so a queue can hold a hundred
# ready items and offer none. Measuring lag over `ready` would report a backlog
# that is actually a retry storm serving its penalty — an alert that fires
# hardest exactly when the system is correctly throttling itself.
#
# The age is measured from `available_at`, not `created_at`: it answers "how
# long has claimable work gone unclaimed", which is a statement about the
# worker fleet. `now() - created_at` would answer "how long since enqueue",
# which folds the deliberate backoff wait back into the number this exists to
# keep it out of.
_LAG_SQL = """
SELECT queue,
       count(*)::bigint AS claimable,
       COALESCE(
           EXTRACT(EPOCH FROM (now() - min(available_at)))::float8,
           0::float8
       ) AS oldest_age
  FROM work_item_public
 WHERE state = 'ready'
   AND available_at <= now()
 GROUP BY queue
"""

# Lease health per queue. `lapsed` counts attempts the database would already
# refuse a heartbeat from — the reaper's backlog, not a correctness signal.
_LEASE_SQL = """
SELECT i.queue,
       count(*)::bigint AS active,
       COALESCE(
           EXTRACT(EPOCH FROM (now() - min(a.created_at)))::float8,
           0::float8
       ) AS oldest_age,
       count(*) FILTER (WHERE a.visible_until <= now())::bigint AS lapsed
  FROM work_attempt_public a
  JOIN work_item_public i ON i.work_item_id = a.work_item_id
 WHERE a.state = 'active'
 GROUP BY i.queue
"""

# Per-worker liveness, scoped to workers holding at least one active attempt.
#
# `heartbeat_age` is a MAX and `expires_in` a MIN: both pick the worst attempt
# this worker holds, because one silent lease is a lost worker regardless of
# how healthy its other attempts look.
#
# COALESCE(heartbeat_at, created_at) is load-bearing. An attempt claimed two
# seconds ago has not beaten yet and `heartbeat_at` is NULL; treating that NULL
# as infinitely old would make WorkerLost fire on every healthy claim. The
# claim itself is the first proof of life, so it is the right fallback.
#
# `visibility` is the window of the MOST OVERDUE attempt, not the min or max
# across them — it has to be the same attempt `heartbeat_age` came from, or the
# alert would compare one attempt's staleness against another's tolerance.
# array_agg with an ORDER BY inside the aggregate is the argmax.
_WORKER_SQL = """
SELECT claimed_by_role,
       queue,
       count(*)::bigint AS active,
       max(hb_age)::float8 AS heartbeat_age,
       (array_agg(visibility_seconds ORDER BY hb_age DESC))[1]::float8 AS visibility,
       min(expires_in)::float8 AS expires_in
  FROM (
        SELECT a.claimed_by_role,
               i.queue,
               a.visibility_seconds,
               EXTRACT(EPOCH FROM (now() - COALESCE(a.heartbeat_at, a.created_at)))
                   AS hb_age,
               EXTRACT(EPOCH FROM (a.visible_until - now())) AS expires_in
          FROM work_attempt_public a
          JOIN work_item_public i ON i.work_item_id = a.work_item_id
         WHERE a.state = 'active'
       ) s
 GROUP BY claimed_by_role, queue
"""


class QueueMetricsCollector:
    """Aggregate reads over the queue's public views, as metric families."""

    def __init__(
        self,
        connection_factory: Any = None,
        *,
        expected_queues: tuple[str, ...] = (),
    ) -> None:
        """``expected_queues`` names queues that must appear even when they
        hold no rows at all.

        Discovery alone cannot do this. A queue is only discoverable from the
        items in it, so a queue that has never been enqueued to — or whose rows
        were archived — produces no series, and "no series" is exactly what a
        dead exporter produces too. Naming the queue an operator expects turns
        that silence into an explicit zero. It defaults to empty because
        guessing the fleet's queue names here would be a second authority over
        a value that lives in the worker's own config.
        """
        self._factory = connection_factory
        self._expected_queues = tuple(expected_queues)

    def _connection(self) -> Any:
        if self._factory is not None:
            return self._factory()
        from command_center.db import pool

        return pool.connection()

    def collect(self) -> list[Family]:
        """Every data family, in contract order. Raises if the database is
        unreachable — `render_exposition` is what turns that into `up 0`."""
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_ITEMS_SQL)
                item_rows = cur.fetchall()
                cur.execute(_LAG_SQL)
                lag_rows = cur.fetchall()
                cur.execute(_LEASE_SQL)
                lease_rows = cur.fetchall()
                cur.execute(_WORKER_SQL)
                worker_rows = cur.fetchall()

        queues = sorted(
            {str(row[0]) for row in item_rows}
            | {str(row[0]) for row in lease_rows}
            | set(self._expected_queues)
        )

        return [
            *self._item_families(item_rows, queues),
            *self._lag_families(lag_rows, queues),
            *self._lease_families(lease_rows, queues),
            *self._worker_families(worker_rows),
        ]

    # -- families -------------------------------------------------------------

    def _item_families(self, rows: list[tuple], queues: list[str]) -> list[Family]:
        # Zero-filled across the schema's state vocabulary: a `ready` series
        # that vanishes when the queue drains cannot be told apart from an
        # exporter that stopped, and `absent()` rules covering that are a worse
        # contract than an explicit zero.
        counts = {(str(q), str(s)): int(n) for q, s, n in rows}
        samples = [
            Sample({"queue": queue, "state": state}, counts.get((queue, state), 0))
            for queue in queues
            for state in contract.ITEM_STATES
        ]
        return [Family(contract.QUEUE_ITEMS, samples)]

    def _lag_families(self, rows: list[tuple], queues: list[str]) -> list[Family]:
        by_queue = {str(q): (int(n), float(age)) for q, n, age in rows}
        claimable, oldest = [], []
        for queue in queues:
            count, age = by_queue.get(queue, (0, 0.0))
            claimable.append(Sample({"queue": queue}, count))
            oldest.append(Sample({"queue": queue}, age))
        return [
            Family(contract.QUEUE_READY_CLAIMABLE, claimable),
            Family(contract.QUEUE_OLDEST_READY_AGE, oldest),
        ]

    def _lease_families(self, rows: list[tuple], queues: list[str]) -> list[Family]:
        by_queue = {
            str(q): (int(active), float(age), int(lapsed))
            for q, active, age, lapsed in rows
        }
        active_s, oldest_s, lapsed_s = [], [], []
        for queue in queues:
            active, age, lapsed = by_queue.get(queue, (0, 0.0, 0))
            active_s.append(Sample({"queue": queue}, active))
            oldest_s.append(Sample({"queue": queue}, age))
            lapsed_s.append(Sample({"queue": queue}, lapsed))
        return [
            Family(contract.LEASE_ACTIVE, active_s),
            Family(contract.LEASE_OLDEST_AGE, oldest_s),
            Family(contract.LEASE_LAPSED, lapsed_s),
        ]

    def _worker_families(self, rows: list[tuple]) -> list[Family]:
        # NOT zero-filled, and this is the one place that asymmetry is correct:
        # the population is "workers holding work", so a worker with no active
        # attempt has no series by definition rather than by absence. Emitting
        # a zero for it would require a roster this database does not have —
        # see the contract's "what is deliberately not here".
        active, hb_age, visibility, expires = [], [], [], []
        for role, queue, count, age, vis, exp in sorted(rows, key=lambda r: (r[0], r[1])):
            labels = {"worker_role": str(role), "queue": str(queue)}
            active.append(Sample(labels, int(count)))
            hb_age.append(Sample(labels, float(age)))
            visibility.append(Sample(labels, float(vis)))
            expires.append(Sample(labels, float(exp)))
        return [
            Family(contract.WORKER_ACTIVE_ATTEMPTS, active),
            Family(contract.WORKER_HEARTBEAT_AGE, hb_age),
            Family(contract.WORKER_LEASE_VISIBILITY, visibility),
            Family(contract.WORKER_LEASE_EXPIRES_IN, expires),
        ]


def render_exposition(
    collector: QueueMetricsCollector,
    *,
    clock: Callable[[], float] = time.time,
) -> str:
    """Collect and render, turning a database failure into `up 0` rather than
    an exception.

    A collector that raises leaves the previous `.prom` file in place, and the
    node exporter goes on serving stale numbers indefinitely with no signal
    that they stopped moving. Writing `up 0` with a fresh generated-timestamp
    keeps the failure visible and distinguishable from the exporter itself
    having died — which is what `QueueMetricsStale` covers, and the two need to
    be separable to be actionable: one is a database problem, the other is a
    timer problem.

    ``clock`` is the exporter host's wall clock, and it is the one clock here
    that is deliberately NOT the database's. It is only ever compared against
    Prometheus's own `time()` for staleness, so it has to be in that frame; and
    when the database is unreachable there is no database clock to ask, which
    is precisely the moment the timestamp matters most.
    """
    started = time.monotonic()
    try:
        families = collector.collect()
        up = 1.0
    except Exception:  # noqa: BLE001 -- the boundary of the exporter
        logger.exception("queue metrics collection failed")
        families = []
        up = 0.0
    duration = time.monotonic() - started

    families.extend(
        [
            Family(contract.QUEUE_METRICS_UP, [Sample({}, up)]),
            Family(contract.QUEUE_METRICS_GENERATED, [Sample({}, clock())]),
            Family(contract.QUEUE_METRICS_DURATION, [Sample({}, duration)]),
        ]
    )
    return render(families)
