"""Streamlit AppTest coverage: page renders, the task→runner confirmation flow, and
the completed-run → parsed-result / Create Next Task flows.

Every test here either never touches `subprocess.run` at all, or explicitly asserts it
is *not* called (the unconfigured-repository refusal path), or replaces it with a fake
that only intercepts calls to the `claude` binary and forwards everything else (git
snapshot calls) to the real `subprocess.run` — a real, but short and local, `git init`
in a throwaway tmp_path repo. No test here launches a real Claude Code job or makes a
network/billable call.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from streamlit.testing.v1 import AppTest

from command_center import agent_runner, models, project_config, report_parser, storage

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


def _at_on_page(page_key: str, **extra_session_state) -> AppTest:
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.session_state["nav_page"] = page_key
    for key, value in extra_session_state.items():
        at.session_state[key] = value
    at.run()
    return at


def _seed_task(**overrides) -> dict:
    data_dir = Path(os.environ["AICC_DATA_DIR"])
    task = {
        "id": "seeded-task-1",
        "project": "AIOS",
        "title": "Seeded task for AppTest",
        "task_type": "implementation",
        "status": "Backlog",
        "priority": "Medium",
        "owner": "",
        "estimate_hours": 0.0,
        "depends_on": [],
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }
    task.update(models.default_task_workflow_fields())
    task.update(overrides)
    storage.atomic_write_json(data_dir / "tasks.json", [task])
    return task


def _seed_run(**overrides) -> dict:
    run = models.new_run_record(
        project="AIOS", task_id=None, agent="claude_code", task_type="review",
        repository_path="/tmp/nonexistent-repo", prompt="review it", timeout_seconds=60,
    )
    run["status"] = "completed"
    run["exit_code"] = 0
    run["parsed"] = report_parser.empty_parsed_result()
    run.update(overrides)
    agent_runner.append_run(run)
    return run


# --------------------------------------------------------------------------
# Basic renders
# --------------------------------------------------------------------------


def test_dashboard_renders_without_exception():
    at = _at_on_page("dashboard")
    assert not at.exception


def test_project_chat_page_renders():
    at = _at_on_page("chat")
    assert not at.exception
    assert at.subheader[0].value == "Чат по проекту"


def test_runs_page_renders_empty_state():
    at = _at_on_page("runs")
    assert not at.exception
    assert at.subheader[0].value == "Журнал запусков"


def test_executive_dashboard_shows_run_metrics_section():
    at = _at_on_page("executive")
    assert not at.exception
    metric_labels = [m.label for m in at.metric]
    assert "Запусков сегодня" in metric_labels
    assert "Одобрено для commit" in metric_labels


# --------------------------------------------------------------------------
# Sensitive-project warning behavior
# --------------------------------------------------------------------------


def test_sensitive_project_chat_shows_warning():
    at = _at_on_page("chat", chat_project_select="BANK")
    assert not at.exception
    warnings = [w.value for w in at.warning]
    assert any("чувствительный" in w for w in warnings)


def test_non_sensitive_project_chat_shows_no_sensitivity_warning():
    at = _at_on_page("chat", chat_project_select="AIOS")
    assert not at.exception
    warnings = [w.value for w in at.warning]
    assert not any("чувствительный" in w for w in warnings)


# --------------------------------------------------------------------------
# Refusal to run against unconfigured paths (task → runner confirmation flow)
# --------------------------------------------------------------------------


def test_kanban_launcher_present_but_never_calls_subprocess_on_render(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("subprocess.run must not be called merely by rendering the page")

    monkeypatch.setattr(agent_runner.subprocess, "run", fail_if_called)

    _seed_task()
    at = _at_on_page("kanban")
    assert not at.exception
    assert any(b.label == "Запустить Claude Code" for b in at.button)


def test_kanban_launcher_refuses_unconfigured_repository(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("subprocess.run must not be called when the repository is unconfigured")

    monkeypatch.setattr(agent_runner.subprocess, "run", fail_if_called)

    _seed_task()
    at = _at_on_page("kanban")
    assert not at.exception

    open_button = next(b for b in at.button if b.label == "Запустить Claude Code")
    at = open_button.click().run()
    assert not at.exception

    errors = [e.value for e in at.error]
    assert any("не настроен" in message for message in errors)


def test_kanban_launcher_blocking_validation_error_cannot_be_bypassed(monkeypatch, tmp_path):
    """`disabled=` on the launch button is the primary gate, but
    `streamlit.testing.v1.AppTest.click()` does not itself respect
    `disabled` (it drives the widget's simulated state directly) — so this
    test forces the click a real disabled button in a browser could never
    receive, to prove the server-side `validation.can_launch` re-check
    (not just the widget attribute) is what actually stops the launch."""

    real_run = subprocess.run

    def fail_if_claude_launched(command, **kwargs):
        # `subprocess` is a single shared module object, so this also
        # intercepts `git_info`'s legitimate (and expected) read-only status
        # calls for the Task Card's git badge — only the `claude` launch
        # itself must be refused.
        if command and command[0] == "claude":
            raise AssertionError("claude must not be launched when launch validation blocks the launch")
        return real_run(command, **kwargs)

    monkeypatch.setattr(agent_runner.subprocess, "run", fail_if_claude_launched)

    repo = tmp_path / "aios-real-repo"
    repo.mkdir()
    project_config.save_repository_path("AIOS", str(repo))

    _seed_task(workspace_path=str(tmp_path / "does-not-exist"))
    at = _at_on_page("kanban")
    assert not at.exception

    at = at.button(key="kanban_seeded-task-1_launch_open_btn").click().run()
    assert not at.exception
    assert any("не найден" in e.value for e in at.error)

    at = at.checkbox(key="kanban_seeded-task-1_launch_confirmed").check().run()
    assert not at.exception

    launch_button = at.button(key="kanban_seeded-task-1_launch_launch_btn")
    assert launch_button.disabled is True  # confirms the UI-level gate is also engaged

    at = launch_button.click().run()
    assert not at.exception
    assert agent_runner.load_runs() == []  # the forced click must not have launched anything
    assert any("заблокирован" in e.value for e in at.error)


# --------------------------------------------------------------------------
# Full confirm → run → parse flow, with subprocess mocked (never a real Claude job)
# --------------------------------------------------------------------------


def test_full_launch_flow_records_run_and_parses_verdict(monkeypatch, tmp_path):
    repo = tmp_path / "aios-fake-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "f.txt").write_text("hello")
    subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)

    project_config.save_repository_path("AIOS", str(repo))
    real_run = subprocess.run

    def fake_run(command, **kwargs):
        if command and command[0] == "claude":
            payload = json.dumps([{"type": "result", "result": "Verdict: APPROVED FOR COMMIT"}])
            return subprocess.CompletedProcess(command, 0, stdout=payload, stderr="")
        return real_run(command, **kwargs)

    monkeypatch.setattr(agent_runner.subprocess, "run", fake_run)

    _seed_task()
    at = _at_on_page("kanban")
    assert not at.exception

    at = at.button(key="kanban_seeded-task-1_launch_open_btn").click().run()
    assert not at.exception

    at = at.checkbox(key="kanban_seeded-task-1_launch_confirmed").check().run()
    assert not at.exception

    at = at.button(key="kanban_seeded-task-1_launch_launch_btn").click().run()
    assert not at.exception

    runs = agent_runner.load_runs()
    assert len(runs) == 1
    assert runs[0]["status"] == "completed"
    assert runs[0]["parsed"]["verdict"] == "APPROVED_FOR_COMMIT"
    assert runs[0]["repository_path"] == str(repo.resolve())


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    (path / "f.txt").write_text("hello")
    subprocess.run(["git", "add", "f.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


def test_launcher_launches_claude_against_task_workspace_not_project_repository(monkeypatch, tmp_path):
    """Regression test for the Launch path-resolution bug: a task's own
    `workspace_path` (a separate worktree on its own feature branch) must be
    what Claude Code actually runs against, and what gets persisted in the
    run record and the task's `launch_history` — never silently replaced by
    the project's configured `repository_path`, even though both are valid,
    clean git repositories."""
    project_repo = tmp_path / "aios"
    project_repo.mkdir()
    _init_repo(project_repo)

    task_workspace = tmp_path / "aios-p1-deployment"
    task_workspace.mkdir()
    _init_repo(task_workspace)
    subprocess.run(["git", "checkout", "-q", "-b", "feature/p1-7-deployment"], cwd=task_workspace, check=True)

    project_config.save_repository_path("AIOS", str(project_repo))
    real_run = subprocess.run

    def fake_run(command, **kwargs):
        if command and command[0] == "claude":
            payload = json.dumps([{"type": "result", "result": "Verdict: APPROVED FOR COMMIT"}])
            return subprocess.CompletedProcess(command, 0, stdout=payload, stderr="")
        return real_run(command, **kwargs)

    monkeypatch.setattr(agent_runner.subprocess, "run", fake_run)

    _seed_task(workspace_path=str(task_workspace), branch="feature/p1-7-deployment")
    at = _at_on_page("kanban")
    assert not at.exception

    at = at.button(key="kanban_seeded-task-1_launch_open_btn").click().run()
    assert not at.exception
    # The confirmation panel must show the selected workspace and its
    # source, not just the project repository.
    body_text = " ".join(w.value for w in at.markdown)
    assert str(task_workspace) in body_text

    at = at.checkbox(key="kanban_seeded-task-1_launch_confirmed").check().run()
    assert not at.exception

    at = at.button(key="kanban_seeded-task-1_launch_launch_btn").click().run()
    assert not at.exception

    runs = agent_runner.load_runs()
    assert len(runs) == 1
    assert runs[0]["status"] == "completed"
    assert runs[0]["repository_path"] == str(task_workspace.resolve())
    assert runs[0]["repository_path"] != str(project_repo.resolve())

    tasks_on_disk = json.loads((Path(os.environ["AICC_DATA_DIR"]) / "tasks.json").read_text())
    saved_task = next(t for t in tasks_on_disk if t["id"] == "seeded-task-1")
    assert saved_task["launch_history"][-1]["workspace_path"] == str(task_workspace.resolve())
    assert saved_task["launch_history"][-1]["branch"] == "feature/p1-7-deployment"


def test_launcher_workspace_action_buttons_use_task_workspace_not_project_repository(monkeypatch, tmp_path):
    """Terminal/Folder actions in the confirmation panel must act on the
    same selected workspace as the launch itself, not the project repo."""
    project_repo = tmp_path / "aios"
    project_repo.mkdir()
    _init_repo(project_repo)

    task_workspace = tmp_path / "aios-p1-deployment"
    task_workspace.mkdir()
    _init_repo(task_workspace)

    project_config.save_repository_path("AIOS", str(project_repo))

    from command_center import launch as launch_module

    captured: dict[str, str] = {}

    def fake_open_folder_at(path):
        captured["folder_path"] = str(path)
        return True, "ok"

    def fake_open_terminal_at(path):
        captured["terminal_path"] = str(path)
        return True, "ok"

    monkeypatch.setattr(launch_module, "open_folder_at", fake_open_folder_at)
    monkeypatch.setattr(launch_module, "open_terminal_at", fake_open_terminal_at)

    _seed_task(workspace_path=str(task_workspace))
    at = _at_on_page("kanban")
    at = at.button(key="kanban_seeded-task-1_launch_open_btn").click().run()
    assert not at.exception

    at = at.button(key="kanban_seeded-task-1_launch_open_folder").click().run()
    assert not at.exception
    assert captured["folder_path"] == str(task_workspace)

    at = at.button(key="kanban_seeded-task-1_launch_open_terminal").click().run()
    assert not at.exception
    assert captured["terminal_path"] == str(task_workspace)


def test_launcher_missing_task_workspace_falls_back_to_project_default_workspace(monkeypatch, tmp_path):
    """When a task has no `workspace_path` but the project defines a
    `default_workspace_path`, the launcher must select that default —
    never silently jump straight to `repository_path`."""
    project_repo = tmp_path / "aios"
    project_repo.mkdir()
    _init_repo(project_repo)

    project_default_workspace = tmp_path / "aios-default-workspace"
    project_default_workspace.mkdir()
    _init_repo(project_default_workspace)

    project_config.save_repository_path("AIOS", str(project_repo))
    project_config.storage.atomic_write_json(
        project_config.CONFIG_FILE,
        {"AIOS": {"repository_path": str(project_repo), "default_workspace_path": str(project_default_workspace)}},
    )

    _seed_task()  # no workspace_path override — task relies on project fallback
    at = _at_on_page("kanban")
    at = at.button(key="kanban_seeded-task-1_launch_open_btn").click().run()
    assert not at.exception

    body_text = " ".join(w.value for w in at.markdown)
    assert str(project_default_workspace) in body_text


# --------------------------------------------------------------------------
# Completed run → parsed result / Create Next Task (Runs page)
# --------------------------------------------------------------------------


def test_runs_page_shows_seeded_completed_run_verdict():
    _seed_run(project="AIOS")
    at = _at_on_page("runs")
    assert not at.exception
    markdown_text = " ".join(md.value for md in at.markdown)
    assert "AIOS" in markdown_text
    assert "Завершено" in markdown_text


def test_completed_run_create_next_task_button_present_for_not_approved_verdict():
    parsed = report_parser.empty_parsed_result()
    parsed["verdict"] = models.VERDICT_NOT_APPROVED_FOR_COMMIT
    run = _seed_run(project="AIOS", parsed=parsed)

    at = _at_on_page("runs")
    assert not at.exception
    assert any(b.key == f"runs_page_{run['id']}_create_next_btn" for b in at.button)
    caption_text = " ".join(c.value for c in at.caption)
    assert "NOT_APPROVED_FOR_COMMIT" in caption_text


def test_create_next_task_button_creates_backlog_task():
    parsed = report_parser.empty_parsed_result()
    parsed["verdict"] = models.VERDICT_NOT_APPROVED_FOR_COMMIT
    run = _seed_run(project="AIOS", parsed=parsed)

    at = _at_on_page("runs")
    assert not at.exception

    create_button = next(b for b in at.button if b.key == f"runs_page_{run['id']}_create_next_btn")
    at = create_button.click().run()
    assert not at.exception

    tasks = storage.read_json(Path(os.environ["AICC_DATA_DIR"]) / "tasks.json", [])
    assert len(tasks) == 1
    assert tasks[0]["parent_task_id"] is None
    assert tasks[0]["prior_run_id"] == run["id"]
    assert tasks[0]["task_type"] == "remediation"
    assert tasks[0]["status"] == "Backlog"


# --------------------------------------------------------------------------
# Run-journal filtering
# --------------------------------------------------------------------------


def test_runs_page_project_filter_narrows_results():
    _seed_run(project="AIOS")
    run_bank = models.new_run_record(
        project="BANK", task_id=None, agent="claude_code", task_type="review",
        repository_path="/tmp/other", prompt="p", timeout_seconds=60,
    )
    run_bank["status"] = "completed"
    run_bank["parsed"] = report_parser.empty_parsed_result()
    agent_runner.append_run(run_bank)

    at = _at_on_page("runs", runs_project_filter="BANK")
    assert not at.exception
    body = " ".join(md.value for md in at.markdown)
    assert "BANK" in body
    assert "AIOS" not in body
