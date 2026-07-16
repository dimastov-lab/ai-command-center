"""Startup reconciliation: PID-reuse protection, conservative classification,
and never signaling a process based only on a reused pid.

These tests spawn real (throwaway `sleep`) processes to get real pids and
real `ps`-observable identities, then build `run` rows by hand (bypassing
`Supervisor.start`) to simulate exactly what a crashed/restarted Supervisor
would find on disk: a `RUNNING` row with a `pid` and (sometimes) a recorded
identity, but no in-memory `_ActiveRun` for it (a fresh `Supervisor()` never
launched it) — the same situation a real restart produces.
"""

from __future__ import annotations

import subprocess
import time

from command_center.runtime import db, identity, supervisor


def _make_running_row(db_path, *, pid, process_start_identity):
    task = db.create_task(db_path, project="AIOS", title="t", task_type="implementation")
    session = db.create_session(db_path, task_id=task["id"], project="AIOS", repository_path="/tmp/x")
    run = db.create_run(
        db_path, session_id=session["id"], task_id=task["id"], project="AIOS", task_type="implementation",
        repository_path="/tmp/x", prompt="p", is_resume=False,
    )
    run = db.update_run_state(db_path, run["id"], expected_version=run["version"], new_state="QUEUED")
    run = db.update_run_state(
        db_path, run["id"], expected_version=run["version"], new_state="RUNNING",
        fields={"pid": pid, "process_start_identity": process_start_identity, "started_at": "2026-01-01T00:00:00"},
    )
    return run


def test_reconcile_classifies_dead_pid_as_interrupted(tmp_path):
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)

    proc = subprocess.Popen(["true"])
    proc.wait()
    time.sleep(0.2)  # ensure `ps` no longer reports it

    run = _make_running_row(db_path, pid=proc.pid, process_start_identity="whatever|whatever")

    sup = supervisor.Supervisor(db_path)
    outcomes = sup.reconcile()

    assert len(outcomes) == 1
    assert outcomes[0]["classification"] == "INTERRUPTED"
    assert db.get_run(db_path, run["id"])["state"] == "INTERRUPTED"


def test_reconcile_classifies_missing_pid_as_interrupted(tmp_path):
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)
    run = _make_running_row(db_path, pid=None, process_start_identity=None)

    sup = supervisor.Supervisor(db_path)
    outcomes = sup.reconcile()

    assert outcomes[0]["classification"] == "INTERRUPTED"
    assert db.get_run(db_path, run["id"])["state"] == "INTERRUPTED"


def test_reconcile_classifies_alive_pid_without_recorded_identity_as_unknown(tmp_path):
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)

    proc = subprocess.Popen(["sleep", "3"])
    try:
        run = _make_running_row(db_path, pid=proc.pid, process_start_identity=None)
        sup = supervisor.Supervisor(db_path)
        outcomes = sup.reconcile()
        assert outcomes[0]["classification"] == "UNKNOWN"
        assert db.get_run(db_path, run["id"])["state"] == "UNKNOWN"
    finally:
        proc.terminate()
        proc.wait()


def test_reconcile_never_signals_a_process_matched_only_by_reused_pid(tmp_path):
    """A pid that currently belongs to a real, unrelated process (identity
    does not match what was recorded) must be classified INTERRUPTED — and,
    critically, that unrelated process must never be sent a signal."""
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)

    unrelated = subprocess.Popen(["sleep", "3"])
    try:
        run = _make_running_row(
            db_path, pid=unrelated.pid, process_start_identity="totally-bogus-recorded-identity"
        )
        sup = supervisor.Supervisor(db_path)
        outcomes = sup.reconcile()

        assert outcomes[0]["classification"] == "INTERRUPTED"
        assert db.get_run(db_path, run["id"])["state"] == "INTERRUPTED"
        # The unrelated process must still be alive — it was never signalled.
        assert unrelated.poll() is None
    finally:
        unrelated.terminate()
        unrelated.wait()


