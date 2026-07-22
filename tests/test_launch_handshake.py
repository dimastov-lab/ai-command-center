"""Regression suite for AICC-LAUNCH-HANDSHAKE-001 — reliable Claude launch
handshake and timeout semantics.

The confirmed live defect: a `claude` process was spawned successfully (valid
PID, correct isolated workspace, correct expected branch) and remained alive,
yet AI Command Center reported it as timeout / Requires Attention / Failed. In
other words a *launcher/response-level* delay (no early stdout yet) was being
treated as a *launch failure*.

These tests pin the separation the fix guarantees:

    1. process spawn confirmation   (a valid PID -> persisted immediately)
    2. Claude startup / handshake   (first output -> `first_output_at`)
    3. running state                (RUNNING, handshaked)
    4. inactivity / execution timeout (a real deadline, kills + FAILED)
    5. process completion           (terminal classification on exit facts)

and the display-mapping guarantee: a spawned, still-alive run that has not yet
produced early output is `Starting` (a warning), never `Failed`.

All tests use throwaway git repos, an isolated `runtime.db` (via the
autouse `isolated_data_dir` fixture), and the `fake_claude` subprocess double —
never a real project, real `tasks.json`, or the real `claude` CLI.
"""

from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime

import pytest

from command_center.runtime import db, identity, session_view, supervisor, task_sync


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _lifecycles(db_path, run_id) -> list[str]:
    events = db.list_run_events(db_path, run_id)
    return [e["payload"].get("lifecycle") for e in events if e["event_type"] == "lifecycle"]


def _make_running_row(db_path, *, pid, process_start_identity, repository_path="/tmp/x"):
    """A hand-built RUNNING run row — the on-disk shape a crashed/restarted
    Supervisor would find (a pid, maybe an identity, but no in-memory
    `_ActiveRun`)."""
    task = db.create_task(db_path, project="AIOS", title="t", task_type="implementation")
    session = db.create_session(
        db_path, task_id=task["id"], project="AIOS", repository_path=repository_path
    )
    run = db.create_run(
        db_path, session_id=session["id"], task_id=task["id"], project="AIOS",
        task_type="implementation", repository_path=repository_path, prompt="p", is_resume=False,
    )
    run = db.update_run_state(db_path, run["id"], expected_version=run["version"], new_state="QUEUED")
    run = db.update_run_state(
        db_path, run["id"], expected_version=run["version"], new_state="RUNNING",
        fields={"pid": pid, "process_start_identity": process_start_identity, "started_at": "2026-01-01T00:00:00"},
    )
    return run


# --------------------------------------------------------------------------
# 1. Spawn succeeds, no early stdout, process remains alive -> Starting, alive,
#    never Failed.
# --------------------------------------------------------------------------


def test_spawn_succeeds_with_no_early_stdout_stays_alive_and_starting(git_repo, configure_project_repo, fake_claude):
    fake_claude["FAKE_CLAUDE_LINES"] = json.dumps([])  # emits nothing
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "5"  # but stays alive
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()
    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="p", confirmed=True
    )
    try:
        assert run["state"] == "RUNNING"
        assert run["pid"] is not None

        fresh = db.get_run(sup.db_path, run["id"])
        assert fresh["state"] == "RUNNING", "a spawned, silent process must stay RUNNING, never flip to FAILED"
        assert fresh["first_output_at"] is None, "no output yet -> handshake not recorded"
        assert identity.process_exists(fresh["pid"]) is True, "the real process must still be alive"

        assert session_view.is_awaiting_handshake(fresh) is True
        session = session_view.build_session_view(
            fresh, kanban_task=None, project_cfg=None, latest_event=None, report_path=None,
            now=datetime.now(),
        )
        assert session["status"] == session_view.STATUS_STARTING
        assert session["status"] != session_view.STATUS_FAILED
    finally:
        sup.cancel(run["id"], confirmed=True, grace_seconds=2)


# --------------------------------------------------------------------------
# 2. Spawn succeeds and handshake arrives late -> first_output_at is set once
#    output finally arrives, and the run completes normally.
# --------------------------------------------------------------------------


def test_handshake_arrives_late_is_recorded_and_run_completes(git_repo, configure_project_repo, fake_claude):
    fake_claude["FAKE_CLAUDE_INITIAL_DELAY"] = "0.6"  # spawned, but silent for 0.6s
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()
    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="p", confirmed=True
    )

    # Immediately after launch, before the delayed first line, we are in the
    # awaiting-handshake window: a valid PID, but no output yet.
    right_after = db.get_run(sup.db_path, run["id"])
    assert right_after["state"] == "RUNNING"
    assert right_after["first_output_at"] is None
    assert session_view.is_awaiting_handshake(right_after) is True

    final = sup.wait_for_run(run["id"], timeout=10)
    assert final["state"] == "COMPLETED"
    assert final["first_output_at"] is not None, "the late handshake must be recorded once output arrives"
    assert "handshake_received" in _lifecycles(sup.db_path, run["id"])
    assert session_view.is_awaiting_handshake(final) is False


