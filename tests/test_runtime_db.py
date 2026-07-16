import multiprocessing
import sqlite3
import threading
import uuid
from pathlib import Path

import pytest

from command_center.runtime import db


def _fresh_db(tmp_path):
    path = tmp_path / "runtime.db"
    db.migrate(path)
    return path


# Top-level (picklable) worker functions for the real multi-*process* tests
# below — a separate OS process, not just a separate thread in this same
# interpreter, each opening its own sqlite3 connection to the same file.


def _mp_migrate_worker(path_str: str) -> None:
    from command_center.runtime import db as _db

    _db.migrate(Path(path_str))


def _mp_barrier_migrate_worker(path_str: str, barrier, result_queue) -> None:
    """Like `_mp_migrate_worker`, but synchronizes every worker on a shared
    `multiprocessing.Barrier` before calling `migrate()`, so all processes
    call `sqlite3.connect()`/`PRAGMA journal_mode=WAL` against the
    not-yet-existent db file at, as close as an OS scheduler allows,
    literally the same instant — a genuine race, not merely "started close
    together". Reports outcome via `result_queue` rather than relying only
    on exitcode, so the test can see *what* failed, not just *that* it did."""
    from command_center.runtime import db as _db

    barrier.wait()
    try:
        _db.migrate(Path(path_str))
        result_queue.put(("ok", None))
    except BaseException as exc:  # noqa: BLE001 - must surface every failure mode to the test, not just crash silently
        code = getattr(exc, "sqlite_errorcode", None)
        name = getattr(exc, "sqlite_errorname", None)
        result_queue.put(("FAIL", f"{exc!r} code={code} name={name}"))


def _mp_event_writer_worker(path_str: str, run_id: str, process_idx: int, n_events: int) -> None:
    from command_center.runtime import db as _db

    path = Path(path_str)
    for i in range(n_events):
        _db.append_run_event(path, run_id, "lifecycle", {"process_idx": process_idx, "i": i})


# --------------------------------------------------------------------------
# Migrations / schema versioning
# --------------------------------------------------------------------------


def test_migrate_creates_schema_version_row(tmp_path):
    path = _fresh_db(tmp_path)
    assert db.current_schema_version(path) == db.SCHEMA_VERSION


def test_migrate_is_idempotent(tmp_path):
    path = tmp_path / "runtime.db"
    db.migrate(path)
    db.migrate(path)
    db.migrate(path)
    assert db.current_schema_version(path) == db.SCHEMA_VERSION
    with db.connect(path) as conn:
        rows = conn.execute("SELECT COUNT(*) AS c FROM schema_version").fetchone()
        # Only one row per migration actually applied, not one per migrate() call.
        assert rows["c"] == len(db.MIGRATIONS)


def test_migrate_creates_all_expected_tables(tmp_path):
    path = _fresh_db(tmp_path)
    with db.connect(path) as conn:
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    for expected in ("task", "session", "run", "run_event", "report", "schema_version"):
        assert expected in tables


def test_migrate_on_existing_populated_db_does_not_lose_data(tmp_path):
    path = _fresh_db(tmp_path)
    task = db.create_task(path, project="AIOS", title="t", task_type="implementation")
    db.migrate(path)  # re-run against a populated db
    assert db.get_task(path, task["id"]) is not None


# --------------------------------------------------------------------------
# WAL / busy_timeout / foreign_keys configuration
# --------------------------------------------------------------------------


def test_connection_uses_wal_journal_mode(tmp_path):
    path = _fresh_db(tmp_path)
    with db.connect(path) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"


def test_connection_sets_busy_timeout(tmp_path):
    path = _fresh_db(tmp_path)
    with db.connect(path) as conn:
        timeout_ms = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert timeout_ms == 30000


def test_connection_enables_foreign_keys(tmp_path):
    path = _fresh_db(tmp_path)
    with db.connect(path) as conn:
        enabled = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert enabled == 1