def test_reconcile_leaves_verified_still_running_process_as_running_but_flags_orphan(tmp_path):
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)

    proc = subprocess.Popen(["sleep", "3"])
    try:
        recorded_identity = identity.capture_identity(proc.pid).as_string()
        run = _make_running_row(db_path, pid=proc.pid, process_start_identity=recorded_identity)

        sup = supervisor.Supervisor(db_path)
        outcomes = sup.reconcile()

        assert outcomes[0]["classification"] == "RUNNING"
        assert db.get_run(db_path, run["id"])["state"] == "RUNNING"
        # Still alive: reconciliation must not have signalled it.
        assert proc.poll() is None
        # Not adopted into the new Supervisor's active-run registry (no pipe/wait handle for it).
        assert run["id"] not in sup.active_run_ids()

        events = db.list_run_events(db_path, run["id"])
        lifecycles = [e["payload"].get("lifecycle") for e in events if e["event_type"] == "lifecycle"]
        assert "reconciliation_orphaned" in lifecycles
    finally:
        proc.terminate()
        proc.wait()


def test_reconcile_does_not_guess_completion_for_any_ambiguous_case(tmp_path):
    """None of the reconciliation outcomes for a stale RUNNING row may ever be
    COMPLETED — that would be guessing a successful outcome for a run whose
    actual fate was never observed."""
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)

    dead = subprocess.Popen(["true"])
    dead.wait()
    time.sleep(0.2)
    _make_running_row(db_path, pid=dead.pid, process_start_identity=None)

    sup = supervisor.Supervisor(db_path)
    outcomes = sup.reconcile()
    for outcome in outcomes:
        assert outcome["classification"] != "COMPLETED"
        assert outcome["classification"] != "FAILED"


def test_reconcile_only_touches_running_rows(tmp_path):
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)
    task = db.create_task(db_path, project="AIOS", title="t", task_type="implementation")
    session = db.create_session(db_path, task_id=task["id"], project="AIOS", repository_path="/tmp/x")
    run = db.create_run(
        db_path, session_id=session["id"], task_id=task["id"], project="AIOS", task_type="implementation",
        repository_path="/tmp/x", prompt="p", is_resume=False,
    )
    run = db.update_run_state(db_path, run["id"], expected_version=run["version"], new_state="QUEUED")
    run = db.update_run_state(db_path, run["id"], expected_version=run["version"], new_state="RUNNING")
    run = db.update_run_state(db_path, run["id"], expected_version=run["version"], new_state="COMPLETED")

    sup = supervisor.Supervisor(db_path)
    outcomes = sup.reconcile()

    assert outcomes == []
    assert db.get_run(db_path, run["id"])["state"] == "COMPLETED"


def test_reconcile_is_idempotent_for_interrupted_rows(tmp_path):
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)
    dead = subprocess.Popen(["true"])
    dead.wait()
    time.sleep(0.2)
    run = _make_running_row(db_path, pid=dead.pid, process_start_identity=None)

    sup = supervisor.Supervisor(db_path)
    first = sup.reconcile()
    second = sup.reconcile()

    assert first[0]["classification"] == "INTERRUPTED"
    assert second == [], "an already-reconciled (now terminal) run must not be reconciled again"
    assert db.get_run(db_path, run["id"])["state"] == "INTERRUPTED"


def test_reconcile_does_not_use_claude_agents_registry(tmp_path, monkeypatch):
    """Reconciliation must never shell out to `claude agents --json` — the
    Supervisor's own SQLite `run` table is its lifecycle registry."""
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)

    def fail_if_called(*args, **kwargs):
        raise AssertionError(f"reconcile() must not invoke subprocess: {args!r}")

    monkeypatch.setattr(supervisor.subprocess, "Popen", fail_if_called)
    monkeypatch.setattr(supervisor.subprocess, "run", fail_if_called)

    sup = supervisor.Supervisor(db_path)
    outcomes = sup.reconcile()  # no RUNNING rows at all — must not touch subprocess either
    assert outcomes == []
