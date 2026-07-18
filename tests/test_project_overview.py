from datetime import datetime

from command_center.runtime import project_overview, session_view


def _session(**overrides) -> dict:
    base = {
        "run_id": "run-1",
        "project_id": "AIOS",
        "status": session_view.STATUS_RUNNING,
        "executor": "claude_code",
        "workspace_path": "/workspace/a",
        "actual_branch": "main",
        "started_at": "2026-01-01T00:00:00",
        "finished_at": None,
    }
    base.update(overrides)
    return base


def test_build_project_overview_counts_running_and_waiting():
    now = datetime(2026, 1, 1, 1, 0, 0)
    sessions = [
        _session(run_id="r1", status=session_view.STATUS_RUNNING),
        _session(run_id="r2", status=session_view.STATUS_RUNNING),
        _session(run_id="r3", status=session_view.STATUS_WAITING),
        _session(run_id="r4", status=session_view.STATUS_REQUIRES_ATTENTION),
    ]
    overview = project_overview.build_project_overview("AIOS", sessions=sessions, project_cfg=None, now=now)
    assert overview["running_count"] == 2
    assert overview["waiting_count"] == 2  # Waiting + Requires Attention


def test_build_project_overview_completed_today_only_counts_todays_completions():
    now = datetime(2026, 1, 2, 12, 0, 0)
    sessions = [
        _session(run_id="r1", status=session_view.STATUS_COMPLETED, finished_at="2026-01-02T09:00:00"),
        _session(run_id="r2", status=session_view.STATUS_COMPLETED, finished_at="2026-01-01T09:00:00"),
        _session(run_id="r3", status=session_view.STATUS_COMPLETED, finished_at=None),
    ]
    overview = project_overview.build_project_overview("AIOS", sessions=sessions, project_cfg=None, now=now)
    assert overview["completed_today_count"] == 1


def test_build_project_overview_current_fields_from_most_recent_active_session():
    now = datetime(2026, 1, 1, 1, 0, 0)
    sessions = [
        _session(
            run_id="r1", status=session_view.STATUS_RUNNING, executor="claude_code",
            workspace_path="/workspace/old", actual_branch="old-branch", started_at="2026-01-01T00:00:00",
        ),
        _session(
            run_id="r2", status=session_view.STATUS_RUNNING, executor="claude_code",
            workspace_path="/workspace/new", actual_branch="new-branch", started_at="2026-01-01T00:30:00",
        ),
    ]
    overview = project_overview.build_project_overview("AIOS", sessions=sessions, project_cfg=None, now=now)
    assert overview["current_workspace"] == "/workspace/new"
    assert overview["current_branch"] == "new-branch"
    assert overview["current_executor"] == "claude_code"


def test_build_project_overview_falls_back_to_project_defaults_when_no_active_session():
    now = datetime(2026, 1, 1, 1, 0, 0)
    sessions = [_session(status=session_view.STATUS_COMPLETED, finished_at="2026-01-01T00:00:00")]
    cfg = {"default_executor": "claude_code", "default_workspace_path": "/default/ws", "default_branch": "main"}
    overview = project_overview.build_project_overview("AIOS", sessions=sessions, project_cfg=cfg, now=now)
    assert overview["current_executor"] == "claude_code"
    assert overview["current_workspace"] == "/default/ws"
    assert overview["current_branch"] == "main"


def test_build_project_overview_health_ok_when_nothing_wrong():
    now = datetime(2026, 1, 1, 1, 0, 0)
    sessions = [_session(status=session_view.STATUS_RUNNING)]
    overview = project_overview.build_project_overview("AIOS", sessions=sessions, project_cfg=None, now=now)
    assert overview["health"] == project_overview.HEALTH_OK


def test_build_project_overview_health_attention_on_waiting_session():
    now = datetime(2026, 1, 1, 1, 0, 0)
    sessions = [_session(status=session_view.STATUS_WAITING)]
    overview = project_overview.build_project_overview("AIOS", sessions=sessions, project_cfg=None, now=now)
    assert overview["health"] == project_overview.HEALTH_ATTENTION


def test_build_project_overview_health_attention_on_stale_heartbeat():
    now = datetime(2026, 1, 1, 1, 0, 0)
    sessions = [_session(run_id="stale-run", status=session_view.STATUS_RUNNING)]
    overview = project_overview.build_project_overview(
        "AIOS", sessions=sessions, project_cfg=None, now=now, stale_run_ids=frozenset({"stale-run"})
    )
    assert overview["health"] == project_overview.HEALTH_ATTENTION


def test_build_project_overview_health_degraded_on_failed_session():
    now = datetime(2026, 1, 1, 1, 0, 0)
    sessions = [_session(status=session_view.STATUS_FAILED)]
    overview = project_overview.build_project_overview("AIOS", sessions=sessions, project_cfg=None, now=now)
    assert overview["health"] == project_overview.HEALTH_DEGRADED


def test_build_project_overview_health_degraded_wins_over_waiting():
    now = datetime(2026, 1, 1, 1, 0, 0)
    sessions = [
        _session(run_id="r1", status=session_view.STATUS_WAITING),
        _session(run_id="r2", status=session_view.STATUS_REQUIRES_ATTENTION),
    ]
    overview = project_overview.build_project_overview("AIOS", sessions=sessions, project_cfg=None, now=now)
    assert overview["health"] == project_overview.HEALTH_DEGRADED