def test_foreign_keys_cascade_delete_task_removes_descendants(tmp_path):
    path = _fresh_db(tmp_path)
    task = db.create_task(path, project="AIOS", title="t", task_type="implementation")
    session = db.create_session(path, task_id=task["id"], project="AIOS", repository_path="/tmp/x")
    run = db.create_run(
        path,
        session_id=session["id"],
        task_id=task["id"],
        project="AIOS",
        task_type="implementation",
        repository_path="/tmp/x",
        prompt="p",
        is_resume=False,
    )
    db.append_run_event(path, run["id"], "lifecycle", {"lifecycle": "x"})

    with db.connect(path) as conn:
        with db.transaction(conn):
            conn.execute("DELETE FROM task WHERE id = ?", (task["id"],))

    assert db.get_session(path, session["id"]) is None
    assert db.get_run(path, run["id"]) is None
    assert db.list_run_events(path, run["id"]) == []


def test_foreign_keys_reject_orphan_session(tmp_path):
    path = _fresh_db(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        db.create_session(path, task_id="no-such-task", project="AIOS", repository_path="/tmp/x")


# --------------------------------------------------------------------------
# Concurrent writers (WAL + busy_timeout should serialize, not fail)
# --------------------------------------------------------------------------


def test_concurrent_writers_append_events_without_error_or_loss(tmp_path):
    path = _fresh_db(tmp_path)
    task = db.create_task(path, project="AIOS", title="t", task_type="implementation")
    session = db.create_session(path, task_id=task["id"], project="AIOS", repository_path="/tmp/x")
    run = db.create_run(
        path,
        session_id=session["id"],
        task_id=task["id"],
        project="AIOS",
        task_type="implementation",
        repository_path="/tmp/x",
        prompt="p",
        is_resume=False,
    )

    errors: list[Exception] = []
    n_threads = 8
    n_events_per_thread = 15

    def writer(idx: int) -> None:
        try:
            for i in range(n_events_per_thread):
                db.append_run_event(path, run["id"], "lifecycle", {"thread": idx, "i": i})
        except Exception as exc:  # pragma: no cover - failure path under test
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"concurrent writers raised: {errors}"
    events = db.list_run_events(path, run["id"], limit=10_000)
    assert len(events) == n_threads * n_events_per_thread
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs), "sequence numbers must be unique (no lost/duplicated seq under concurrency)"


# F8: a genuine multi-*process* writer test (separate OS processes, each with
# its own sqlite3 connection/interpreter), not only multi-threaded contention
# within one process. This is not a hypothetical scenario for this codebase —
# independent review reproduced two separate CLI invocations racing against
# the same db file (see F1/F5).


def test_true_multi_process_writers_append_events_without_error_or_loss(tmp_path):
    path = tmp_path / "runtime.db"
    db.migrate(path)
    task = db.create_task(path, project="AIOS", title="t", task_type="implementation")
    session = db.create_session(path, task_id=task["id"], project="AIOS", repository_path="/tmp/x")
    run = db.create_run(
        path, session_id=session["id"], task_id=task["id"], project="AIOS", task_type="implementation",
        repository_path="/tmp/x", prompt="p", is_resume=False,
    )

    n_procs = 4
    n_per_proc = 10
    ctx = multiprocessing.get_context("spawn")
    procs = [
        ctx.Process(target=_mp_event_writer_worker, args=(str(path), run["id"], i, n_per_proc))
        for i in range(n_procs)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)

    for i, p in enumerate(procs):
        assert p.exitcode == 0, f"writer process {i} exited with code {p.exitcode}"

    events = db.list_run_events(path, run["id"], limit=10_000)
    assert len(events) == n_procs * n_per_proc
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs), "sequence numbers must be unique across separate OS processes, not just threads"


def test_concurrent_first_time_migration_from_separate_processes_is_safe(tmp_path):
    """F5: two processes racing to apply migration N for the first time
    against a brand-new db file must not corrupt `schema_version` — the
    loser's INSERT hits the `PRIMARY KEY` on `version` and is swallowed as
    'already recorded', not raised."""
    path = tmp_path / "runtime.db"
    n_procs = 4
    ctx = multiprocessing.get_context("spawn")
    procs = [ctx.Process(target=_mp_migrate_worker, args=(str(path),)) for _ in range(n_procs)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)

    for i, p in enumerate(procs):
        assert p.exitcode == 0, f"migrate() worker {i} exited with code {p.exitcode}"

    assert db.current_schema_version(path) == db.SCHEMA_VERSION
    with db.connect(path) as conn:
        rows = conn.execute("SELECT version, COUNT(*) AS c FROM schema_version GROUP BY version").fetchall()
        assert len(rows) == len(db.MIGRATIONS)
        for row in rows:
            assert row["c"] == 1, "schema_version.version must stay unique even under concurrent first-time migration"


