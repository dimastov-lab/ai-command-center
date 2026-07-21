from datetime import datetime, timedelta

from command_center.runtime import session_view


# --------------------------------------------------------------------------
# derive_status — every RUN_STATES x cancel_requested combination
# --------------------------------------------------------------------------


def test_derive_status_prepared_and_queued_are_launching():
    assert session_view.derive_status({"state": "PREPARED"}) == session_view.STATUS_LAUNCHING
    assert session_view.derive_status({"state": "QUEUED"}) == session_view.STATUS_LAUNCHING


def test_derive_status_running_without_cancel_is_running():
    assert session_view.derive_status({"state": "RUNNING", "cancel_requested": 0}) == session_view.STATUS_RUNNING
    assert session_view.derive_status({"state": "RUNNING"}) == session_view.STATUS_RUNNING


def test_derive_status_running_with_cancel_requested_is_waiting():
    assert session_view.derive_status({"state": "RUNNING", "cancel_requested": 1}) == session_view.STATUS_WAITING


def test_derive_status_terminal_states():
    assert session_view.derive_status({"state": "COMPLETED"}) == session_view.STATUS_COMPLETED
    assert session_view.derive_status({"state": "FAILED"}) == session_view.STATUS_FAILED
    assert session_view.derive_status({"state": "CANCELLED"}) == session_view.STATUS_CANCELLED


def test_derive_status_failed_with_blocked_reason_is_blocked():
    run = {"state": "FAILED", "failure_reason": "blocked:permission_denied:Write"}
    assert session_view.derive_status(run) == session_view.STATUS_BLOCKED


def test_derive_status_failed_with_incomplete_reason_is_incomplete():
    run = {"state": "FAILED", "failure_reason": "incomplete:working_tree_unchanged"}
    assert session_view.derive_status(run) == session_view.STATUS_INCOMPLETE


def test_derive_status_failed_with_other_reason_stays_failed():
    assert session_view.derive_status({"state": "FAILED", "failure_reason": "timeout"}) == session_view.STATUS_FAILED
    assert session_view.derive_status({"state": "FAILED", "failure_reason": None}) == session_view.STATUS_FAILED


def test_blocked_and_incomplete_are_terminal_display_statuses():
    assert session_view.STATUS_BLOCKED in session_view.TERMINAL_DISPLAY_STATUSES
    assert session_view.STATUS_INCOMPLETE in session_view.TERMINAL_DISPLAY_STATUSES


def test_derive_status_interrupted_and_unknown_are_requires_attention():
    assert session_view.derive_status({"state": "INTERRUPTED"}) == session_view.STATUS_REQUIRES_ATTENTION
    assert session_view.derive_status({"state": "UNKNOWN"}) == session_view.STATUS_REQUIRES_ATTENTION


def test_derive_status_unrecognized_state_is_conservatively_requires_attention():
    assert session_view.derive_status({"state": "SOMETHING_NEW"}) == session_view.STATUS_REQUIRES_ATTENTION
    assert session_view.derive_status({}) == session_view.STATUS_REQUIRES_ATTENTION


# --------------------------------------------------------------------------
# format_elapsed / elapsed_seconds
# --------------------------------------------------------------------------


def test_format_elapsed_none_is_dash():
    assert session_view.format_elapsed(None) == "—"


def test_format_elapsed_negative_is_dash():
    assert session_view.format_elapsed(-1.0) == "—"


def test_format_elapsed_formats_hh_mm_ss():
    assert session_view.format_elapsed(0) == "00:00:00"
    assert session_view.format_elapsed(65) == "00:01:05"
    assert session_view.format_elapsed(3661) == "01:01:01"
    assert session_view.format_elapsed(1112) == "00:18:32"


def test_elapsed_seconds_none_when_not_started():
    now = datetime(2026, 1, 1, 12, 0, 0)
    assert session_view.elapsed_seconds(None, None, now) is None


def test_elapsed_seconds_uses_now_when_not_finished():
    started = "2026-01-01T11:59:00"
    now = datetime(2026, 1, 1, 12, 0, 0)
    assert session_view.elapsed_seconds(started, None, now) == 60.0


