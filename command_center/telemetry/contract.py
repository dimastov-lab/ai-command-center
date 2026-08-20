"""The queue/lease/worker metric contract (VOYN-W0-AICC-SRV-08-WORKER-METRICS).

WHY THIS MODULE EXISTS
======================
The task this file closes recorded a specific defect: *the metric names baked
into the alert rules are a contract for SRV-04, not implemented telemetry — the
``WorkerLost`` rule is loaded but no series exist, so it cannot fire.*

Measured before writing any of this, not assumed:

* ``grep -rn WorkerLost`` over every checkout under ``~/Projects`` and over
  every git ref in this repository (``git log --all --diff-filter=A``) returns
  nothing. The rule was never committed here.
* No Prometheus server runs on this host and none is installed
  (``apt-cache policy prometheus`` → ``Installed: (none)``). The only metric
  substrate is ``prometheus-node-exporter``'s textfile collector, fed from
  ``/usr/local/sbin/voyn-service-metrics`` on a 30s timer, which emits exactly
  two families (``voyn_service_active`` / ``voyn_service_enabled``) for two
  units and knows nothing about the queue.

So the honest reading is stronger than the ticket's: neither the rule NOR the
series existed. A rules file alone would have reproduced the very defect being
fixed — a name with nothing behind it. This module is therefore the single
authority for the names, and everything that could otherwise drift from it is
derived or tested against it:

    contract.py  ─┬─→ queue_metrics.py   (the collector emits these names)
                  ├─→ deploy/prometheus/aicc_queue_rules.yml  (rules use them)
                  └─→ deploy/grafana/aicc-worker-platform.json  (panels use them)

``tests/telemetry/test_contract_is_the_only_authority.py`` fails if a rule or a
dashboard panel references a metric this module does not define, and if the
collector emits a family this module does not declare. A renamed metric that
silences an alert is the failure mode this arrangement exists to prevent: it
cannot be renamed in one place.

THE CLOCK DECISION, which is what makes these series trustworthy
================================================================
Every ``*_age_seconds`` and ``*_expires_in_seconds`` value here is computed by
PostgreSQL's ``now()`` inside the collecting query — never by subtracting a
stored timestamp from the exporter host's clock, and never by Prometheus's
``time()``.

That is not a stylistic preference. ``0002_queue_claim.up.sql`` states the
invariant the whole protocol rests on: "Both are server now(); no client clock
is ever stored." Lease expiry is decided by the database's clock, so an alert
about lease health that used any other clock would be measuring a different
quantity than the one the protocol enforces — and would fire, or fail to fire,
on clock skew between the exporter host and the database. Ages arrive already
differenced, by the same clock that decides the thing being alerted on.

The cost is that these gauges are only as fresh as the last collection, which
is why ``QUEUE_METRICS_GENERATED`` exists and why ``QueueMetricsStale`` is a
rule: a gauge whose age is computed at write time is silently wrong if the
writer stopped, and every other rule in the file is blind to its own absence.

WHAT IS DELIBERATELY NOT HERE
=============================
*A fleet roster.* "Which workers SHOULD be alive" is not knowable from this
database. Worker identity is ``session_user`` at claim time
(``work_attempt.claimed_by_role``, written by trigger), so the queue can only
observe workers that have claimed something. ``0003_worker_enrollment`` holds
enrolled roles, but enrolment is not liveness — a role enrolled months ago on a
decommissioned host is not a lost worker. The ``aicc_worker_*`` families below
are therefore scoped to workers *holding work*, which is the population where
"lost" has an operational meaning: an idle worker that vanishes costs nothing,
while a worker that vanishes holding a lease blocks that item until the lease
lapses. Broadening this needs a heartbeat registry, which is SRV-02's shape and
not something to fake here.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

__all__ = [
    "GAUGE",
    "COUNTER",
    "Metric",
    "METRICS",
    "METRIC_NAMES",
    "NAMESPACE",
    "QUEUE_ITEMS",
    "QUEUE_OLDEST_READY_AGE",
    "QUEUE_READY_CLAIMABLE",
    "LEASE_ACTIVE",
    "LEASE_OLDEST_AGE",
    "LEASE_LAPSED",
    "WORKER_ACTIVE_ATTEMPTS",
    "WORKER_HEARTBEAT_AGE",
    "WORKER_LEASE_VISIBILITY",
    "WORKER_LEASE_EXPIRES_IN",
    "QUEUE_METRICS_UP",
    "QUEUE_METRICS_GENERATED",
    "QUEUE_METRICS_DURATION",
    "ITEM_STATES",
    "missed_heartbeat_threshold_expr",
]

NAMESPACE = "aicc"

GAUGE = "gauge"
COUNTER = "counter"


@dataclass(frozen=True, slots=True)
class Metric:
    """One metric family: its name, type, label set and help text.

    ``labels`` is a tuple rather than a set because it is also the ORDER the
    exposition renderer emits them in. Prometheus does not care about label
    order, but a stable order makes the ``.prom`` file diffable, which is what
    lets an operator see "the same series, a different value" instead of a
    whole-file churn on every 30s rewrite.
    """

    name: str
    type: str
    labels: tuple[str, ...]
    help: str


def _m(name: str, type_: str, labels: tuple[str, ...], help_: str) -> Metric:
    return Metric(name=f"{NAMESPACE}_{name}", type=type_, labels=labels, help=help_)


# ---------------------------------------------------------------------------
# Queue depth and lag
# ---------------------------------------------------------------------------

# The state vocabulary belongs to the schema (`work_item.state`), and
# work_queue_read.py already pins the same four. Listed here because the
# collector emits a zero for every (queue, state) pair rather than omitting
# empty ones: a `ready` series that disappears when the queue drains is
# indistinguishable from an exporter that stopped, and `absent()` rules to
# cover that are a worse contract than an explicit zero.
ITEM_STATES = ("ready", "claimed", "succeeded", "dead")

QUEUE_ITEMS = _m(
    "queue_items",
    GAUGE,
    ("queue", "state"),
    "Work items per queue and state, zero-filled across the schema's state vocabulary.",
)

QUEUE_OLDEST_READY_AGE = _m(
    "queue_oldest_ready_age_seconds",
    GAUGE,
    ("queue",),
    "Age of the oldest claimable ready item (queue lag). 0 when nothing is claimable.",
)

QUEUE_READY_CLAIMABLE = _m(
    "queue_ready_claimable",
    GAUGE,
    ("queue",),
    # `ready` and `claimable` differ by backoff: queue_fail() pushes
    # available_at into the future, so a queue can hold many ready items and
    # offer none. Alerting on `ready` alone reports a backlog that is in fact
    # a retry storm serving its penalty, so lag is measured over this subset.
    "Ready items whose available_at has arrived, i.e. claimable by the next claim().",
)

# ---------------------------------------------------------------------------
# Leases
# ---------------------------------------------------------------------------

LEASE_ACTIVE = _m(
    "lease_active",
    GAUGE,
    ("queue",),
    "Attempts in state 'active' — leases currently held by some worker.",
)

LEASE_OLDEST_AGE = _m(
    "lease_oldest_age_seconds",
    GAUGE,
    ("queue",),
    "Age of the oldest active attempt, measured from its claim.",
)

LEASE_LAPSED = _m(
    "lease_lapsed",
    GAUGE,
    ("queue",),
    # The reaper's own backlog. A lapsed-but-active attempt is invisible to
    # claims already, so this is not a correctness signal — it measures how far
    # behind aicc-queue-reaper.timer is running, which is a recovery-latency
    # signal and the only way an operator sees a reaper that stopped.
    "Active attempts already past visible_until, awaiting queue_reap().",
)

# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------
#
# Scoped to workers holding at least one active attempt — see the module
# docstring. `worker_role` is `work_attempt.claimed_by_role`: the PostgreSQL
# role the server authenticated, written by trigger and unforgeable by any
# function argument, so this label cannot be spoofed by the worker it names.

WORKER_ACTIVE_ATTEMPTS = _m(
    "worker_active_attempts",
    GAUGE,
    ("worker_role", "queue"),
    "Active attempts held by this worker role.",
)

WORKER_HEARTBEAT_AGE = _m(
    "worker_heartbeat_age_seconds",
    GAUGE,
    ("worker_role", "queue"),
    # coalesce(heartbeat_at, created_at): an attempt claimed seconds ago has
    # not beaten yet and is not late. Using NULL-as-infinite here would fire
    # WorkerLost on every healthy claim.
    "Seconds since this worker's most overdue held attempt last beat (or was claimed).",
)

WORKER_LEASE_VISIBILITY = _m(
    "worker_lease_visibility_seconds",
    GAUGE,
    ("worker_role", "queue"),
    # Exported so the alert threshold is derived from the lease rather than
    # hard-coded. See missed_heartbeat_threshold_expr().
    "Visibility window of this worker's most overdue held attempt.",
)

WORKER_LEASE_EXPIRES_IN = _m(
    "worker_lease_expires_in_seconds",
    GAUGE,
    ("worker_role", "queue"),
    "Seconds until this worker's soonest lease lapses; negative once lapsed.",
)

# ---------------------------------------------------------------------------
# The collector's own health
# ---------------------------------------------------------------------------

QUEUE_METRICS_UP = _m(
    "queue_metrics_up",
    GAUGE,
    (),
    "1 when the last collection reached the database and completed, 0 otherwise.",
)

QUEUE_METRICS_GENERATED = _m(
    "queue_metrics_generated_timestamp_seconds",
    GAUGE,
    (),
    "Unix time the exposition was written, for staleness detection.",
)

QUEUE_METRICS_DURATION = _m(
    "queue_metrics_collection_duration_seconds",
    GAUGE,
    (),
    "Wall-clock duration of the last collection.",
)


METRICS: tuple[Metric, ...] = (
    QUEUE_ITEMS,
    QUEUE_OLDEST_READY_AGE,
    QUEUE_READY_CLAIMABLE,
    LEASE_ACTIVE,
    LEASE_OLDEST_AGE,
    LEASE_LAPSED,
    WORKER_ACTIVE_ATTEMPTS,
    WORKER_HEARTBEAT_AGE,
    WORKER_LEASE_VISIBILITY,
    WORKER_LEASE_EXPIRES_IN,
    QUEUE_METRICS_UP,
    QUEUE_METRICS_GENERATED,
    QUEUE_METRICS_DURATION,
)

METRIC_NAMES = frozenset(metric.name for metric in METRICS)

BY_NAME = MappingProxyType({metric.name: metric for metric in METRICS})


# ---------------------------------------------------------------------------
# The alert threshold, as an expression rather than a number
# ---------------------------------------------------------------------------

# How many heartbeat intervals a worker may miss before it is provably lost.
#
# Two, because that is the daemon's OWN stated tolerance rather than a number
# chosen here. `worker/daemon.py::_heartbeat_loop` sets the beat interval to
# `visibility_seconds / 3` with the comment: "A third of the window: two
# consecutive beats may fail (a restarting PostgreSQL, a network blip) before
# the lease actually lapses." So a worker whose most overdue attempt has not
# beaten for two intervals has exhausted the tolerance the daemon was built
# with — the next missed beat is a lapsed lease, not a blip.
MISSED_BEATS_BEFORE_LOST = 2

# The daemon's divisor. Named rather than inlined so the relationship to
# `_heartbeat_loop` is greppable from both ends.
BEATS_PER_VISIBILITY_WINDOW = 3


def missed_heartbeat_threshold_expr() -> str:
    """The PromQL right-hand side of ``WorkerLost``.

    Returns a vector expression, not a scalar: the threshold is derived per
    series from that worker's own visibility window, so a queue running short
    leases and one running long leases are both alerted at *their* two missed
    beats. A hard-coded seconds value would have to be re-tuned every time a
    caller passes a different ``visibility_seconds`` to ``claim()`` — which the
    protocol explicitly allows, clamping only to [1, 3600].

    The two operands carry identical label sets (``worker_role``, ``queue``),
    so the comparison in the rule matches on all of them without an explicit
    ``on()``; scalar-vector arithmetic here preserves those labels.
    """
    return (
        f"{MISSED_BEATS_BEFORE_LOST} * "
        f"({WORKER_LEASE_VISIBILITY.name} / {BEATS_PER_VISIBILITY_WINDOW})"
    )