# --------------------------------------------------------------------------
# Barrier-synchronized concurrent first-time migration: a genuine race
# (every worker released at the same instant, against a database path that
# does not yet exist), repeated enough times to reliably catch an
# intermittent regression rather than getting lucky once. This is the
# regression coverage for the "database is locked" fix in `connect()` /
# `_migration_2_add_failure_reason` — before that fix, this scenario failed
# roughly 1 run in 5 with an uncaught `sqlite3.OperationalError: database is
# locked` raised from `PRAGMA journal_mode=WAL`; after fixing that, it
# uncovered a second, previously-masked race in migration 2's check-then-add
# `ALTER TABLE` (both fixed in `command_center/runtime/db.py`).
# --------------------------------------------------------------------------

_STRESS_REPETITIONS = 12
_STRESS_PROCS_PER_REPETITION = 6


def test_concurrent_first_time_migration_is_safe_under_a_genuine_barrier_synchronized_race(tmp_path):
    ctx = multiprocessing.get_context("spawn")

    for attempt in range(_STRESS_REPETITIONS):
        path = tmp_path / f"race_{attempt}" / "runtime.db"
        barrier = ctx.Barrier(_STRESS_PROCS_PER_REPETITION)
        result_queue = ctx.Queue()
        procs = [
            ctx.Process(target=_mp_barrier_migrate_worker, args=(str(path), barrier, result_queue))
            for _ in range(_STRESS_PROCS_PER_REPETITION)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=60)

        results = [result_queue.get_nowait() for _ in range(_STRESS_PROCS_PER_REPETITION)]
        failures = [r for r in results if r[0] != "ok"]
        assert not failures, (
            f"attempt {attempt}: {len(failures)}/{_STRESS_PROCS_PER_REPETITION} workers failed "
            f"(no worker may ever see an uncaught database-locked/busy exception): {failures}"
        )
        for i, p in enumerate(procs):
            assert p.exitcode == 0, f"attempt {attempt}: worker {i} exited with code {p.exitcode}"

        # Every returned database must be fully, correctly migrated — no
        # partial migration state, no duplicate schema objects.
        assert db.current_schema_version(path) == db.SCHEMA_VERSION, f"attempt {attempt}: schema not fully applied"
        with db.connect(path) as conn:
            version_rows = conn.execute(
                "SELECT version, COUNT(*) AS c FROM schema_version GROUP BY version"
            ).fetchall()
            assert len(version_rows) == len(db.MIGRATIONS), f"attempt {attempt}: wrong number of migrations recorded"
            for row in version_rows:
                assert row["c"] == 1, f"attempt {attempt}: duplicate schema_version row for version {row['version']}"

            tables = {
                r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            for expected in ("task", "session", "run", "run_event", "report", "schema_version"):
                assert expected in tables, f"attempt {attempt}: missing table {expected!r}"

            run_columns = {r["name"] for r in conn.execute("PRAGMA table_info(run)").fetchall()}
            assert "failure_reason" in run_columns, (
                f"attempt {attempt}: migration 2's failure_reason column is missing "
                "(the exact partial-migration-state failure mode this test guards against)"
            )

            # The returned connection/database must be genuinely usable, not
            # just structurally present — a real write-then-read round trip.
            task = db.create_task(path, project="AIOS", title="race check", task_type="implementation")
            assert db.get_task(path, task["id"])["title"] == "race check"


# --------------------------------------------------------------------------
# Negative test: an unrelated OperationalError must never be retried or
# hidden by the busy/locked retry wrapper — only a genuine
# SQLITE_BUSY/SQLITE_LOCKED condition may be retried.
# --------------------------------------------------------------------------


def test_retry_on_busy_does_not_retry_or_swallow_unrelated_operational_errors():
    call_count = 0

    def always_raises_unrelated_error():
        nonlocal call_count
        call_count += 1
        raise sqlite3.OperationalError("no such table: definitely_not_a_lock_issue")

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        db._retry_on_busy(always_raises_unrelated_error, deadline_seconds=5.0)

    assert call_count == 1, "an unrelated OperationalError must propagate on the first attempt, never retried"


def test_retry_on_busy_does_not_wrap_unrelated_operational_errors_in_the_busy_timeout_error():
    def raises_unrelated_error():
        raise sqlite3.OperationalError("unable to open database file")

    try:
        db._retry_on_busy(raises_unrelated_error, deadline_seconds=5.0)
        pytest.fail("expected sqlite3.OperationalError to propagate")
    except db.DatabaseBusyTimeoutError:
        pytest.fail("an unrelated OperationalError must never be reclassified as a busy/locked timeout")
    except sqlite3.OperationalError:
        pass  # expected: the original, unmodified exception type propagates


def test_is_busy_or_locked_false_for_synthetic_unrelated_operational_errors():
    """A hand-constructed `OperationalError` (not produced by the sqlite3 C
    extension) has no `sqlite_errorcode`/`sqlite_errorname`, exercising the
    message-substring fallback path — it must still correctly classify a
    non-lock error as non-retryable."""
    assert db._is_busy_or_locked(sqlite3.OperationalError("no such column: bogus")) is False
    assert db._is_busy_or_locked(sqlite3.OperationalError("syntax error")) is False


def test_is_busy_or_locked_true_for_a_genuine_sqlite_busy_error(tmp_path):
    """Forces a *real* SQLITE_BUSY from the sqlite3 C extension (not a
    hand-constructed message string) by holding a write lock open on one
    connection while a second, short-timeout connection tries to write —
    confirms `_is_busy_or_locked` correctly recognizes the real thing, not
    just a string that happens to look like it."""
    path = tmp_path / "busy.db"
    db.migrate(path)

    holder = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    contender = sqlite3.connect(str(path), timeout=0.2, isolation_level=None)
    try:
        holder.execute("BEGIN IMMEDIATE")
        holder.execute("CREATE TABLE IF NOT EXISTS _busy_probe (x INTEGER)")
        with pytest.raises(sqlite3.OperationalError) as excinfo:
            contender.execute("BEGIN IMMEDIATE")
            contender.execute("INSERT INTO task (id, project, title, task_type, created_at, updated_at) VALUES ('x','x','x','x','x','x')")
        assert db._is_busy_or_locked(excinfo.value) is True
    finally:
        holder.execute("ROLLBACK")
        holder.close()
        try:
            contender.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass
        contender.close()


def test_retry_on_busy_succeeds_once_the_lock_clears():
    """A busy condition that clears before the deadline must succeed
    transparently — the caller gets a normal return value, not an
    exception, and the retry loop stops as soon as `fn()` stops raising."""
    attempts = []

    def fails_twice_then_succeeds():
        attempts.append(1)
        if len(attempts) < 3:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    result = db._retry_on_busy(fails_twice_then_succeeds, deadline_seconds=5.0)
    assert result == "ok"
    assert len(attempts) == 3


def test_retry_on_busy_raises_domain_error_with_original_cause_when_deadline_exhausted():
    def always_busy():
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(db.DatabaseBusyTimeoutError) as excinfo:
        db._retry_on_busy(always_busy, deadline_seconds=0.05)

    assert isinstance(excinfo.value.__cause__, sqlite3.OperationalError)
    assert "database is locked" in str(excinfo.value.__cause__)


# --------------------------------------------------------------------------
# Compare-and-set: lost-update prevention
# --------------------------------------------------------------------------


def _make_run(path):
    task = db.create_task(path, project="AIOS", title="t", task_type="implementation")
    session = db.create_session(path, task_id=task["id"], project="AIOS", repository_path="/tmp/x")
    return db.create_run(
        path,
        session_id=session["id"],
        task_id=task["id"],
        project="AIOS",
        task_type="implementation",
        repository_path="/tmp/x",
        prompt="p",
        is_resume=False,
    )


def test_update_run_state_succeeds_with_correct_version(tmp_path):
    path = _fresh_db(tmp_path)
    run = _make_run(path)
    updated = db.update_run_state(path, run["id"], expected_version=run["version"], new_state="QUEUED")
    assert updated["state"] == "QUEUED"
    assert updated["version"] == run["version"] + 1


def test_update_run_state_raises_lost_update_on_stale_version(tmp_path):
    path = _fresh_db(tmp_path)
    run = _make_run(path)
    db.update_run_state(path, run["id"], expected_version=run["version"], new_state="QUEUED")
    with pytest.raises(db.LostUpdateError):
        # `run["version"]` is now stale (already consumed above).
        db.update_run_state(path, run["id"], expected_version=run["version"], new_state="RUNNING")


def test_concurrent_cas_updates_exactly_one_writer_wins(tmp_path):
    """Two threads race to CAS-update the same run's non-state fields using the
    same (correct-at-read-time) expected_version; exactly one may succeed and
    the other must observe a lost update, purely from the version mismatch
    (not from any state-transition legality question, which
    `update_run_fields` does not evaluate at all)."""
    path = _fresh_db(tmp_path)
    run = _make_run(path)

    results: list[str] = []
    lock = threading.Lock()

    def try_update(pid_value: int) -> None:
        try:
            db.update_run_fields(path, run["id"], expected_version=run["version"], fields={"pid": pid_value})
            with lock:
                results.append(f"ok:{pid_value}")
        except db.LostUpdateError:
            with lock:
                results.append("lost")

    t1 = threading.Thread(target=try_update, args=(111,))
    t2 = threading.Thread(target=try_update, args=(222,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert sorted(results) in (["lost", "ok:111"], ["lost", "ok:222"])
    final = db.get_run(path, run["id"])
    assert final["pid"] in (111, 222)
    assert final["version"] == run["version"] + 1


# --------------------------------------------------------------------------
# Terminal-state protection
# --------------------------------------------------------------------------


@pytest.mark.parametrize("terminal_state", sorted(db.TERMINAL_STATES))
def test_terminal_states_never_transition_anywhere(tmp_path, terminal_state):
    path = _fresh_db(tmp_path)
    run = _make_run(path)
    run = db.update_run_state(path, run["id"], expected_version=run["version"], new_state="QUEUED")
    run = db.update_run_state(path, run["id"], expected_version=run["version"], new_state="RUNNING")
    run = db.update_run_state(path, run["id"], expected_version=run["version"], new_state=terminal_state)

    for target in db.RUN_STATES:
        if target == terminal_state:
            continue
        with pytest.raises(db.InvalidTransitionError):
            db.update_run_state(path, run["id"], expected_version=run["version"], new_state=target)


def test_completed_run_cannot_silently_return_to_running(tmp_path):
    path = _fresh_db(tmp_path)
    run = _make_run(path)
    run = db.update_run_state(path, run["id"], expected_version=run["version"], new_state="QUEUED")
    run = db.update_run_state(path, run["id"], expected_version=run["version"], new_state="RUNNING")
    run = db.update_run_state(path, run["id"], expected_version=run["version"], new_state="COMPLETED")
    with pytest.raises(db.InvalidTransitionError):
        db.update_run_state(path, run["id"], expected_version=run["version"], new_state="RUNNING")
    assert db.get_run(path, run["id"])["state"] == "COMPLETED"


def test_invalid_transition_does_not_bump_version(tmp_path):
    path = _fresh_db(tmp_path)
    run = _make_run(path)
    run = db.update_run_state(path, run["id"], expected_version=run["version"], new_state="QUEUED")
    run = db.update_run_state(path, run["id"], expected_version=run["version"], new_state="RUNNING")
    run = db.update_run_state(path, run["id"], expected_version=run["version"], new_state="COMPLETED")
    version_before = run["version"]
    with pytest.raises(db.InvalidTransitionError):
        db.update_run_state(path, run["id"], expected_version=run["version"], new_state="FAILED")
    assert db.get_run(path, run["id"])["version"] == version_before


# --------------------------------------------------------------------------
# F6: unknown update fields are rejected before any SQL is built
# --------------------------------------------------------------------------


def test_update_run_state_rejects_unknown_field(tmp_path):
    path = _fresh_db(tmp_path)
    run = _make_run(path)
    with pytest.raises(db.UnknownRunFieldError):
        db.update_run_state(
            path, run["id"], expected_version=run["version"], new_state="QUEUED",
            fields={"prompt": "attempted overwrite of a write-once column"},
        )
    # Must not have partially applied — the row is untouched.
    assert db.get_run(path, run["id"])["version"] == run["version"]
    assert db.get_run(path, run["id"])["state"] == "PREPARED"


def test_update_run_fields_rejects_unknown_field(tmp_path):
    path = _fresh_db(tmp_path)
    run = _make_run(path)
    with pytest.raises(db.UnknownRunFieldError):
        db.update_run_fields(
            path, run["id"], expected_version=run["version"],
            fields={"session_id": "attempted overwrite of a write-once column"},
        )
    assert db.get_run(path, run["id"])["version"] == run["version"]


def test_update_run_fields_accepts_every_documented_updatable_field(tmp_path):
    path = _fresh_db(tmp_path)
    run = _make_run(path)
    # `cancel_requested` is NOT NULL (default 0); every other updatable field
    # is nullable. This exercises the full allowlist with schema-valid values.
    fields = {key: (0 if key == "cancel_requested" else None) for key in db._UPDATABLE_RUN_FIELDS}
    updated = db.update_run_fields(path, run["id"], expected_version=run["version"], fields=fields)
    assert updated["version"] == run["version"] + 1


# --------------------------------------------------------------------------
# Task 1:N Session, Session 1:N Run, Run 0..1 Report, Run 1:N RunEvent
# --------------------------------------------------------------------------


def test_auto_generated_session_id_is_a_canonical_dashed_uuid(tmp_path):
    """`session.id` is passed straight to `claude --session-id`/`--resume`,
    which rejects anything that isn't a valid UUID string — verified against
    the real `claude` CLI during Sprint 1 end-to-end validation, which caught
    an earlier version of this code generating a bare 32-char hex digest
    (`uuid4().hex`, no dashes) instead."""
    path = _fresh_db(tmp_path)
    task = db.create_task(path, project="AIOS", title="t", task_type="implementation")
    session = db.create_session(path, task_id=task["id"], project="AIOS", repository_path="/tmp/x")
    parsed = uuid.UUID(session["id"])
    assert str(parsed) == session["id"]
    assert "-" in session["id"]


def test_task_can_have_multiple_sessions(tmp_path):
    path = _fresh_db(tmp_path)
    task = db.create_task(path, project="AIOS", title="t", task_type="implementation")
    s1 = db.create_session(path, task_id=task["id"], project="AIOS", repository_path="/tmp/x")
    s2 = db.create_session(path, task_id=task["id"], project="AIOS", repository_path="/tmp/x")
    sessions = db.list_sessions(path, task_id=task["id"])
    assert {s["id"] for s in sessions} == {s1["id"], s2["id"]}


def test_session_can_have_multiple_runs_with_incrementing_sequence(tmp_path):
    path = _fresh_db(tmp_path)
    task = db.create_task(path, project="AIOS", title="t", task_type="implementation")
    session = db.create_session(path, task_id=task["id"], project="AIOS", repository_path="/tmp/x")
    r1 = db.create_run(
        path, session_id=session["id"], task_id=task["id"], project="AIOS", task_type="implementation",
        repository_path="/tmp/x", prompt="p1", is_resume=False,
    )
    r2 = db.create_run(
        path, session_id=session["id"], task_id=task["id"], project="AIOS", task_type="implementation",
        repository_path="/tmp/x", prompt="p2", is_resume=True,
    )
    assert r1["sequence"] == 1
    assert r2["sequence"] == 2
    runs = db.list_runs(path, session_id=session["id"])
    assert {r["id"] for r in runs} == {r1["id"], r2["id"]}


def test_report_is_at_most_one_per_run(tmp_path):
    path = _fresh_db(tmp_path)
    run = _make_run(path)
    db.create_report(path, run["id"], "reports/AIOS/x.md")
    with pytest.raises(sqlite3.IntegrityError):
        db.create_report(path, run["id"], "reports/AIOS/y.md")


def test_run_events_are_append_only_and_ordered(tmp_path):
    path = _fresh_db(tmp_path)
    run = _make_run(path)
    for i in range(5):
        db.append_run_event(path, run["id"], "lifecycle", {"i": i})
    events = db.list_run_events(path, run["id"])
    assert [e["payload"]["i"] for e in events] == [0, 1, 2, 3, 4]
    assert [e["seq"] for e in events] == [1, 2, 3, 4, 5]


def test_list_run_events_after_seq_returns_only_newer_events(tmp_path):
    path = _fresh_db(tmp_path)
    run = _make_run(path)
    for i in range(5):
        db.append_run_event(path, run["id"], "lifecycle", {"i": i})
    events = db.list_run_events(path, run["id"], after_seq=2)
    assert [e["payload"]["i"] for e in events] == [2, 3, 4]