def test_elapsed_seconds_uses_finished_at_when_present():
    started = "2026-01-01T11:00:00"
    finished = "2026-01-01T11:05:00"
    now = datetime(2026, 1, 1, 12, 0, 0)
    assert session_view.elapsed_seconds(started, finished, now) == 300.0


# --------------------------------------------------------------------------
# build_session_view — field mapping, missing-task/workspace graceful None
# --------------------------------------------------------------------------


def _run(**overrides) -> dict:
    base = {
        "id": "run-1",
        "session_id": "session-1",
        "task_id": "task-1",
        "project": "AIOS",
        "state": "RUNNING",
        "repository_path": None,
        "expected_branch": "feature/x",
        "launch_source": "kanban_task",
        "pid": 12345,
        "started_at": "2026-01-01T00:00:00",
        "completed_at": None,
        "exit_code": None,
        "failure_reason": None,
        "prompt_version": 2,
        "prompt": "do the thing",
        "commit_hash": None,
        "pull_request_url": None,
        "cancel_requested": 0,
    }
    base.update(overrides)
    return base


def test_build_session_view_maps_every_field_with_no_task_or_workspace():
    now = datetime(2026, 1, 1, 0, 30, 0)
    run = _run()
    session = session_view.build_session_view(
        run, kanban_task=None, project_cfg=None, latest_event=None, report_path=None, now=now
    )
    assert session["session_id"] == "session-1"
    assert session["run_id"] == "run-1"
    assert session["task_id"] == "task-1"
    assert session["project_id"] == "AIOS"
    assert session["executor"] == "claude_code"
    assert session["workspace_path"] is None
    assert session["repository_path"] is None
    assert session["expected_branch"] == "feature/x"
    assert session["actual_branch"] is None  # no workspace_path -> no live git status
    assert session["launch_source"] == "kanban_task"
    assert session["process_id"] == 12345
    assert session["status"] == session_view.STATUS_RUNNING
    assert session["current_stage"] is None
    assert session["progress"] is None
    assert session["prompt_version"] == 2
    assert session["prompt"] == "do the thing"
    assert session["elapsed_seconds"] == 1800.0


def test_build_session_view_uses_kanban_task_title_stage_and_progress():
    now = datetime(2026, 1, 1, 0, 0, 0)
    run = _run()
    task = {"title": "Deploy P1", "current_stage": "Implementation", "progress": 40, "executor": "claude_code"}
    session = session_view.build_session_view(
        run, kanban_task=task, project_cfg=None, latest_event=None, report_path="reports/AIOS/x.md", now=now
    )
    assert session["task_title"] == "Deploy P1"
    assert session["current_stage"] == "Implementation"
    assert session["progress"] == 40
    assert session["report_path"] == "reports/AIOS/x.md"


def test_build_session_view_falls_back_to_prompt_or_id_for_title_when_no_task():
    now = datetime(2026, 1, 1, 0, 0, 0)
    run = _run(prompt="A short prompt")
    session = session_view.build_session_view(
        run, kanban_task=None, project_cfg=None, latest_event=None, report_path=None, now=now
    )
    assert session["task_title"] == "A short prompt"


def test_build_session_view_repository_path_prefers_project_cfg_over_workspace():
    now = datetime(2026, 1, 1, 0, 0, 0)
    run = _run(repository_path="/some/workspace")
    session = session_view.build_session_view(
        run, kanban_task=None, project_cfg={"repository_path": "/canonical/repo"}, latest_event=None,
        report_path=None, now=now,
    )
    assert session["repository_path"] == "/canonical/repo"
    assert session["workspace_path"] == "/some/workspace"


def test_build_session_view_blocker_reason_populated_for_blocked_status():
    now = datetime(2026, 1, 1, 0, 0, 0)
    run = _run(state="FAILED", failure_reason="blocked:permission_denied:Write")
    session = session_view.build_session_view(
        run, kanban_task=None, project_cfg=None, latest_event=None, report_path=None, now=now
    )
    assert session["status"] == session_view.STATUS_BLOCKED
    assert session["blocker_reason"] == "blocked:permission_denied:Write"


