"""BO-S2: the dispatch protocol and the planner tick, on real PostgreSQL.

The atomic act under the real ``aicc_app`` grants, the wave gate under the
approved semantics (a later numeric wave yields only while the earliest
unfinished numeric wave still has a dispatchable candidate), and the whole
plan_once composition end to end — dispatch through the store, execution
through a real worker-role claim, lane release on the terminal state.

Skipped wholesale unless ``AICC_TEST_PG_ADMIN_DSN`` is set — see ``conftest``.
"""

from __future__ import annotations

import json
import secrets

import pytest

from command_center.db import roles
from command_center.db.backlog_parser import ParsedTask
from command_center.db.backlog_store import BacklogStore
from command_center.db.work_queue_store import ClaimedWork, WorkQueueStore
from command_center.orchestrator.planner import PlanLimits, plan_once

pytestmark = [pytest.mark.serial, pytest.mark.usefixtures("role_passwords")]


def _as_role(dsn: str, role: str, password: str) -> str:
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    params = conninfo_to_dict(dsn)
    params.update(user=role, password=password)
    return make_conninfo(**params)


def _provision(admin_conn, psycopg, test_dsn, role_passwords) -> None:
    from command_center.db import migrations

    roles.apply_bootstrap(admin_conn)
    with psycopg.connect(
        _as_role(test_dsn, roles.MIGRATOR_ROLE, role_passwords[roles.MIGRATOR_ROLE]),
        autocommit=True,
    ) as conn:
        migrations.upgrade(conn)
        roles.apply_table_grants(conn)


@pytest.fixture
def rig(admin_conn, psycopg, test_dsn, role_passwords):
    """(app_factory, backlog_store, worker_queue_store) under real grants."""
    from contextlib import contextmanager

    from psycopg import sql

    _provision(admin_conn, psycopg, test_dsn, role_passwords)
    name = f"aicc_wh_plan_{secrets.token_hex(4)}"
    password = secrets.token_urlsafe(24)
    with admin_conn.cursor() as cur:
        for statement in roles.render_worker_host_role(name):
            cur.execute(statement)
        cur.execute(
            sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                sql.Identifier(name), sql.Literal(password)
            )
        )
    app_dsn = _as_role(test_dsn, roles.APP_ROLE, role_passwords[roles.APP_ROLE])
    worker_dsn = _as_role(test_dsn, name, password)

    def factory_for(dsn):
        @contextmanager
        def factory():
            with psycopg.connect(dsn, autocommit=True) as conn:
                yield conn

        return factory

    app_factory = factory_for(app_dsn)
    try:
        yield (
            app_factory,
            BacklogStore(app_factory),
            WorkQueueStore(factory_for(worker_dsn)),
        )
    finally:
        with admin_conn.cursor() as cur:
            try:
                cur.execute(
                    sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(name))
                )
            except Exception:  # noqa: BLE001 — cleanup must not mask a failure
                admin_conn.rollback()


def _task(task_id: str, **overrides) -> ParsedTask:
    values = dict(
        task_id=task_id,
        wave="0",
        priority="P0",
        status="OPEN",
        kind="task",
        title=task_id.lower(),
        body="do the thing",
        repo=f"repo-{task_id[-2:]}",
        line_no=1,
    )
    values.update(overrides)
    return ParsedTask(**values)


def _dispatch(app_factory, task_id, planner="planner-t", wip=4, payload=None):
    with app_factory() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM backlog_dispatch(%s, %s, 3600, %s, %s::jsonb, 3)",
                (task_id, planner, wip, json.dumps(payload or {"kind": "agent_run"})),
            )
            return cur.fetchone()


def test_dispatch_is_one_atomic_act(rig) -> None:
    """Lease + enqueue + IN_PROGRESS + audit together; a refusal leaves NO
    trace of any step."""
    app_factory, store, _worker = rig
    assert store.upsert_task(_task("VOYN-W0-AT"))[0]
    ok, _reason, work_item_id, revision = _dispatch(app_factory, "VOYN-W0-AT")
    assert ok and work_item_id.startswith("wki") and revision == 2
    task = store.get_task("VOYN-W0-AT")
    assert task["status"] == "IN_PROGRESS"
    with app_factory() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT state FROM work_item_public WHERE task_id = %s", ("VOYN-W0-AT",)
            )
            assert cur.fetchall() == [("ready",)]
            cur.execute(
                "SELECT count(*) FROM backlog_writer_lease "
                "WHERE authority = %s AND owner = %s",
                ("repo:" + task["repo"], "planner-t"),
            )
            assert cur.fetchone()[0] == 1

    # A refusal (already IN_PROGRESS) mutates nothing further.
    ok, reason, *_ = _dispatch(app_factory, "VOYN-W0-AT")
    assert not ok and reason == "not_eligible"


def test_refusals_leave_no_lease_and_no_work_item(rig) -> None:
    app_factory, store, _worker = rig
    assert store.upsert_task(_task("VOYN-W0-D1", repo="repo-shared"))[0]
    assert store.upsert_task(_task("VOYN-W0-D2", repo="repo-d2"))[0]
    assert store.add_dependency("VOYN-W0-D2", "VOYN-W0-D1")[0]

    ok, reason, *_ = _dispatch(app_factory, "VOYN-W0-D2")
    assert not ok and reason == "dependencies_unsatisfied"
    with app_factory() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM work_item_public")
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT count(*) FROM backlog_writer_lease")
            assert cur.fetchone()[0] == 0