# --------------------------------------------------------------------------
# 3. Spawn fails before a PID exists -> FAILED (a genuine start failure), and
#    it is display-distinct from Starting.
# --------------------------------------------------------------------------


def test_spawn_failure_before_pid_is_failed_not_starting(git_repo, configure_project_repo, monkeypatch):
    configure_project_repo("AIOS", git_repo)

    def raise_oserror(*args, **kwargs):
        raise OSError("claude binary not found")

    monkeypatch.setattr(supervisor.subprocess, "Popen", raise_oserror)

    sup = supervisor.Supervisor()
    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="p", confirmed=True
    )
    assert run["state"] == "FAILED"
    assert run["pid"] is None, "a pre-spawn failure never records a PID"
    assert run["first_output_at"] is None
    assert "launch_failed" in _lifecycles(sup.db_path, run["id"])
    assert session_view.derive_status(run) == session_view.STATUS_FAILED
    assert session_view.derive_status(run) != session_view.STATUS_STARTING


# --------------------------------------------------------------------------
# 4. A request/UI timeout after spawn must not mark the task Failed. Modeled at
#    the sync layer: a spawned RUNNING run syncs to an active (non-terminal)
#    launch status, never a terminal one, and terminal finalization never runs.
# --------------------------------------------------------------------------


def test_request_timeout_after_spawn_does_not_mark_task_failed(tmp_path):
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)
    run = _make_running_row(db_path, pid=424242, process_start_identity="id|cmd")
    # A spawned RUNNING run that has not yet produced output.
    db.update_run_fields(db_path, run["id"], expected_version=run["version"], fields={"first_output_at": None})

    task = {"id": run["task_id"], "current_run_id": run["id"], "launch_status": "Launching", "progress": 5}
    mutated = task_sync.sync_task_from_run(task, db.get_run(db_path, run["id"]), db_path=db_path)

    assert mutated is True
    assert task["launch_status"] == "Running", "a live spawned run is active, never Failed"
    assert task["launch_status"] not in task_sync._TERMINAL_LAUNCH_STATUSES
    # No terminal finalization: progress must not be nudged, no completed/report fields written.
    assert task["progress"] == 5
    assert "report_path" not in task


# --------------------------------------------------------------------------
# 5. Duplicate launch while the PID is alive is rejected (idempotent) — exactly
#    one active run for the workspace.
# --------------------------------------------------------------------------


def test_duplicate_launch_while_pid_alive_is_rejected(git_repo, configure_project_repo, fake_claude):
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "5"
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()
    first = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="p1", confirmed=True
    )
    try:
        assert identity.process_exists(first["pid"]) is True
        with pytest.raises(supervisor.WorkspaceLockedError) as excinfo:
            sup.start_raw(
                project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="p2", confirmed=True
            )
        assert excinfo.value.conflicting_run["id"] == first["id"]
        active = db.list_runs(sup.db_path, states=db.EXECUTION_CENTER_ACTIVE_STATES)
        assert [r["id"] for r in active] == [first["id"]], "the rejected launch must not create a second active run"
    finally:
        sup.cancel(first["id"], confirmed=True, grace_seconds=2)


# --------------------------------------------------------------------------
# 6. PID and run metadata are persisted atomically — the instant start_raw
#    returns RUNNING, a fresh DB read already has every spawn fact.
# --------------------------------------------------------------------------


def test_pid_and_run_metadata_persisted_immediately_after_spawn(git_repo, configure_project_repo, fake_claude):
    fake_claude["FAKE_CLAUDE_EXTRA_SLEEP"] = "3"
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()
    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="p", confirmed=True,
        expected_branch="feature/x", launch_source="kanban_task",
    )
    try:
        # A *fresh read* (not the returned dict) proves it was committed, not just returned.
        fresh = db.get_run(sup.db_path, run["id"])
        assert fresh["state"] == "RUNNING"
        assert fresh["pid"] is not None
        assert fresh["process_start_identity"], "process identity captured and persisted with the PID"
        assert fresh["started_at"] is not None
        assert fresh["repository_path"] == str(git_repo)
        assert fresh["expected_branch"] == "feature/x"
        assert fresh["launch_source"] == "kanban_task"
        assert fresh["command_json"], "the exact argv is persisted for audit"
        assert "process_started" in _lifecycles(sup.db_path, run["id"])
    finally:
        sup.cancel(run["id"], confirmed=True, grace_seconds=2)


# --------------------------------------------------------------------------
# 7. Process exits during STARTING (before ever handshaking) -> still reaches a
#    correct terminal state, no crash, first_output_at stays None.
# --------------------------------------------------------------------------