def test_build_session_view_blocker_reason_none_for_ordinary_failed():
    now = datetime(2026, 1, 1, 0, 0, 0)
    run = _run(state="FAILED", failure_reason="timeout")
    session = session_view.build_session_view(
        run, kanban_task=None, project_cfg=None, latest_event=None, report_path=None, now=now
    )
    assert session["status"] == session_view.STATUS_FAILED
    assert session["blocker_reason"] is None


def test_build_session_view_last_error_uses_failure_reason_first():
    now = datetime(2026, 1, 1, 0, 0, 0)
    run = _run(state="FAILED", failure_reason="timeout")
    session = session_view.build_session_view(
        run, kanban_task=None, project_cfg=None, latest_event=None, report_path=None, now=now
    )
    assert session["last_error"] == "timeout"


def test_build_session_view_last_error_falls_back_to_stderr_event_when_failed_without_reason():
    now = datetime(2026, 1, 1, 0, 0, 0)
    run = _run(state="FAILED", failure_reason=None)
    latest_event = {"event_type": "stderr_line", "payload": {"line": "Traceback: boom"}, "created_at": "x"}
    session = session_view.build_session_view(
        run, kanban_task=None, project_cfg=None, latest_event=latest_event, report_path=None, now=now
    )
    assert session["last_error"] == "Traceback: boom"


def test_build_session_view_latest_event_summarizes_lifecycle_and_result_events():
    now = datetime(2026, 1, 1, 0, 0, 0)
    run = _run()
    lifecycle_event = {"event_type": "lifecycle", "payload": {"lifecycle": "process_started"}, "created_at": "t1"}
    session = session_view.build_session_view(
        run, kanban_task=None, project_cfg=None, latest_event=lifecycle_event, report_path=None, now=now
    )
    assert session["latest_event"] == {"summary": "process_started", "at": "t1"}


def test_build_session_view_no_latest_event_is_none():
    now = datetime(2026, 1, 1, 0, 0, 0)
    run = _run()
    session = session_view.build_session_view(
        run, kanban_task=None, project_cfg=None, latest_event=None, report_path=None, now=now
    )
    assert session["latest_event"] is None


def test_build_session_view_workspace_path_missing_directory_gives_none_actual_branch(tmp_path):
    now = datetime(2026, 1, 1, 0, 0, 0)
    missing = tmp_path / "does-not-exist"
    run = _run(repository_path=str(missing))
    session = session_view.build_session_view(
        run, kanban_task=None, project_cfg=None, latest_event=None, report_path=None, now=now
    )
    assert session["actual_branch"] is None
    assert session["git_status"] is None


# --------------------------------------------------------------------------
# Heartbeat — derived liveness probe, never fabricated
# --------------------------------------------------------------------------


def test_heartbeat_age_seconds_none_when_never_probed():
    now = datetime(2026, 1, 1, 0, 0, 30)
    assert session_view.heartbeat_age_seconds(None, now) is None


def test_heartbeat_age_seconds_computes_elapsed_since_last_probe():
    probe_at = datetime(2026, 1, 1, 0, 0, 0)
    now = datetime(2026, 1, 1, 0, 0, 3)
    assert session_view.heartbeat_age_seconds(probe_at, now) == 3.0


def test_is_heartbeat_stale_true_when_never_probed():
    now = datetime(2026, 1, 1, 0, 0, 0)
    assert session_view.is_heartbeat_stale(None, now) is True


def test_is_heartbeat_stale_false_when_probe_is_fresh():
    probe_at = datetime(2026, 1, 1, 0, 0, 0)
    now = probe_at + timedelta(seconds=5)
    assert session_view.is_heartbeat_stale(probe_at, now, threshold_seconds=90) is False


def test_is_heartbeat_stale_true_when_probe_exceeds_threshold():
    probe_at = datetime(2026, 1, 1, 0, 0, 0)
    now = probe_at + timedelta(seconds=200)
    assert session_view.is_heartbeat_stale(probe_at, now, threshold_seconds=90) is True