def test_one_writer_per_repository_across_planners(rig) -> None:
    app_factory, store, _worker = rig
    assert store.upsert_task(_task("VOYN-W0-R1", repo="repo-one"))[0]
    assert store.upsert_task(_task("VOYN-W0-R2", repo="repo-one"))[0]
    assert _dispatch(app_factory, "VOYN-W0-R1", planner="planner-a")[0]
    ok, reason, *_ = _dispatch(app_factory, "VOYN-W0-R2", planner="planner-b")
    assert not ok and reason == "repo_busy"
    # The SAME planner may take a second task in its held repo? No: the lease
    # renews for the holder, so the dispatch proceeds — one WRITER, not one
    # task, is the invariant; WIP is the task cap.
    assert _dispatch(app_factory, "VOYN-W0-R2", planner="planner-a")[0]


def test_wip_limit_is_enforced_in_the_database(rig) -> None:
    app_factory, store, _worker = rig
    for i in range(3):
        assert store.upsert_task(_task(f"VOYN-W0-W{i}", repo=f"repo-w{i}"))[0]
    assert _dispatch(app_factory, "VOYN-W0-W0", wip=2)[0]
    assert _dispatch(app_factory, "VOYN-W0-W1", wip=2)[0]
    ok, reason, *_ = _dispatch(app_factory, "VOYN-W0-W2", wip=2)
    assert not ok and reason == "wip_exhausted"


def test_the_wave_gate_yields_exactly_when_the_earlier_wave_is_spent(rig) -> None:
    """Approved decision 1, both directions: refused while the earliest
    numeric wave has a dispatchable candidate; admitted the moment it has
    none (each remaining task blocked by deps, busy repo, or no repo).
    Named lanes bypass throughout."""
    app_factory, store, _worker = rig
    assert store.upsert_task(_task("VOYN-W0-GA", repo="repo-ga"))[0]
    assert store.upsert_task(_task("VOYN-W1-GB", wave="1", repo="repo-gb"))[0]
    assert store.upsert_task(_task("VOYN-COM-GC", wave="COM", repo="repo-gc"))[0]

    ok, reason, *_ = _dispatch(app_factory, "VOYN-W1-GB")
    assert not ok and reason == "earlier_wave_has_eligible_work"
    assert _dispatch(app_factory, "VOYN-COM-GC")[0], "named lanes are always parallel"

    assert _dispatch(app_factory, "VOYN-W0-GA")[0]
    ok, reason, *_ = _dispatch(app_factory, "VOYN-W1-GB")
    assert ok, f"wave 0 spent, wave 1 must be admitted (got {reason})"


def test_planner_tick_end_to_end_with_a_real_worker(rig) -> None:
    """plan_once dispatches by wave order, reports the wave gate, skips the
    repo-less; a real worker-role claim executes and dead-letters; the next
    tick releases the lane."""
    app_factory, store, worker = rig
    assert store.upsert_task(_task("VOYN-W0-P1", repo="repo-p1"))[0]
    assert store.upsert_task(_task("VOYN-W0-P2", priority="P1", repo=None))[0]
    assert store.upsert_task(_task("VOYN-W1-P3", wave="1", repo="repo-p3"))[0]

    limits = PlanLimits(planner="planner-e2e", wip_limit=2, max_dispatches_per_tick=2)
    report = plan_once(app_factory, limits)
    assert [t for t, _ in report.dispatched] == ["VOYN-W0-P1", "VOYN-W1-P3"], (
        "wave 0 spent itself on P1, so wave 1 rides the SAME tick — the "
        "approved non-blockade semantics"
    )
    assert report.undispatchable == [("VOYN-W0-P2", "no_repo")]
    assert report.skipped_by_wave_gate == []

    # The queue delivers to a real worker; a non-retryable failure (budget 1
    # link -> attempts land on the same route) dead-letters the item.
    claimed = worker.claim("execution", visibility_seconds=60)
    assert isinstance(claimed, ClaimedWork)
    assert claimed.payload["kind"] == "agent_run"
    assert claimed.payload["cascade"], "the planner must route through the cascade"
    assert worker.fail(claimed, reason="synthetic failure", retryable=False)

    report2 = plan_once(app_factory, limits)
    assert ("VOYN-W0-P1", "dead") in report2.released
    with app_factory() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM backlog_writer_lease WHERE authority = %s",
                ("repo:repo-p1",),
            )
            assert cur.fetchone()[0] == 0, "the dead lane must be freed"


def test_two_planner_ticks_cannot_run_concurrently(rig) -> None:
    app_factory, _store, _worker = rig
    with app_factory() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ok FROM backlog_lease_acquire('planner:global', 'other-host', 60)"
            )
            assert cur.fetchone()[0]
    report = plan_once(app_factory, PlanLimits(planner="planner-late"))
    assert report.planner_busy and report.dispatched == []