def test_process_exits_during_starting_without_output_still_terminalizes(
    git_repo, configure_project_repo, fake_claude
):
    fake_claude["FAKE_CLAUDE_LINES"] = json.dumps([])  # never emits output
    fake_claude["FAKE_CLAUDE_TOUCH_FILE"] = ""  # and touches nothing
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()
    # `review` is read-only: a clean exit with no output is a genuine COMPLETED,
    # not `Incomplete` (which only applies to change-requiring task types).
    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="review", prompt="look", confirmed=True
    )
    final = sup.wait_for_run(run["id"], timeout=10)

    assert final["state"] == "COMPLETED", "a never-handshaked run must still reach a proper terminal state"
    assert final["first_output_at"] is None, "no output was ever produced -> no handshake recorded"
    assert "handshake_received" not in _lifecycles(sup.db_path, run["id"])
    assert "process_exited" in _lifecycles(sup.db_path, run["id"])


# --------------------------------------------------------------------------
# 8. Malformed stream-json does not erase process state — the PID, identity, and
#    state survive; the malformed line is preserved; handshake still fires.
# --------------------------------------------------------------------------


def test_malformed_stream_json_does_not_erase_process_state(git_repo, configure_project_repo, fake_claude):
    lines = [
        "THIS IS NOT JSON {{{",
        json.dumps({"type": "result", "result": "done anyway"}),
    ]
    fake_claude["FAKE_CLAUDE_LINES"] = json.dumps(lines)
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()
    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation", prompt="p", confirmed=True
    )
    pid = run["pid"]
    identity_at_launch = run["process_start_identity"]

    final = sup.wait_for_run(run["id"], timeout=10)

    assert final["state"] in db.TERMINAL_STATES, "a malformed line must not crash the supervisor or wipe the run"
    assert final["pid"] == pid, "the recorded PID must survive a malformed line"
    assert final["process_start_identity"] == identity_at_launch, "the recorded identity must survive too"
    # A malformed line is still *output* — proof the process is alive and talking.
    assert final["first_output_at"] is not None, "any output (even malformed) records the handshake"

    events = db.list_run_events(sup.db_path, run["id"])
    malformed = [e for e in events if e["event_type"] == "malformed"]
    assert len(malformed) == 1
    assert "NOT JSON" in malformed[0]["payload"]["raw"]


# --------------------------------------------------------------------------
# 9. Restart reloads a confirmed active run — a fresh Supervisor reconciling a
#    genuinely-alive orphaned process leaves it RUNNING, never Failed/Interrupted.
# --------------------------------------------------------------------------


def test_restart_reloads_confirmed_active_run_as_running(tmp_path):
    db_path = tmp_path / "runtime.db"
    db.migrate(db_path)

    # A real, long-lived process so both `kill(pid,0)` and its `ps` identity
    # are genuinely observable — exactly what a restarted Supervisor confronts.
    proc = subprocess.Popen(["sleep", "30"])
    try:
        time.sleep(0.2)
        current = identity.capture_identity(proc.pid)
        assert current is not None
        run = _make_running_row(db_path, pid=proc.pid, process_start_identity=current.as_string())

        # A brand-new Supervisor instance (the "restarted app") never launched this run.
        sup = supervisor.Supervisor(db_path)
        outcomes = sup.reconcile()

        assert len(outcomes) == 1
        assert outcomes[0]["classification"] == "RUNNING", "a provably-alive orphan must stay RUNNING"
        reloaded = db.get_run(db_path, run["id"])
        assert reloaded["state"] == "RUNNING", "restart must not misclassify a confirmed-live run as Failed/Interrupted"
        assert session_view.derive_status(reloaded) != session_view.STATUS_FAILED
        assert "reconciliation_orphaned" in _lifecycles(db_path, run["id"])
    finally:
        proc.terminate()
        proc.wait()


# --------------------------------------------------------------------------
# 10. UI status mapping distinguishes warning from failure.
# --------------------------------------------------------------------------


def test_ui_status_mapping_distinguishes_warning_from_failure():
    # Spawned-but-silent -> Starting (warning), never Failed.
    starting = {"state": "RUNNING", "first_output_at": None}
    assert session_view.derive_status(starting, awaiting_handshake=True) == session_view.STATUS_STARTING

    # Spawned, probe momentarily old -> Stale (warning), never Failed.
    stale = {"state": "RUNNING", "first_output_at": "2026-01-01T00:00:00"}
    assert session_view.derive_status(stale, heartbeat_stale=True) == session_view.STATUS_STALE

    # A genuine start failure -> Failed.
    failed = {"state": "FAILED", "failure_reason": None}
    assert session_view.derive_status(failed) == session_view.STATUS_FAILED

    # Warnings are live (a process is up / was just up) and non-terminal;
    # failure is terminal. They must never collapse into each other.
    assert session_view.STATUS_STARTING in session_view.LIVE_PROCESS_DISPLAY_STATUSES
    assert session_view.STATUS_STALE in session_view.LIVE_PROCESS_DISPLAY_STATUSES
    assert session_view.STATUS_FAILED not in session_view.LIVE_PROCESS_DISPLAY_STATUSES
    assert session_view.STATUS_FAILED in session_view.TERMINAL_DISPLAY_STATUSES
    for warning in (session_view.STATUS_STARTING, session_view.STATUS_STALE):
        assert warning != session_view.STATUS_FAILED
        assert warning not in session_view.TERMINAL_DISPLAY_STATUSES
