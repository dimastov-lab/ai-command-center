"""Integration tests for the executor-capability preflight across the launch
paths (v2 Session Supervisor + v1 synchronous orchestrator), persistence, and
the display projection.

These lock the AIOS-RECON-001 fix end to end: a write-required task can never
reach `subprocess.Popen` with a read-only tool set; the mismatch is persisted
and rendered as a distinct, blocking preflight outcome.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from command_center import agent_runner, capabilities, launch_service
from command_center.runtime import db, outcome, session_view, supervisor

WRITE_PROMPT = "Inspect git history, edit the files, add regression tests, run validation, and commit."
READ_PROMPT = "Review the module and summarize the findings."


def _boom_popen(*_args, **_kwargs):
    raise AssertionError("subprocess.Popen must not be called when preflight fails")


def _view(run: dict) -> dict:
    return session_view.build_session_view(
        run, kanban_task=None, project_cfg=None, latest_event=None, report_path=None, now=datetime.now()
    )


# --------------------------------------------------------------------------
# v2 Supervisor — the confirmed defect path.
# --------------------------------------------------------------------------


def test_write_required_read_only_task_is_blocked_before_spawn(git_repo, configure_project_repo, monkeypatch):
    configure_project_repo("AIOS", git_repo)
    monkeypatch.setattr(supervisor.subprocess, "Popen", _boom_popen)
    sup = supervisor.Supervisor()

    with pytest.raises(supervisor.CapabilityMismatchError) as excinfo:
        sup.start_raw(
            project="AIOS", repository_path=str(git_repo), task_type="review",
            prompt=WRITE_PROMPT, confirmed=True,
        )

    decision = excinfo.value.decision
    assert decision.missing_capabilities == ["Bash", "Edit", "Write"]

    runs = db.list_runs(sup.db_path)
    assert len(runs) == 1
    run = runs[0]
    assert run["state"] == "FAILED"  # persisted, visible — not a silent drop
    assert run["failure_reason"].startswith(capabilities.FAILURE_REASON_PREFIX)
    assert run["capability_profile"] == capabilities.PROFILE_READ_ONLY
    assert run["required_capabilities"] == "Read,Glob,Grep,Bash,Edit,Write"
    assert run["granted_capabilities"] == "Read,Glob,Grep"
    assert run["capability_preflight"] == "mismatch"
    assert run["command_policy"].startswith(capabilities.PROFILE_READ_ONLY)


def test_blocked_launch_persists_structured_events_and_reason(git_repo, configure_project_repo):
    # No Popen guard here: this test builds a session view (which runs a
    # read-only `git status`), and patching `subprocess.Popen` would patch the
    # shared module globally. A preflight mismatch never spawns regardless —
    # `test_write_required_read_only_task_is_blocked_before_spawn` asserts that.
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()

    with pytest.raises(supervisor.CapabilityMismatchError):
        sup.start_raw(
            project="AIOS", repository_path=str(git_repo), task_type="architecture_review",
            prompt=WRITE_PROMPT, confirmed=True,
        )
    run = db.list_runs(sup.db_path)[0]

    events = db.list_run_events(sup.db_path, run["id"], after_seq=0, limit=1000)
    assert any(e["event_type"] == "capability_preflight" for e in events)
    assert any(
        e["event_type"] == "lifecycle" and e["payload"].get("lifecycle") == "capability_preflight_failed"
        for e in events
    )

    # Display projection: distinct status + the exact user-facing sentence.
    assert session_view.derive_status(run) == session_view.STATUS_CAPABILITY_MISMATCH
    view = _view(run)
    assert view["status"] == session_view.STATUS_CAPABILITY_MISMATCH
    assert view["blocker_reason"] == (
        "Executor capability mismatch: task requires Bash/Edit/Write; "
        "configured session provides only Read/Glob/Grep."
    )
    assert view["capability_profile"] == capabilities.PROFILE_READ_ONLY


def test_blocked_launch_does_not_leave_workspace_locked(git_repo, configure_project_repo, monkeypatch):
    configure_project_repo("AIOS", git_repo)
    monkeypatch.setattr(supervisor.subprocess, "Popen", _boom_popen)
    sup = supervisor.Supervisor()
    with pytest.raises(supervisor.CapabilityMismatchError):
        sup.start_raw(
            project="AIOS", repository_path=str(git_repo), task_type="review",
            prompt=WRITE_PROMPT, confirmed=True,
        )
    # A FAILED run is terminal, so the workspace lock is released — no active run remains.
    assert db.list_runs(sup.db_path, states=db.EXECUTION_CENTER_ACTIVE_STATES) == []


def test_invalid_override_fails_closed_with_no_rows(git_repo, configure_project_repo, monkeypatch):
    configure_project_repo("AIOS", git_repo)
    monkeypatch.setattr(supervisor.subprocess, "Popen", _boom_popen)
    sup = supervisor.Supervisor()
    with pytest.raises(supervisor.InvalidCapabilityOverrideError):
        sup.start_raw(
            project="AIOS", repository_path=str(git_repo), task_type="review",
            prompt=READ_PROMPT, confirmed=True, capability_override="banana",
        )
    # Fails before any task/session/run row is created.
    assert db.list_runs(sup.db_path) == []
    assert db.list_tasks(sup.db_path) == []


def test_repository_validation_precedes_capability_preflight(git_repo, configure_project_repo, monkeypatch):
    """A capability mismatch must never bypass the workspace security boundary:
    an unconfigured path is rejected on the repository check first."""
    configure_project_repo("AIOS", git_repo)
    monkeypatch.setattr(supervisor.subprocess, "Popen", _boom_popen)
    sup = supervisor.Supervisor()
    with pytest.raises(agent_runner.RunnerError):
        sup.start_raw(
            project="AIOS", repository_path="/not/the/configured/path", task_type="review",
            prompt=WRITE_PROMPT, confirmed=True,
        )


def test_implementation_run_persists_ok_capability_metadata(git_repo, configure_project_repo, fake_claude):
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()
    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation",
        prompt="do the work", confirmed=True,
    )
    assert run["state"] == "RUNNING"
    assert run["capability_profile"] == capabilities.PROFILE_WORKSPACE_WRITE
    assert run["capability_preflight"] == "ok"
    assert "Bash" in run["granted_capabilities"]
    assert run["command_policy"].startswith(capabilities.PROFILE_WORKSPACE_WRITE)

    final = sup.wait_for_run(run["id"], timeout=10)
    assert final["state"] == "COMPLETED"


def test_read_only_override_on_write_task_launches_read_only(git_repo, configure_project_repo, fake_claude):
    """An explicit read-only override on a write-category task with a benign
    prompt is respected — it launches (spawns) with a read-only tool set."""
    configure_project_repo("AIOS", git_repo)
    sup = supervisor.Supervisor()
    run = sup.start_raw(
        project="AIOS", repository_path=str(git_repo), task_type="implementation",
        prompt="investigate the failing module", confirmed=True, capability_override="read_only",
    )
    assert run["state"] == "RUNNING"  # it actually spawned
    assert run["pid"] is not None
    assert run["capability_profile"] == capabilities.PROFILE_READ_ONLY
    assert run["capability_override"] == capabilities.PROFILE_READ_ONLY
    command = json.loads(run["command_json"])
    assert "--tools" in command and "--disallowedTools" not in command
    sup.wait_for_run(run["id"], timeout=10)


# --------------------------------------------------------------------------
# Command construction (v1 + v2) reflects the granted profile / override.
# --------------------------------------------------------------------------


def test_v2_command_read_only_override_uses_tool_replacement():
    command = supervisor.build_claude_command(
        session_id="s", prompt="do the work", task_type="implementation",
        is_resume=False, capability_override="read_only",
    )
    assert "--tools" in command
    assert "--disallowedTools" not in command


def test_v2_command_workspace_write_override_uses_denylist():
    command = supervisor.build_claude_command(
        session_id="s", prompt="x", task_type="review", is_resume=False, capability_override="workspace_write",
    )
    assert "--disallowedTools" in command
    assert "--tools" not in command


def test_v1_build_command_read_only_override_uses_tool_replacement():
    command = agent_runner.build_command("do the work", task_type="implementation", capability_override="read_only")
    assert "--tools" in command
    assert "--disallowedTools" not in command


def test_v1_build_command_no_override_matches_task_type_default():
    # No-override behavior is byte-for-byte the historical behavior.
    assert "--disallowedTools" in agent_runner.build_command("x", task_type="implementation")
    assert "--tools" in agent_runner.build_command("x", task_type="review")


# --------------------------------------------------------------------------
# v1 synchronous orchestrator preflight.
# --------------------------------------------------------------------------


def test_v1_execute_agent_launch_blocks_write_required_read_only(git_repo, monkeypatch):
    called = {"executor": False}

    def spy_run_claude_code(**_kwargs):
        called["executor"] = True
        raise AssertionError("executor must not run on capability mismatch")

    monkeypatch.setattr(agent_runner, "run_claude_code", spy_run_claude_code)

    with pytest.raises(launch_service.CapabilityMismatchError):
        launch_service.execute_agent_launch(
            project="AIOS", task_type="review", prompt=WRITE_PROMPT,
            timeout_seconds=30, repository_path=git_repo,
        )
    assert called["executor"] is False


def test_v1_execute_agent_launch_respects_read_only_task_override(git_repo, monkeypatch):
    # A read-only task with a benign prompt still passes preflight and reaches
    # the executor (which we stub) — the preflight only blocks genuine
    # mismatches, never legitimate read-only work.
    monkeypatch.setattr(
        agent_runner,
        "run_claude_code",
        lambda **_kwargs: agent_runner.RunResult(
            status="completed", exit_code=0, stdout="{}", stderr="",
            duration_seconds=0.0, started_at="t", completed_at="t",
        ),
    )
    outcome_obj = launch_service.execute_agent_launch(
        project="AIOS", task_type="review", prompt=READ_PROMPT,
        timeout_seconds=30, repository_path=git_repo,
    )
    assert outcome_obj.result_status == "completed"


# --------------------------------------------------------------------------
# Backward compatibility + status vocabulary distinctness.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "task_type,expected_profile",
    [
        ("implementation", capabilities.PROFILE_WORKSPACE_WRITE),
        ("review", capabilities.PROFILE_READ_ONLY),
        ("architecture_review", capabilities.PROFILE_READ_ONLY),
    ],
)
def test_legacy_run_without_capability_metadata_gets_deterministic_default(task_type, expected_profile):
    # A run row that predates the capability columns (all NULL) must project a
    # stable profile derived from its task_type, never crash.
    legacy_run = {"id": "legacy1", "state": "COMPLETED", "task_type": task_type, "repository_path": None}
    view = _view(legacy_run)
    assert view["capability_profile"] == expected_profile
    assert view["required_capabilities"] == []  # nothing persisted -> empty, not an error
    assert view["granted_capabilities"] == []


def test_capability_mismatch_status_is_distinct_from_blocked_and_failed():
    assert (
        session_view.derive_status({"state": "FAILED", "failure_reason": "capability_mismatch:Bash,Edit,Write"})
        == session_view.STATUS_CAPABILITY_MISMATCH
    )
    assert (
        session_view.derive_status({"state": "FAILED", "failure_reason": "blocked:permission_denied:Write"})
        == session_view.STATUS_BLOCKED
    )
    assert session_view.derive_status({"state": "FAILED", "failure_reason": "timeout"}) == session_view.STATUS_FAILED


def test_permission_denials_are_classified_blocked_and_rendered():
    """A Claude runtime permission denial (distinct from a pre-spawn capability
    mismatch) is classified BLOCKED and rendered with its denied tools."""
    classification, reason = outcome.classify_process_result(
        task_type="implementation", result_text="done",
        permission_denials=[{"tool_name": "Write"}, {"tool_name": "Edit"}],
        working_tree_changed=False,
    )
    assert classification == outcome.BLOCKED
    run = {"state": "FAILED", "failure_reason": f"{classification}:{reason}", "task_type": "implementation"}
    assert session_view.derive_status(run) == session_view.STATUS_BLOCKED
    view = _view(run)
    assert "permission_denied" in view["blocker_reason"]
    assert "Edit" in view["blocker_reason"] and "Write" in view["blocker_reason"]
