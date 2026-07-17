"""Unit tests for `command_center.workspace_home` — plain-pytest, Streamlit-free.

Every test isolates `workspace_home.GENERATED_DIR`/`REPORTS_DIR` into a fresh
`tmp_path`, since this module (unlike `agent_runner`/`runtime.reports`) owns
its own copies of these constants rather than reusing already-isolated ones —
see `tests/conftest.py`'s `isolated_reports_dir` docstring for why leaking
into the developer's real `reports/`/`generated/` directories is treated as
a real, previously-hit hazard in this codebase, not a theoretical one.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from command_center import agent_runner, models, project_config
from command_center import workspace_home
from command_center.runtime import db
from command_center.runtime.api import ExecutionCenterAPI


@pytest.fixture(autouse=True)
def isolated_workspace_home_dirs(tmp_path, monkeypatch):
    generated_dir = tmp_path / "generated"
    reports_dir = tmp_path / "reports"
    generated_dir.mkdir()
    reports_dir.mkdir()
    monkeypatch.setattr(workspace_home, "GENERATED_DIR", generated_dir)
    monkeypatch.setattr(workspace_home, "REPORTS_DIR", reports_dir)
    return generated_dir, reports_dir


class _MutationGuardAPI:
    """Wraps a real `ExecutionCenterAPI`, raising if a mutation method is
    called — proves `build_workspace_home_snapshot` never starts or cancels
    a run as a side effect of building a read-only snapshot."""

    def __init__(self, real_api):
        self._real = real_api

    def __getattr__(self, name):
        return getattr(self._real, name)

    def start_run(self, *args, **kwargs):
        raise AssertionError("start_run must not be called while building a snapshot")

    def request_cancel(self, *args, **kwargs):
        raise AssertionError("request_cancel must not be called while building a snapshot")

    def list_tasks(self, *args, **kwargs):
        raise AssertionError(
            "list_tasks must not be called while building a snapshot — the "
            "task_count metric it used to back was removed (B-1 remediation)"
        )


def _transition(api, run, *states):
    for state in states:
        run = db.update_run_state(api.db_path, run["id"], expected_version=run["version"], new_state=state)
    return run


def _make_v2_run(api, project="AIOS", task_type="implementation", run_id=None):
    task = db.create_task(api.db_path, project=project, title="t", task_type=task_type)
    session = db.create_session(api.db_path, task_id=task["id"], project=project, repository_path="/tmp/x")
    return db.create_run(
        api.db_path,
        session_id=session["id"],
        task_id=task["id"],
        project=project,
        task_type=task_type,
        repository_path="/tmp/x",
        prompt="a secret prompt that must never leak",
        is_resume=False,
        run_id=run_id,
    )


def _make_v1_2_run(project="AIOS", *, report_path=None, parsed=None, run_id=None):
    run = models.new_run_record(
        project=project,
        task_id=None,
        agent="claude_code",
        task_type="implementation",
        repository_path="/tmp/x",
        prompt="another secret prompt",
        timeout_seconds=900,
    )
    if run_id is not None:
        run["id"] = run_id
    run["status"] = "completed"
    run["stdout"] = "secret stdout content"
    run["stderr"] = "secret stderr content"
    run["report_path"] = report_path
    run["parsed"] = parsed
    return agent_runner.append_run(run)


def test_importing_workspace_home_does_not_import_streamlit():
    result = subprocess.run(
        [sys.executable, "-c", "import command_center.workspace_home, sys; assert 'streamlit' not in sys.modules"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_empty_state_all_projects_unconfigured(tmp_path):
    api = ExecutionCenterAPI(db_path=tmp_path / "runtime.db")
    snapshot = workspace_home.build_workspace_home_snapshot(execution_center_api=api)

    assert {p["id"] for p in snapshot["projects"]} == set(models.PROJECT_IDS)
    assert all(p["repository_path_configured"] is False for p in snapshot["projects"])
    assert all(
        snapshot["worktrees_by_project"][pid]["status"] == "unconfigured" for pid in models.PROJECT_IDS
    )
    assert snapshot["active_runs"] == []
    assert snapshot["recent_runs"] == []
    assert snapshot["artifacts"] == []
    assert snapshot["reports"] == []


def test_snapshot_never_constructs_its_own_api_or_mutates(tmp_path):
    api = ExecutionCenterAPI(db_path=tmp_path / "runtime.db")
    guarded = _MutationGuardAPI(api)
    snapshot = workspace_home.build_workspace_home_snapshot(execution_center_api=guarded)
    assert snapshot["projects"]


def test_project_snapshot_construction(tmp_path):
    api = ExecutionCenterAPI(db_path=tmp_path / "runtime.db")
    project_config.save_repository_path("AIOS", str(tmp_path))
    snapshot = workspace_home.build_workspace_home_snapshot(execution_center_api=api)

    aios = next(p for p in snapshot["projects"] if p["id"] == "AIOS")
    assert aios["repository_path_configured"] is True
    assert aios["repository_path"] == str(tmp_path)
    assert aios["sensitive"] is False

    bank = next(p for p in snapshot["projects"] if p["id"] == "BANK")
    assert bank["sensitive"] is True


def test_run_aggregation_active_vs_recent_and_source_tagging(tmp_path):
    api = ExecutionCenterAPI(db_path=tmp_path / "runtime.db")
    running = _make_v2_run(api, project="AIOS")
    running = _transition(api, running, "QUEUED", "RUNNING")
    completed = _make_v2_run(api, project="AIOS")
    completed = _transition(api, completed, "QUEUED", "RUNNING", "COMPLETED")
    _make_v1_2_run(project="AIOS")

    snapshot = workspace_home.build_workspace_home_snapshot(execution_center_api=api)

    active_ids = {(r["source"], r["run_id"]) for r in snapshot["active_runs"]}
    recent_ids = {(r["source"], r["run_id"]) for r in snapshot["recent_runs"]}
    assert active_ids == {("v2", running["id"])}
    assert ("v2", completed["id"]) in recent_ids
    assert any(source == "v1.2" for source, _ in recent_ids)


def test_run_aggregation_respects_limits(tmp_path):
    api = ExecutionCenterAPI(db_path=tmp_path / "runtime.db")
    for _ in range(5):
        run = _make_v2_run(api, project="AIOS")
        _transition(api, run, "QUEUED", "RUNNING")

    snapshot = workspace_home.build_workspace_home_snapshot(execution_center_api=api, active_runs_limit=2)
    assert len(snapshot["active_runs"]) == 2


def test_report_aggregation_v1_2_and_v2(tmp_path):
    reports_dir = tmp_path / "reports"
    api = ExecutionCenterAPI(db_path=tmp_path / "runtime.db")

    (reports_dir / "AIOS").mkdir(parents=True)
    v1_2_report_file = reports_dir / "AIOS" / "v1_report.md"
    v1_2_report_file.write_text("# Report\nVerdict: APPROVED FOR COMMIT\n")
    parsed = {"verdict": models.VERDICT_APPROVED_FOR_COMMIT, "findings": {s: [] for s in models.SEVERITIES}}
    _make_v1_2_run(project="AIOS", report_path="reports/AIOS/v1_report.md", parsed=parsed)

    v2_run = _make_v2_run(api, project="AIOS")
    v2_run = _transition(api, v2_run, "QUEUED", "RUNNING", "COMPLETED")
    v2_report_file = reports_dir / "AIOS" / "v2_report.md"
    v2_report_file.write_text("# Report\nNOT APPROVED FOR COMMIT\n- **Blocker:** something broke\n")
    db.create_report(api.db_path, v2_run["id"], "reports/AIOS/v2_report.md")

    snapshot = workspace_home.build_workspace_home_snapshot(execution_center_api=api)

    reports_by_source = {r["source"]: r for r in snapshot["reports"]}
    assert reports_by_source["v1.2"]["verdict"] == models.VERDICT_APPROVED_FOR_COMMIT
    assert reports_by_source["v2"]["verdict"] == models.VERDICT_NOT_APPROVED_FOR_COMMIT
    assert reports_by_source["v2"]["severity_counts"]["Blocker"] == 1
    assert "path" not in reports_by_source["v2"]


def test_artifact_aggregation(tmp_path):
    generated_dir = tmp_path / "generated"
    (generated_dir / "AIOS").mkdir(parents=True)
    (generated_dir / "AIOS" / "abc123_implementation.md").write_text("content")

    api = ExecutionCenterAPI(db_path=tmp_path / "runtime.db")
    snapshot = workspace_home.build_workspace_home_snapshot(execution_center_api=api)

    assert len(snapshot["artifacts"]) == 1
    artifact = snapshot["artifacts"][0]
    assert artifact["project"] == "AIOS"
    assert artifact["task_type"] == "implementation"
    assert artifact["path"].endswith("abc123_implementation.md")


def test_artifact_aggregation_redacts_path_for_sensitive_project(tmp_path):
    generated_dir = tmp_path / "generated"
    (generated_dir / "BANK").mkdir(parents=True)
    (generated_dir / "BANK" / "abc123_implementation.md").write_text("content")

    api = ExecutionCenterAPI(db_path=tmp_path / "runtime.db")
    snapshot = workspace_home.build_workspace_home_snapshot(execution_center_api=api)

    assert len(snapshot["artifacts"]) == 1
    artifact = snapshot["artifacts"][0]
    assert artifact["project"] == "BANK"
    assert "path" not in artifact
    assert "filename" not in artifact
    assert artifact["nav_target"] == {"nav": "generated", "project": "BANK"}


def test_activity_aggregation_includes_derived_run_rows(tmp_path):
    from command_center import activity_log

    activity_log.log_event("manual_field_correction", project="AIOS", message="corrected a field")

    api = ExecutionCenterAPI(db_path=tmp_path / "runtime.db")
    run = _make_v2_run(api, project="AIOS")
    run = db.update_run_state(api.db_path, run["id"], expected_version=run["version"], new_state="QUEUED")
    run = db.update_run_state(
        api.db_path, run["id"], expected_version=run["version"], new_state="RUNNING",
        fields={"started_at": models.iso_now()},
    )
    db.update_run_state(
        api.db_path, run["id"], expected_version=run["version"], new_state="COMPLETED",
        fields={"completed_at": models.iso_now()},
    )

    snapshot = workspace_home.build_workspace_home_snapshot(execution_center_api=api)
    types = {e.get("type") for e in snapshot["recent_activity"]}
    assert "manual_field_correction" in types
    assert "run_started" in types
    assert "run_terminal" in types


def test_git_snapshot_unconfigured(tmp_path):
    api = ExecutionCenterAPI(db_path=tmp_path / "runtime.db")
    snapshot = workspace_home.build_workspace_home_snapshot(execution_center_api=api)
    assert snapshot["worktrees_by_project"]["AIOS"] == {"status": "unconfigured", "worktrees": []}


def test_git_snapshot_invalid_path(tmp_path):
    project_config.save_repository_path("AIOS", str(tmp_path / "does-not-exist"))
    api = ExecutionCenterAPI(db_path=tmp_path / "runtime.db")
    snapshot = workspace_home.build_workspace_home_snapshot(execution_center_api=api)
    assert snapshot["worktrees_by_project"]["AIOS"]["status"] == "invalid_path"


def test_git_snapshot_not_a_git_repository(tmp_path):
    not_a_repo = tmp_path / "plain_dir"
    not_a_repo.mkdir()
    project_config.save_repository_path("AIOS", str(not_a_repo))
    api = ExecutionCenterAPI(db_path=tmp_path / "runtime.db")
    snapshot = workspace_home.build_workspace_home_snapshot(execution_center_api=api)
    assert snapshot["worktrees_by_project"]["AIOS"]["status"] == "not_a_git_repository"


def test_git_snapshot_valid_repo(git_repo, tmp_path):
    project_config.save_repository_path("AIOS", str(git_repo))
    api = ExecutionCenterAPI(db_path=tmp_path / "runtime.db")
    snapshot = workspace_home.build_workspace_home_snapshot(execution_center_api=api)
    entry = snapshot["worktrees_by_project"]["AIOS"]
    assert entry["status"] == "ok"
    assert len(entry["worktrees"]) == 1


# --------------------------------------------------------------------------
# `sanitize_workspace_project_entry` — direct unit tests (§14)
# --------------------------------------------------------------------------


def test_sanitize_identity_transform_for_non_sensitive_project(tmp_path):
    run = {"run_id": "r1", "source": "v2", "project": "AIOS", "prompt": "not actually banned here"}
    report = {"run_id": "r1", "source": "v2", "project": "AIOS", "verdict": "APPROVED_FOR_COMMIT"}
    activity = {"project": "AIOS", "type": "run_started", "ts": "2026-01-01T00:00:00", "message": "hello"}
    path = tmp_path / "task_implementation.md"
    path.write_text("x")

    for project_id in ("AIOS", "AICOS", "BUSINESS", "PERSONAL"):
        result = workspace_home.sanitize_workspace_project_entry(
            project_id, runs=[run], reports=[report], artifacts=[path], activity=[activity]
        )
        assert result["runs"] == [run]
        assert result["reports"] == [report]
        assert result["activity"] == [activity]
        assert result["artifacts"][0]["path"] == str(path)


def test_sanitize_strips_banned_fields_for_sensitive_project(tmp_path):
    run = {
        "run_id": "r1",
        "source": "v2",
        "project": "BANK",
        "state": "COMPLETED",
        "prompt": "SECRET PROMPT",
        "command_json": '["claude", "--dangerous"]',
        "stdout": "secret stdout",
        "stderr": "secret stderr",
        "repository_path": "/Users/someone/secret-repo",
        "exit_code": 0,
    }
    report = {
        "run_id": "r1",
        "source": "v2",
        "project": "BANK",
        "verdict": "APPROVED_FOR_COMMIT",
        "severity_counts": {"Blocker": 0, "High": 0, "Medium": 0, "Low": 0},
        "created_at": "2026-01-01T00:00:00",
        "report_body": "full sensitive report text",
        "path": "reports/BANK/secret.md",
    }
    activity = {
        "project": "BANK",
        "type": "run_completed",
        "event_type": "run_completed",
        "ts": "2026-01-01T00:00:00",
        "run_id": "r1",
        "message": "full sensitive activity message",
    }
    path = tmp_path / "task_implementation.md"
    path.write_text("x")

    for project_id in ("BANK", "LEGAL"):
        result = workspace_home.sanitize_workspace_project_entry(
            project_id, runs=[run], reports=[report], artifacts=[path], activity=[activity]
        )
        sanitized_run = result["runs"][0]
        for banned in ("prompt", "command_json", "stdout", "stderr", "repository_path"):
            assert banned not in sanitized_run
        assert sanitized_run["run_id"] == "r1"
        assert sanitized_run["state"] == "COMPLETED"
        assert sanitized_run["exit_code"] == 0

        sanitized_report = result["reports"][0]
        assert "report_body" not in sanitized_report
        assert "path" not in sanitized_report
        assert sanitized_report["verdict"] == "APPROVED_FOR_COMMIT"

        sanitized_artifact = result["artifacts"][0]
        assert "path" not in sanitized_artifact
        assert "filename" not in sanitized_artifact

        sanitized_activity = result["activity"][0]
        assert "message" not in sanitized_activity
        assert sanitized_activity["run_id"] == "r1"


def test_snapshot_never_contains_banned_fields_for_sensitive_project(tmp_path):
    generated_dir, reports_dir = tmp_path / "generated", tmp_path / "reports"
    (generated_dir / "BANK").mkdir(parents=True)
    (reports_dir / "BANK").mkdir(parents=True)
    (generated_dir / "BANK" / "abc_implementation.md").write_text("x")
    report_file = reports_dir / "BANK" / "report.md"
    report_file.write_text("NOT APPROVED FOR COMMIT\n- **Blocker:** leak risk\n")

    api = ExecutionCenterAPI(db_path=tmp_path / "runtime.db")
    bank_run = _make_v2_run(api, project="BANK")
    bank_run = _transition(api, bank_run, "QUEUED", "RUNNING", "COMPLETED")
    db.create_report(api.db_path, bank_run["id"], "reports/BANK/report.md")

    aios_run = _make_v2_run(api, project="AIOS")
    _transition(api, aios_run, "QUEUED", "RUNNING", "COMPLETED")

    snapshot = workspace_home.build_workspace_home_snapshot(execution_center_api=api)

    banned_substrings = ("secret", "a secret prompt that must never leak")
    for section in ("active_runs", "recent_runs", "reports", "artifacts", "recent_activity"):
        for entry in snapshot[section]:
            if entry.get("project") != "BANK":
                continue
            assert "prompt" not in entry
            assert "repository_path" not in entry
            assert "path" not in entry
            for value in entry.values():
                if isinstance(value, str):
                    for banned in banned_substrings:
                        assert banned not in value

    # A non-sensitive project's entries in the same snapshot retain full fields.
    aios_runs = [r for r in snapshot["recent_runs"] if r["project"] == "AIOS"]
    assert aios_runs and "prompt" in aios_runs[0]


def test_merged_run_identity_uses_source_and_run_id(tmp_path):
    shared_id = "a" * 32
    api = ExecutionCenterAPI(db_path=tmp_path / "runtime.db")
    v2_run = _make_v2_run(api, project="AIOS", run_id=shared_id)
    _transition(api, v2_run, "QUEUED", "RUNNING", "COMPLETED")
    _make_v1_2_run(project="AIOS", run_id=shared_id)

    snapshot = workspace_home.build_workspace_home_snapshot(execution_center_api=api)
    matching = [r for r in snapshot["recent_runs"] if r["run_id"] == shared_id]
    assert len(matching) == 2
    assert {r["source"] for r in matching} == {"v1.2", "v2"}


def test_cross_project_rollup(tmp_path):
    api = ExecutionCenterAPI(db_path=tmp_path / "runtime.db")
    for project in ("AIOS", "BANK", "BUSINESS"):
        run = _make_v2_run(api, project=project)
        _transition(api, run, "QUEUED", "RUNNING", "COMPLETED")

    snapshot = workspace_home.build_workspace_home_snapshot(execution_center_api=api)
    projects_represented = {r["project"] for r in snapshot["recent_runs"]}
    assert projects_represented == {"AIOS", "BANK", "BUSINESS"}


def test_unavailable_data_source_handling_does_not_raise(tmp_path):
    project_config.save_repository_path("AIOS", str(tmp_path / "moved-away"))
    project_config.save_repository_path("BANK", str(tmp_path))
    api = ExecutionCenterAPI(db_path=tmp_path / "runtime.db")
    snapshot = workspace_home.build_workspace_home_snapshot(execution_center_api=api)
    assert snapshot["worktrees_by_project"]["AIOS"]["status"] == "invalid_path"
    assert snapshot["worktrees_by_project"]["BANK"]["status"] == "not_a_git_repository"
    assert snapshot["worktrees_by_project"]["LEGAL"]["status"] == "unconfigured"


def test_deterministic_output(tmp_path):
    api = ExecutionCenterAPI(db_path=tmp_path / "runtime.db")
    run = _make_v2_run(api, project="AIOS")
    _transition(api, run, "QUEUED", "RUNNING", "COMPLETED")

    first = workspace_home.build_workspace_home_snapshot(execution_center_api=api)
    second = workspace_home.build_workspace_home_snapshot(execution_center_api=api)
    assert first == second


# --------------------------------------------------------------------------
# B-1 remediation — the misleading `task_count` metric is fully removed.
# --------------------------------------------------------------------------


def test_project_entries_no_longer_expose_task_count(tmp_path):
    api = ExecutionCenterAPI(db_path=tmp_path / "runtime.db")
    snapshot = workspace_home.build_workspace_home_snapshot(execution_center_api=api)
    for project in snapshot["projects"]:
        assert "task_count" not in project


def test_list_tasks_is_never_called_while_building_the_snapshot(tmp_path):
    """Regression for B-1: `list_tasks` was only ever called to back the
    removed `task_count` metric — `_MutationGuardAPI.list_tasks` raises if
    it is still called anywhere in the build path."""
    api = ExecutionCenterAPI(db_path=tmp_path / "runtime.db")
    guarded = _MutationGuardAPI(api)
    snapshot = workspace_home.build_workspace_home_snapshot(execution_center_api=guarded)
    assert snapshot["projects"]


def test_other_sections_render_correctly_without_task_count(tmp_path):
    """Removing `task_count` must not disturb any other project-entry field
    or any other section of the snapshot."""
    api = ExecutionCenterAPI(db_path=tmp_path / "runtime.db")
    project_config.save_repository_path("AIOS", str(tmp_path))
    run = _make_v2_run(api, project="AIOS")
    _transition(api, run, "QUEUED", "RUNNING")

    snapshot = workspace_home.build_workspace_home_snapshot(execution_center_api=api)
    aios = next(p for p in snapshot["projects"] if p["id"] == "AIOS")
    assert set(aios) == {
        "id",
        "display_name",
        "sensitive",
        "repository_path",
        "repository_path_configured",
        "active_run_count",
    }
    assert aios["active_run_count"] == 1
    assert len(snapshot["active_runs"]) == 1


# --------------------------------------------------------------------------
# B-2 remediation — a malformed `repository_path` degrades only that
# project instead of crashing the whole snapshot build.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "malformed_value",
    [12345, ["a", "list"], {"an": "object"}, None, "", "   "],
    ids=["int", "list", "dict", "none", "empty_string", "whitespace_only"],
)
def test_malformed_repository_path_degrades_to_unconfigured(tmp_path, malformed_value):
    config_path = Path(os.environ["AICC_DATA_DIR"]) / "project_config.json"
    config_path.write_text(json.dumps({"AIOS": {"repository_path": malformed_value}}), encoding="utf-8")

    api = ExecutionCenterAPI(db_path=tmp_path / "runtime.db")
    snapshot = workspace_home.build_workspace_home_snapshot(execution_center_api=api)

    assert snapshot["worktrees_by_project"]["AIOS"] == {"status": "unconfigured", "worktrees": []}
    aios = next(p for p in snapshot["projects"] if p["id"] == "AIOS")
    assert aios["repository_path_configured"] is False


def test_malformed_project_does_not_prevent_other_projects_from_processing(tmp_path, git_repo):
    data_dir = Path(os.environ["AICC_DATA_DIR"])
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "project_config.json").write_text(
        json.dumps({"AIOS": {"repository_path": 12345}, "BANK": {"repository_path": str(git_repo)}}),
        encoding="utf-8",
    )

    api = ExecutionCenterAPI(db_path=tmp_path / "runtime.db")
    snapshot = workspace_home.build_workspace_home_snapshot(execution_center_api=api)

    assert snapshot["worktrees_by_project"]["AIOS"] == {"status": "unconfigured", "worktrees": []}
    bank_entry = snapshot["worktrees_by_project"]["BANK"]
    assert bank_entry["status"] == "ok"
    assert len(bank_entry["worktrees"]) == 1
    # The rest of the snapshot must still be well-formed.
    assert {p["id"] for p in snapshot["projects"]} == set(models.PROJECT_IDS)


def test_malformed_repository_path_never_reaches_git_helpers(tmp_path, monkeypatch):
    from command_center import git_info

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("git_info.run_git_command must not be called for a malformed repository_path")

    monkeypatch.setattr(git_info, "run_git_command", _fail_if_called)

    data_dir = Path(os.environ["AICC_DATA_DIR"])
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "project_config.json").write_text(
        json.dumps({"AIOS": {"repository_path": 12345}}), encoding="utf-8"
    )

    api = ExecutionCenterAPI(db_path=tmp_path / "runtime.db")
    snapshot = workspace_home.build_workspace_home_snapshot(execution_center_api=api)
    assert snapshot["worktrees_by_project"]["AIOS"]["status"] == "unconfigured"
