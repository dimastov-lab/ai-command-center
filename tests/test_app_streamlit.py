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

import inspect
import json
import os
import subprocess
from pathlib import Path

from streamlit.testing.v1 import AppTest

from command_center import agent_runner, execution_queue, models, project_config, report_parser, storage, task_view, workspace_home
from command_center.runtime import db as runtime_db
from command_center.runtime import reports as runtime_reports
from command_center.ui import project_selector

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


def _wait_for_run_terminal(db_path, run_id: str, *, timeout: float = 10.0) -> dict:
    """Poll `runtime.db` directly until `run_id` reaches a terminal state
    *and* — for the states that produce one (`COMPLETED`/`FAILED`/
    `CANCELLED`) — its report row exists too, not just `state` (set partway
    through `Supervisor._supervise`, before report-saving, which is the
    background thread's last write before it exits). Waiting for state alone
    leaves that thread still running past this test's teardown, racing the
    next test's fixtures (e.g. `isolated_reports_dir` reverting `REPORTS_ROOT`
    out from under it) — the same hazard `tests/test_execution_center_ui.py`'s
    `_wait_for_report` guards against.

    Deliberately does not go through any `Supervisor`/`ExecutionCenterAPI`
    instance: `st.cache_resource` (used by `app.get_execution_center_api()`)
    only reliably resolves to the *same* cached singleton when called from
    inside a live Streamlit `ScriptRunContext` — calling it from a test's own
    thread constructs an unrelated second `Supervisor` with an empty
    `_active` registry, whose own `reconcile()` would then race the real
    one still finishing this exact run (see the `Supervisor.reconcile()`
    docstring on why an actively-supervised run is skipped, which only
    protects the *correct* instance). Reading `runtime.db` directly sidesteps
    that hazard entirely."""
    import time

    report_producing_states = {"COMPLETED", "FAILED", "CANCELLED"}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = runtime_db.get_run(db_path, run_id)
        if run is not None and run["state"] in runtime_db.TERMINAL_STATES:
            if run["state"] not in report_producing_states or runtime_db.get_report(db_path, run_id) is not None:
                return run
        time.sleep(0.05)
    raise AssertionError(f"run {run_id!r} did not reach a settled terminal state within {timeout}s")


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
    assert any(b.label == "Запустить агента" for b in at.button)


def test_kanban_launcher_confirmation_renders_as_dialog_not_inline_in_narrow_lane(monkeypatch, tmp_path):
    """P1 layout regression test. The Kanban board renders one narrow
    `st.columns(len(KANBAN_COLUMNS))` lane per status, and each task card's
    "Запустить агента" button used to expand its confirmation form
    (workspace metadata, a 3-column workspace-action row, a 2-column
    confirm/cancel row) *inline* into that single narrow lane — collapsing
    it into a barely-readable, word-wrapped sliver with the rest of the page
    left empty. The form must instead open as an `st.dialog`, which
    Streamlit always gives its own full-width top-level surface, never a
    descendant of `at.main`'s tree — asserted here by checking the
    confirmation checkbox is queryable across the whole app (the dialog is
    open) but is *not* a descendant of `at.main` (the narrow Kanban lane)."""

    real_run = subprocess.run

    def fail_if_claude_launched(command, **kwargs):
        # `subprocess` is a single shared module object, so this also
        # intercepts `git_info`'s legitimate (and expected) read-only status
        # call for the dialog's branch/dirty-tree display — only the
        # `claude` launch itself must be refused.
        if command and command[0] == "claude":
            raise AssertionError("claude must not be launched merely by opening the confirmation dialog")
        return real_run(command, **kwargs)

    monkeypatch.setattr(agent_runner.subprocess, "run", fail_if_claude_launched)

    repo = tmp_path / "aios-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    project_config.save_repository_path("AIOS", str(repo))

    _seed_task()
    at = _at_on_page("kanban")
    assert not at.exception

    open_button = next(b for b in at.button if b.label == "Запустить агента")
    at = open_button.click().run()
    assert not at.exception

    confirm_key = "kanban_seeded-task-1_launch_confirmed"
    assert any(c.key == confirm_key for c in at.checkbox), "confirmation dialog did not open"
    assert not any(c.key == confirm_key for c in at.main.checkbox), (
        "launch confirmation is rendered inline inside the narrow Kanban lane column "
        "instead of as a full-width st.dialog"
    )


def test_kanban_launcher_refuses_unconfigured_repository(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("subprocess.run must not be called when the repository is unconfigured")

    monkeypatch.setattr(agent_runner.subprocess, "run", fail_if_called)

    _seed_task()
    at = _at_on_page("kanban")
    assert not at.exception

    open_button = next(b for b in at.button if b.label == "Запустить агента")
    at = open_button.click().run()
    assert not at.exception

    errors = [e.value for e in at.error]
    assert any("не настроен" in message for message in errors)


# --------------------------------------------------------------------------
# AICC-UI-001: a task whose `project` field holds a display name renders under
# its canonical project lane, not only on the "all projects" board.
# --------------------------------------------------------------------------


def test_kanban_renders_display_name_task_under_canonical_project_lane():
    """End-to-end proof for AICC-UI-001. AICC-CI-001 stores its `project` as
    the display name "AI Command Center", but the Kanban project selector
    emits the canonical id "AICC". Before the fix the task rendered on the
    "all projects" board yet vanished the moment the AICC pill was selected —
    "not rendered anywhere" from the user working inside their project. This
    seeds that exact shape, selects the AICC pill, and asserts the card's
    title is actually rendered."""
    task = _seed_task(
        id="AICC-CI-001",
        project="AI Command Center",  # display name, not the canonical "AICC" id
        title="Main Branch Protection and Required CI Checks",
        priority="P0",  # also non-canonical — guards the earlier priority fix too
        status="Backlog",
    )
    # Select the canonical "AICC" project pill (what the selector actually emits).
    at = _at_on_page("kanban", kanban_project_selector_pills="AICC")
    assert not at.exception
    rendered = "\n".join(node.value for node in at.markdown)
    assert task["title"] in rendered, (
        "AICC-CI-001 (project stored as display name 'AI Command Center') is not "
        "rendered under the canonical 'AICC' Kanban lane"
    )


def test_kanban_display_name_task_hidden_under_a_different_project_lane():
    """Control for the fix above: the normalization must not make the task
    match *every* lane — selecting an unrelated project (AIOS) must still hide
    an AICC task."""
    task = _seed_task(
        id="AICC-CI-001",
        project="AI Command Center",
        title="Main Branch Protection and Required CI Checks",
        priority="P0",
        status="Backlog",
    )
    at = _at_on_page("kanban", kanban_project_selector_pills="AIOS")
    assert not at.exception
    rendered = "\n".join(node.value for node in at.markdown)
    assert task["title"] not in rendered


def test_kanban_lane_pill_and_intelligence_strip_agree_for_display_name_tasks():
    """End-to-end cross-component consistency for AICC-UI-001's remediation.

    Seeds two AICC tasks — one storing the canonical id "AICC", one storing the
    display name "AI Command Center" — plus an unrelated AIOS task, then selects
    the canonical "AICC" pill. All three project-scoped views on the Kanban page
    must agree that AICC has *two* tasks:

      * the Kanban lane renders both AICC card titles (and not the AIOS one);
      * the project-intelligence strip's "Осталось" (remaining) metric reads 2;
      * the "AICC" project pill's own count reads 2.

    Before the remediation the lane/pill were fixed to count the display-name
    task but the strip still undercounted it — the pill/lane said 2 while the
    strip directly above them said 1. This asserts the three can never disagree
    again."""
    _seed_tasks(
        [
            {"id": "AICC-CI-001", "project": "AI Command Center", "title": "Display-name AICC task",
             "priority": "P0", "status": "Backlog"},
            {"id": "AICC-X-002", "project": "AICC", "title": "Canonical AICC task",
             "priority": "High", "status": "Backlog"},
            {"id": "AIOS-1", "project": "AIOS", "title": "Unrelated AIOS task",
             "priority": "High", "status": "Backlog"},
        ]
    )
    at = _at_on_page("kanban", kanban_project_selector_pills="AICC")
    assert not at.exception

    rendered = "\n".join(node.value for node in at.markdown)
    # Lane: both AICC tasks render, the AIOS one does not.
    assert "Display-name AICC task" in rendered
    assert "Canonical AICC task" in rendered
    assert "Unrelated AIOS task" not in rendered

    # Intelligence strip: the "Осталось" (remaining, = active count) metric must
    # equal the two rendered AICC cards, not undercount the display-name one.
    remaining = next(m for m in at.metric if m.label == "Осталось")
    assert remaining.value == "2", (
        f"intelligence strip 'Осталось' reads {remaining.value!r} but the AICC lane renders 2 cards"
    )

    # Pill: the "AICC" pill label carries its own count (the selector formats
    # the canonical "AICC" option with its display name "AI Command Center"),
    # which must also be 2.
    pill_labels = at.pills[0].options
    aicc_label = next(label for label in pill_labels if label.startswith("AI Command Center ·"))
    assert aicc_label.endswith("· 2"), f"AICC pill label {aicc_label!r} disagrees with the 2-card lane"


# --------------------------------------------------------------------------
# AICC-UI-001 remediation, phase 2: the five remaining task-domain pages
# (Executive, Workspace, Timeline, Chat, Focus) must count/filter a display-
# name task under its canonical project, in agreement with the Kanban lane —
# each of these compared raw strings before the fix. AICC is PROJECT_IDS[0],
# so the per-project loops render it first.
# --------------------------------------------------------------------------


def _seed_two_display_name_and_one_canonical_aicc_active() -> int:
    """Seed 2 display-name AICC + 1 canonical AICC active tasks (+ 1 AIOS),
    none Done. Returns the Kanban-lane active-AICC count they must all match."""
    _seed_tasks(
        [
            {"id": "AICC-D1", "project": "AI Command Center", "title": "DisplayName Task One", "status": "Backlog"},
            {"id": "AICC-D2", "project": "AI Command Center", "title": "DisplayName Task Two", "status": "Review"},
            {"id": "AICC-C1", "project": "AICC", "title": "Canonical Task", "status": "In Progress"},
            {"id": "AIOS-1", "project": "AIOS", "title": "AIOS Task", "status": "Backlog"},
        ]
    )
    tasks = storage.read_json(Path(os.environ["AICC_DATA_DIR"]) / "tasks.json", [])
    options = task_view.kanban_priority_options(tasks)
    lane = task_view.filter_kanban_tasks(tasks, project="AICC", priorities=options)
    return sum(1 for t in lane if t.get("status") != "Done")  # 3


def test_executive_project_active_count_equals_kanban_lane():
    kanban_active = _seed_two_display_name_and_one_canonical_aicc_active()
    assert kanban_active == 3
    at = _at_on_page("executive")
    assert not at.exception
    # Per-project "Активн." metrics render in PROJECT_IDS order; AICC is first.
    aicc_active_metrics = [m for m in at.metric if m.label == "Активн."]
    assert aicc_active_metrics, "executive page rendered no per-project 'Активн.' metric"
    assert int(aicc_active_metrics[0].value) == kanban_active, (
        f"Executive AICC active count {aicc_active_metrics[0].value} != Kanban lane {kanban_active} "
        "(display-name tasks dropped)"
    )


def test_workspace_project_active_count_equals_kanban_lane():
    kanban_active = _seed_two_display_name_and_one_canonical_aicc_active()
    at = _at_on_page("workspace")
    assert not at.exception
    aicc_active_metrics = [m for m in at.metric if m.label == "Активные"]
    assert aicc_active_metrics, "workspace page rendered no per-project 'Активные' metric"
    assert int(aicc_active_metrics[0].value) == kanban_active, (
        f"Workspace AICC active count {aicc_active_metrics[0].value} != Kanban lane {kanban_active}"
    )


def test_timeline_project_filter_includes_display_name_task_under_canonical_lane():
    _seed_tasks(
        [{"id": "AICC-CI-001", "project": "AI Command Center", "title": "TimelineDisplayNameTask", "status": "Backlog"}]
    )
    at = _at_on_page("timeline", timeline_project_filter="AICC")
    assert not at.exception
    rendered = "\n".join([n.value for n in at.markdown] + [n.value for n in at.caption])
    assert "TimelineDisplayNameTask" in rendered, "display-name task's timeline event dropped under the AICC filter"
    # Control: selecting an unrelated project must hide it.
    at_other = _at_on_page("timeline", timeline_project_filter="AIOS")
    assert not at_other.exception
    rendered_other = "\n".join([n.value for n in at_other.markdown] + [n.value for n in at_other.caption])
    assert "TimelineDisplayNameTask" not in rendered_other


def test_chat_task_link_options_include_display_name_task_under_canonical_project():
    _seed_tasks(
        [{"id": "AICC-CI-001", "project": "AI Command Center", "title": "ChatLinkTask", "status": "Backlog"}]
    )
    # chat_project defaults to PROJECT_IDS[0] == "AICC"; no conversations exist,
    # so the "+ Новый разговор" branch renders the task-link selectbox.
    at = _at_on_page("chat", chat_project_select="AICC")
    assert not at.exception
    link_box = next(box for box in at.selectbox if "Привязать к задаче" in box.label)
    # Options are formatted via task_label(); the task appears as its label.
    assert any("ChatLinkTask" in str(opt) for opt in link_box.options), (
        "display-name AICC task missing from AICC chat task-link options"
    )
    # Control: under AIOS it must not be linkable.
    at_other = _at_on_page("chat", chat_project_select="AIOS")
    assert not at_other.exception
    link_box_other = next(box for box in at_other.selectbox if "Привязать к задаче" in box.label)
    assert not any("ChatLinkTask" in str(opt) for opt in link_box_other.options)


def test_focus_project_filter_includes_display_name_task_under_canonical_lane():
    _seed_tasks(
        [{"id": "AICC-CI-001", "project": "AI Command Center", "title": "FocusDisplayNameTask", "status": "In Progress"}]
    )
    at = _at_on_page("focus", focus_project_filter="AICC")
    assert not at.exception
    rendered = "\n".join([n.value for n in at.markdown] + [n.value for n in at.caption])
    assert "FocusDisplayNameTask" in rendered, "display-name task excluded from the AICC focus filter"
    # Control: selecting AIOS must exclude it (page shows the empty-state info).
    at_other = _at_on_page("focus", focus_project_filter="AIOS")
    assert not at_other.exception
    rendered_other = "\n".join([n.value for n in at_other.markdown] + [n.value for n in at_other.caption])
    assert "FocusDisplayNameTask" not in rendered_other


def test_focus_mode_does_not_crash_on_a_non_canonical_task_status():
    """Regression (audit M1): Focus Mode's status selectbox did
    `KANBAN_COLUMNS.index(task["status"])` with no guard, so a task in a status
    that is live but not a Kanban column (e.g. "Blocked") raised ValueError and
    crashed the whole page."""
    _seed_tasks(
        [{"id": "AICC-BLK-001", "project": "AICC", "title": "BlockedFocusTask", "status": "Blocked"}]
    )
    at = _at_on_page("focus", focus_project_filter="AICC")
    assert not at.exception
    rendered = "\n".join(n.value for n in at.markdown)
    assert "BlockedFocusTask" in rendered


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


def test_full_launch_flow_records_run_and_parses_verdict(fake_claude, tmp_path):
    """The Kanban Task Card's Launch button now bridges onto the async v2
    Session Supervisor (see `launch_service.execute_agent_launch_v2`): the
    click itself must return immediately, and the resulting `runtime.db` run
    must reach `COMPLETED` with the right resolved workspace and a verdict
    parseable from its final result text — the same guarantee the old
    synchronous `execute_agent_launch` gave, just delivered asynchronously."""
    repo = tmp_path / "aios-fake-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "f.txt").write_text("hello")
    subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)

    project_config.save_repository_path("AIOS", str(repo))
    fake_claude["FAKE_CLAUDE_LINES"] = json.dumps(
        [json.dumps({"type": "result", "result": "Verdict: APPROVED FOR COMMIT"})]
    )

    _seed_task()
    at = _at_on_page("kanban")
    assert not at.exception

    at = at.button(key="kanban_seeded-task-1_launch_open_btn").click().run()
    assert not at.exception

    at = at.checkbox(key="kanban_seeded-task-1_launch_confirmed").check().run()
    assert not at.exception

    at = at.button(key="kanban_seeded-task-1_launch_launch_btn").click().run()
    assert not at.exception

    db_path = runtime_db.resolve_db_path()
    runs = runtime_db.list_runs(db_path, task_id="seeded-task-1")
    assert len(runs) == 1
    final = _wait_for_run_terminal(db_path, runs[0]["id"])
    assert final["state"] == "COMPLETED"
    assert final["repository_path"] == str(repo.resolve())

    events = runtime_db.list_run_events(db_path, final["id"], limit=1_000_000)
    result_text = runtime_reports.result_text(events)
    parsed = report_parser.parse_report(result_text)
    assert parsed["verdict"] == "APPROVED_FOR_COMMIT"


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    (path / "f.txt").write_text("hello")
    subprocess.run(["git", "add", "f.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=path, check=True)


def test_launcher_provisions_missing_isolated_worktree_and_launches(fake_claude, tmp_path):
    """Founder Gate regression, Kanban path: a task whose isolated worktree
    does not exist yet must still be launchable — the launch control is
    enabled (not permanently blocked), clicking it provisions the worktree,
    and the agent runs *in that worktree*, never in the main repository."""
    project_repo = tmp_path / "aios"
    project_repo.mkdir()
    _init_repo(project_repo)  # primary checkout on main

    worktree = tmp_path / "worktrees" / "audit"  # absent — must be provisioned
    project_config.save_repository_path("AIOS", str(project_repo))
    fake_claude["FAKE_CLAUDE_TOUCH_FILE"] = "agent_ran_here.txt"
    fake_claude["FAKE_CLAUDE_LINES"] = json.dumps(
        [json.dumps({"type": "result", "result": "Verdict: APPROVED FOR COMMIT"})]
    )

    _seed_task(
        workspace_path=str(worktree),
        branch="audit/execution-queue",
        base_branch="main",
    )
    at = _at_on_page("kanban")
    assert not at.exception

    at = at.button(key="kanban_seeded-task-1_launch_open_btn").click().run()
    assert not at.exception
    # The operator is told the worktree will be created, not shown a fatal error.
    assert any("создан автоматически" in i.value for i in at.info)

    at = at.checkbox(key="kanban_seeded-task-1_launch_confirmed").check().run()
    assert not at.exception

    launch_button = at.button(key="kanban_seeded-task-1_launch_launch_btn")
    assert launch_button.disabled is False  # provisionable launch is actionable
    at = launch_button.click().run()
    assert not at.exception

    db_path = runtime_db.resolve_db_path()
    runs = runtime_db.list_runs(db_path, task_id="seeded-task-1")
    assert len(runs) == 1
    final = _wait_for_run_terminal(db_path, runs[0]["id"])
    assert final["state"] == "COMPLETED"
    assert final["repository_path"] == str(worktree.resolve())
    assert final["expected_branch"] == "audit/execution-queue"

    # The worktree exists on the expected branch; the agent ran inside it; the
    # main repository was never touched.
    assert worktree.is_dir()
    status_out = subprocess.run(
        ["git", "-C", str(worktree), "branch", "--show-current"],
        capture_output=True, text=True, check=True,
    )
    assert status_out.stdout.strip() == "audit/execution-queue"
    assert (worktree / "agent_ran_here.txt").exists()
    assert not (project_repo / "agent_ran_here.txt").exists()
    main_status = subprocess.run(
        ["git", "-C", str(project_repo), "status", "--porcelain"],
        capture_output=True, text=True, check=True,
    )
    assert main_status.stdout.strip() == ""


def test_launcher_launches_claude_against_task_workspace_not_project_repository(fake_claude, tmp_path):
    """Regression test for the Launch path-resolution bug: a task's own
    `workspace_path` (a separate worktree on its own feature branch) must be
    what Claude Code actually runs against, and what gets persisted in the
    v2 run record and the task's `launch_history` — never silently replaced
    by the project's configured `repository_path`, even though both are
    valid, clean git repositories. Covers the same regression as before, now
    through the async v2 bridge (`launch_service.execute_agent_launch_v2`)."""
    project_repo = tmp_path / "aios"
    project_repo.mkdir()
    _init_repo(project_repo)

    # The task's workspace is a real *isolated worktree* of the project repo on
    # its own feature branch — what an isolated task workspace actually is. The
    # workspace-isolation gate verifies it belongs to the project repo and is on
    # the expected branch, so an unrelated repo standing in for it (the previous
    # setup) is correctly rejected now.
    task_workspace = tmp_path / "aios-p1-deployment"
    subprocess.run(
        ["git", "worktree", "add", "-b", "feature/p1-7-deployment", str(task_workspace), "HEAD"],
        cwd=project_repo, check=True, capture_output=True, text=True,
    )

    project_config.save_repository_path("AIOS", str(project_repo))
    fake_claude["FAKE_CLAUDE_LINES"] = json.dumps(
        [json.dumps({"type": "result", "result": "Verdict: APPROVED FOR COMMIT"})]
    )

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

    db_path = runtime_db.resolve_db_path()
    runs = runtime_db.list_runs(db_path, task_id="seeded-task-1")
    assert len(runs) == 1
    final = _wait_for_run_terminal(db_path, runs[0]["id"])
    assert final["state"] == "COMPLETED"
    assert final["repository_path"] == str(task_workspace.resolve())
    assert final["repository_path"] != str(project_repo.resolve())
    assert final["expected_branch"] == "feature/p1-7-deployment"

    tasks_on_disk = json.loads((Path(os.environ["AICC_DATA_DIR"]) / "tasks.json").read_text())
    saved_task = next(t for t in tasks_on_disk if t["id"] == "seeded-task-1")
    assert saved_task["launch_history"][-1]["workspace_path"] == str(task_workspace.resolve())
    assert saved_task["launch_history"][-1]["branch"] == "feature/p1-7-deployment"
    assert saved_task["current_run_id"] == final["id"]


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


# --------------------------------------------------------------------------
# Create Task page: project-driven inheritance of workspace/branch/executor/prompt
# --------------------------------------------------------------------------


def test_create_task_page_shows_inherited_workspace_from_project_default():
    project_config.save_project_settings(
        "AIOS", default_workspace_path="/ws/aios-default", default_branch="develop", default_executor="claude_code",
    )
    at = _at_on_page("create", create_task_project="AIOS")
    assert not at.exception
    caption_text = " ".join(c.value for c in at.caption)
    assert "/ws/aios-default" in caption_text
    assert "develop" in caption_text


def test_create_task_without_project_config_shows_no_inherited_workspace():
    at = _at_on_page("create", create_task_project="PERSONAL")
    assert not at.exception
    caption_text = " ".join(c.value for c in at.caption)
    assert "workspace `—`" in caption_text


def test_created_task_inherits_project_workspace_and_branch_without_manual_entry():
    project_config.save_project_settings("AIOS", default_workspace_path="/ws/aios-default", default_branch="develop")

    at = _at_on_page("create", create_task_project="AIOS")
    assert not at.exception

    at = at.text_input(key="create_task_title").set_value("Inherit test").run()
    at = at.text_area(key="create_task_objective").set_value("Do the inherited thing").run()
    at = at.button(key="create_task_form_submit").click().run()
    assert not at.exception

    tasks_on_disk = storage.read_json(Path(os.environ["AICC_DATA_DIR"]) / "tasks.json", [])
    assert len(tasks_on_disk) == 1
    assert tasks_on_disk[0]["workspace_path"] == "/ws/aios-default"
    assert tasks_on_disk[0]["branch"] == "develop"


def test_created_task_override_wins_over_project_inheritance(tmp_path):
    project_config.save_project_settings("AIOS", default_workspace_path="/ws/aios-default", default_branch="develop")

    at = _at_on_page("create", create_task_project="AIOS")
    assert not at.exception

    override_path = str(tmp_path / "manual-override-workspace")
    at = at.text_input(key="create_task_workspace_override").set_value(override_path).run()
    at = at.text_input(key="create_task_title").set_value("Override test").run()
    at = at.text_area(key="create_task_objective").set_value("Do the overridden thing").run()
    at = at.button(key="create_task_form_submit").click().run()
    assert not at.exception

    tasks_on_disk = storage.read_json(Path(os.environ["AICC_DATA_DIR"]) / "tasks.json", [])
    assert len(tasks_on_disk) == 1
    assert tasks_on_disk[0]["workspace_path"] == override_path
    assert tasks_on_disk[0]["branch"] == "develop"  # not overridden — still inherited


def _snapshot_dir(directory: Path) -> dict[str, tuple[int, float]]:
    if not directory.is_dir():
        return {}
    return {
        str(path.relative_to(directory)): (path.stat().st_size, path.stat().st_mtime)
        for path in directory.rglob("*")
        if path.is_file()
    }


def test_created_task_generated_markdown_is_isolated_from_the_real_repository(isolated_generated_dir):
    """Regression test for the `GENERATED_DIR` test-isolation leak (see
    `tests/conftest.py`'s `isolated_generated_dir` fixture docstring for the full root
    cause): submitting the create-task form shells out to `scripts/start-task.sh`,
    which used to write real Markdown straight into this repository's real
    `generated/AIOS/` on every run of a test like this one — that is exactly how the
    stray `*_implementation.md` files with objective text `"Do the overridden thing"`/
    `"Do the inherited thing"` (the two tests directly above this one) ended up there.
    Proves the write now lands only under the isolated directory, the real
    `generated/` tree is left byte-for-byte unchanged, and `workspace_home
    .GENERATED_DIR` — the module `app.py`'s own "Generated tasks" viewer reads
    through — resolves to that same isolated directory."""
    real_generated_dir = Path(__file__).resolve().parent.parent / "generated"
    before = _snapshot_dir(real_generated_dir)

    at = _at_on_page("create", create_task_project="AIOS")
    assert not at.exception
    at = at.text_input(key="create_task_title").set_value("Isolation regression").run()
    at = at.text_area(key="create_task_objective").set_value("Prove GENERATED_DIR isolation").run()
    at = at.button(key="create_task_form_submit").click().run()
    assert not at.exception

    written = list((isolated_generated_dir / "AIOS").glob("*.md"))
    assert len(written) == 1
    assert "Prove GENERATED_DIR isolation" in written[0].read_text(encoding="utf-8")

    assert _snapshot_dir(real_generated_dir) == before
    assert workspace_home.GENERATED_DIR == isolated_generated_dir


# --------------------------------------------------------------------------
# Create Task page: "Импорт пакета задач" uploader
# --------------------------------------------------------------------------


def _import_task(**overrides) -> dict:
    base = {
        "id": "PKG-001",
        "title": "Imported task",
        "goal": "Imported task goal",
        "repository_path": "/repo",
        "workspace_path": "/repo",
        "branch": "main",
        "status": "Backlog",
        "project": "AIOS",
        "task_type": "implementation",
        "priority": "Medium",
        "depends_on": [],
    }
    base.update(overrides)
    return base


def _upload_package(at, tasks, filename="package.json"):
    payload = json.dumps(tasks).encode("utf-8")
    at.file_uploader(key="import_task_package_uploader").set_value((filename, payload, "application/json"))
    return at.run()


def test_import_task_package_uploader_renders_on_create_page():
    at = _at_on_page("create", create_task_project="AIOS")
    assert not at.exception
    assert any(u.key == "import_task_package_uploader" for u in at.file_uploader)


def test_import_task_package_preview_shows_counts_for_valid_package():
    at = _at_on_page("create", create_task_project="AIOS")
    at = _upload_package(at, [_import_task(id="PKG-001"), _import_task(id="PKG-002")])
    assert not at.exception
    metric_values = [m.value for m in at.metric]
    assert metric_values[:5] == ["2", "2", "0", "0", "0"]  # total/new/dup/errors/warnings
    assert any(b.key == "import_task_package_confirm_btn" for b in at.button)


def test_import_task_package_confirm_writes_tasks_to_store_for_kanban():
    at = _at_on_page("create", create_task_project="AIOS")
    at = _upload_package(at, [_import_task(id="PKG-001"), _import_task(id="PKG-002")])
    assert not at.exception

    confirm = next(b for b in at.button if b.key == "import_task_package_confirm_btn")
    at = confirm.click().run()
    assert not at.exception
    assert "Импортировано задач: 2" in " ".join(s.value for s in at.success)

    stored = storage.read_json(Path(os.environ["AICC_DATA_DIR"]) / "tasks.json", [])
    assert sorted(t["id"] for t in stored) == ["PKG-001", "PKG-002"]
    assert stored[0]["status"] == "Backlog"  # visible on the Kanban board via the same store


def test_import_task_package_reimport_shows_zero_new_and_all_duplicates():
    at = _at_on_page("create", create_task_project="AIOS")
    at = _upload_package(at, [_import_task(id="PKG-001")])
    confirm = next(b for b in at.button if b.key == "import_task_package_confirm_btn")
    at = confirm.click().run()
    assert not at.exception

    at = _at_on_page("create", create_task_project="AIOS")
    at = _upload_package(at, [_import_task(id="PKG-001")])
    assert not at.exception
    metric_values = [m.value for m in at.metric]
    assert metric_values[:3] == ["1", "0", "1"]  # total=1, new=0, duplicates=1
    assert not any(b.key == "import_task_package_confirm_btn" for b in at.button)
    assert any("Нет новых задач" in i.value for i in at.info)


def _upload_markdown_package(at, tasks, filename="package.md"):
    body = "# Task package\n\nSome prose.\n\n```json\n" + json.dumps(tasks) + "\n```\n"
    at.file_uploader(key="import_task_package_uploader").set_value(
        (filename, body.encode("utf-8"), "text/markdown")
    )
    return at.run()


def test_import_task_package_accepts_markdown_by_filename():
    # A .md file parses only because the uploaded filename routes it to the
    # Markdown handler — the plain-text sniffer parses JSON/YAML, not Markdown —
    # so this proves the widened uploader plus filename passing, not the sniffer.
    at = _at_on_page("create", create_task_project="AIOS")
    at = _upload_markdown_package(at, [_import_task(id="MD-001")])
    assert not at.exception
    metric_values = [m.value for m in at.metric]
    assert metric_values[:5] == ["1", "1", "0", "0", "0"]
    assert any(b.key == "import_task_package_confirm_btn" for b in at.button)


def test_import_task_package_from_pasted_text():
    at = _at_on_page("create", create_task_project="AIOS")
    at.text_area(key="import_task_package_paste").set_value(
        json.dumps([_import_task(id="PASTE-001")])
    )
    at = at.run()
    assert not at.exception
    metric_values = [m.value for m in at.metric]
    assert metric_values[:5] == ["1", "1", "0", "0", "0"]
    assert any(b.key == "import_task_package_confirm_btn" for b in at.button)


def test_import_task_package_malformed_json_shows_error():
    at = _at_on_page("create", create_task_project="AIOS")
    at.file_uploader(key="import_task_package_uploader").set_value(("bad.json", b"not json", "application/json"))
    at = at.run()
    assert not at.exception
    assert any("Ошибка разбора пакета" in e.value for e in at.error)


def test_import_task_package_validation_error_blocks_import_button():
    at = _at_on_page("create", create_task_project="AIOS")
    at = _upload_package(at, [_import_task(id="PKG-001", status="Someday")])
    assert not at.exception
    assert not any(b.key == "import_task_package_confirm_btn" for b in at.button)
    assert any("ошибки валидации" in e.value for e in at.error)


def test_import_task_package_unresolved_dependency_blocks_import_button():
    """Founder Review remediation: a typo'd/unknown depends_on id must block
    the whole package by default — same UI treatment as any other blocking
    validation error (no Import button, an error shown)."""
    at = _at_on_page("create", create_task_project="AIOS")
    at = _upload_package(at, [_import_task(id="PKG-001", depends_on=["PKG-TYPO-NONEXISTENT"])])
    assert not at.exception
    assert not any(b.key == "import_task_package_confirm_btn" for b in at.button)
    assert any("PKG-TYPO-NONEXISTENT" in e.value for e in at.error)

    stored = storage.read_json(Path(os.environ["AICC_DATA_DIR"]) / "tasks.json", [])
    assert stored == []


def test_import_task_package_normalizes_project_name_before_writing():
    at = _at_on_page("create", create_task_project="AIOS")
    at = _upload_package(at, [_import_task(id="PKG-001", project="AI Command Center")])
    confirm = next(b for b in at.button if b.key == "import_task_package_confirm_btn")
    at = confirm.click().run()
    assert not at.exception

    stored = storage.read_json(Path(os.environ["AICC_DATA_DIR"]) / "tasks.json", [])
    assert stored[0]["project"] == "AICC"


# --------------------------------------------------------------------------
# Projects settings tab: extended project configuration fields
# --------------------------------------------------------------------------


def test_projects_settings_tab_saves_extended_configuration(tmp_path):
    at = _at_on_page("projects", project_browser_select="AIOS")
    assert not at.exception

    at = at.text_input(key="default_workspace_input_AIOS").set_value(str(tmp_path)).run()
    at = at.text_input(key="default_branch_input_AIOS").set_value("develop").run()
    at = at.text_area(key="default_prompt_input_AIOS").set_value("Default prompt text").run()
    at = at.text_input(key="owner_input_AIOS").set_value("Dmitry").run()
    at = at.button(key="save_project_settings_AIOS").click().run()
    assert not at.exception

    cfg = project_config.get_project_config("AIOS")
    assert cfg["default_workspace_path"] == str(tmp_path)
    assert cfg["default_branch"] == "develop"
    assert cfg["default_prompt"] == "Default prompt text"
    assert cfg["owner"] == "Dmitry"


def test_projects_settings_tab_shows_warning_for_invalid_default_workspace():
    at = _at_on_page("projects", project_browser_select="AIOS")
    assert not at.exception

    at = at.text_input(key="default_workspace_input_AIOS").set_value("/definitely/does/not/exist").run()
    at = at.button(key="save_project_settings_AIOS").click().run()
    assert not at.exception

    warnings = [w.value for w in at.warning]
    assert any("Workspace" in w for w in warnings)
    # Still saves despite the warning — warnings are advisory, not blocking.
    assert project_config.get_project_config("AIOS")["default_workspace_path"] == "/definitely/does/not/exist"


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


# --------------------------------------------------------------------------
# Engineering Workspace redesign: canonical project registry, Kanban state
# separation, recommendation surface, execution queue
# --------------------------------------------------------------------------


def _seed_tasks(tasks: list[dict]) -> None:
    """Seeds several full task records at once — `_seed_task()` above only
    ever writes a single task. Every record is run through
    `models.default_task_workflow_fields`/`default_task_execution_fields`
    first, same as `_seed_task`, so callers only have to specify what's
    relevant to the scenario under test."""
    data_dir = Path(os.environ["AICC_DATA_DIR"])
    normalized = []
    for overrides in tasks:
        task = {
            "id": overrides["id"],
            "project": "AIOS",
            "title": overrides["id"],
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
        task.update(models.default_task_execution_fields())
        task.update(overrides)
        normalized.append(task)
    storage.atomic_write_json(data_dir / "tasks.json", normalized)


def test_kanban_page_registry_includes_aicos_with_no_local_projects_dict():
    # Regression: a second, hand-maintained `PROJECTS` dict used to live in
    # app.py and silently omit AICOS from every Kanban selector/filter.
    # `app` has no `PROJECTS` attribute at all anymore — `models.PROJECT_IDS`
    # is the only registry.
    import app

    assert not hasattr(app, "PROJECTS")

    _seed_tasks([{"id": "aicos-task", "project": "AICOS", "title": "AICOS task"}])
    at = _at_on_page("kanban")
    assert not at.exception
    assert f"Проектов в реестре: {len(models.PROJECT_IDS)}" in [c.value for c in at.caption]
    body = " ".join(md.value for md in at.markdown)
    assert "AICOS task" in body


def test_kanban_project_selector_lets_every_registered_project_through():
    at = _at_on_page("kanban")
    assert not at.exception
    assert at.session_state["kanban_project_selector_pills"] == project_selector.ALL_PROJECTS_LABEL


def test_kanban_project_selector_filters_board_to_selected_project():
    _seed_tasks(
        [
            {"id": "aios-task", "project": "AIOS", "title": "AIOS-only task"},
            {"id": "aicos-task", "project": "AICOS", "title": "AICOS-only task"},
        ]
    )
    at = _at_on_page("kanban", kanban_project_selector_pills="AICOS")
    assert not at.exception
    body = " ".join(md.value for md in at.markdown)
    assert "AICOS-only task" in body
    assert "AIOS-only task" not in body


def test_kanban_project_filter_isolates_each_split_project():
    """Founder Review regression: AICC/AIOS/AICOS/PRODUCT/ECOSYSTEM must each
    show only their own tasks on the filtered Kanban board — before the
    registry split, "AI Command Center" and "Ecosystem" tasks both landed
    under the single id AICOS and could never be told apart here."""
    _seed_tasks(
        [
            {"id": "aicc-task", "project": "AICC", "title": "AICC-only task"},
            {"id": "aios-task", "project": "AIOS", "title": "AIOS-only task"},
            {"id": "aicos-task", "project": "AICOS", "title": "AICOS-only task"},
            {"id": "product-task", "project": "PRODUCT", "title": "PRODUCT-only task"},
            {"id": "ecosystem-task", "project": "ECOSYSTEM", "title": "ECOSYSTEM-only task"},
        ]
    )
    titles = {
        "AICC": "AICC-only task",
        "AIOS": "AIOS-only task",
        "AICOS": "AICOS-only task",
        "PRODUCT": "PRODUCT-only task",
        "ECOSYSTEM": "ECOSYSTEM-only task",
    }
    for project_id, own_title in titles.items():
        at = _at_on_page("kanban", kanban_project_selector_pills=project_id)
        assert not at.exception
        body = " ".join(md.value for md in at.markdown)
        assert own_title in body
        for other_project_id, other_title in titles.items():
            if other_project_id != project_id:
                assert other_title not in body


def test_kanban_card_separates_blocked_reason_from_planning_and_execution_badges():
    _seed_tasks(
        [
            {"id": "blocker", "title": "Blocker task"},
            {"id": "blocked", "title": "Blocked task", "depends_on": ["blocker"]},
        ]
    )
    at = _at_on_page("kanban")
    assert not at.exception
    badges = " ".join(md.value for md in at.markdown if "badge" in md.value)
    assert "Заблокировано" in badges
    captions = [c.value for c in at.caption]
    assert any(c.startswith("Ожидает: Blocker task") for c in captions)


def test_kanban_card_shows_ready_launch_status_separately_from_priority():
    _seed_tasks([{"id": "solo", "title": "Solo task", "priority": "High"}])
    at = _at_on_page("kanban")
    assert not at.exception
    badges = [md.value for md in at.markdown if "badge" in md.value]
    assert any("High" in b for b in badges)
    assert any("Ready" in b for b in badges)


def test_recommendations_panel_shows_dependencies_and_impact():
    _seed_tasks(
        [
            {"id": "base", "title": "Base task", "status": "Done"},
            {"id": "dependent", "title": "Dependent task", "depends_on": ["base"], "priority": "Critical"},
        ]
    )
    at = _at_on_page("kanban")
    assert not at.exception
    captions = [c.value for c in at.caption]
    assert any("Зависимости: Base task" in c for c in captions)


def test_recommendations_panel_launch_button_starts_v2_run(fake_claude, tmp_path):
    fake_claude["FAKE_CLAUDE_LINES"] = json.dumps([json.dumps({"type": "result", "result": "done"})])
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    # Normal task-v2 launches now fail closed unless workspace ownership can
    # be verified against the project's canonical source repository.
    project_config.save_repository_path("AIOS", str(repo))
    _seed_tasks([{"id": "reco-launch", "title": "Recommended task", "workspace_path": str(repo)}])

    at = _at_on_page("kanban")
    assert not at.exception
    launch_button = next(b for b in at.button if b.key == "kanban_reco_reco-launch_launch")
    at = launch_button.click().run()
    assert not at.exception
    successes = [s.value for s in at.success]
    assert any("Запуск начат" in s for s in successes)

    run_id = successes[0].split("`")[1]
    db_path = Path(os.environ["AICC_DATA_DIR"]) / "runtime.db"
    _wait_for_run_terminal(db_path, run_id)


def test_recommendations_panel_launch_blocked_by_dirty_tree_shows_reason(fake_claude, tmp_path):
    """Root-cause regression for the "launch button does nothing" bug:
    `execution_queue.launch_ready` deliberately refuses to launch a task
    whose workspace has validation warnings (dirty working tree, detached
    HEAD, branch mismatch) — a batch/one-click action has no per-task human
    to acknowledge them, unlike the task card's own launcher. Before the fix,
    the resulting message was rendered and then immediately discarded by the
    `st.rerun()` the same click handler issues, so this refusal was
    indistinguishable from the button doing nothing at all (exactly what was
    reported: every AIOS task launch through this button silently no-ops,
    because the real AIOS repository normally has a dirty working tree)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "f.txt").write_text("dirty change")
    (repo / "untracked.txt").write_text("new")
    _seed_tasks([{"id": "reco-launch", "title": "Recommended task", "workspace_path": str(repo)}])

    at = _at_on_page("kanban")
    assert not at.exception
    launch_button = next(b for b in at.button if b.key == "kanban_reco_reco-launch_launch")
    at = launch_button.click().run()
    assert not at.exception
    warnings = [w.value for w in at.warning]
    # The generic "требует подтверждения... запустите вручную" summary is
    # gone — the exact `validate_launch` warnings are itemized as bullets.
    assert any("Не удалось запустить сразу" in w for w in warnings)
    assert any("- Рабочее дерево не чистое" in w for w in warnings)
    assert not any("запустите вручную из карточки задачи" in w for w in warnings)

    # And the full validation report is available on demand, not forced on
    # the reader by default.
    details = next(e for e in at.expander if "Детали проверки" in e.label)
    report = json.loads(details.json[0].value)
    assert report["errors"] == []
    assert any("Рабочее дерево не чистое" in w for w in report["warnings"])
    assert report["git_status"]["dirty"] is True


def test_queue_launch_ready_blocked_by_dirty_tree_shows_reason(fake_claude, tmp_path):
    """Same regression as above, for the "🚀 Запустить готовые" / "Запустить
    следующую готовую задачу" queue-panel buttons: a skipped entry (dirty
    tree, branch mismatch, etc.) must show its blocking reason instead of
    silently doing nothing, and the entry must stay in the queue (not be
    dropped) so the user can act on it from the task card."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "f.txt").write_text("dirty change")
    (repo / "untracked.txt").write_text("new")
    task = _seed_task(id="seeded-task-1", workspace_path=str(repo))

    data_dir = Path(os.environ["AICC_DATA_DIR"])
    entries = execution_queue.enqueue([], task, {task["id"]: task})
    execution_queue.save_queue(data_dir, entries)

    at = _at_on_page("kanban")
    assert not at.exception
    launch_ready_btn = next(b for b in at.button if b.key == "kanban_queue_launch_ready")
    at = launch_ready_btn.click().run()
    assert not at.exception
    warnings = [w.value for w in at.warning]
    # The exact `validate_launch` warning is itemized as a bullet, naming
    # the task — not collapsed into the old generic summary.
    assert any(
        "seeded-task-1" in w and "- Рабочее дерево не чистое" in w for w in warnings
    )
    assert not any("запустите вручную из карточки задачи" in w for w in warnings)

    details = next(e for e in at.expander if "seeded-task-1" in e.label)
    report = json.loads(details.json[0].value)
    assert any("Рабочее дерево не чистое" in w for w in report["warnings"])
    assert report["workspace_path"] == str(repo)

    entries_after = execution_queue.load_queue(data_dir)
    assert entries_after[0]["state"] == execution_queue.STATE_READY
    assert entries_after[0]["run_id"] is None


def test_kanban_card_enqueue_button_adds_task_to_execution_queue():
    _seed_task(id="seeded-task-1", workspace_path=None)
    at = _at_on_page("kanban")
    assert not at.exception
    enqueue_button = next(b for b in at.button if b.key == "kanban_seeded-task-1_action_queue")
    at = enqueue_button.click().run()
    assert not at.exception

    entries = execution_queue.load_queue(Path(os.environ["AICC_DATA_DIR"]))
    assert any(e["task_id"] == "seeded-task-1" for e in entries)
    captions = [c.value for c in at.caption]
    assert any("Готово к запуску" in c or "Ожидает зависимостей" in c for c in captions)


# --------------------------------------------------------------------------
# AICC-EXECUTION-QUEUE-LOCK-002 — the two enqueue-only UI actions must persist
# through the lock-guarded `execution_queue.enqueue_and_persist`, never a raw
# `load_queue` -> `enqueue` -> `save_queue` triple (which loses a concurrent
# writer's update). Behavioural proof: stub `enqueue_and_persist` to a no-op
# spy — the *only* intended persistence route for these actions — then assert
# each click (a) calls it exactly once with the right root/task/tasks_by_id
# and (b) leaves the on-disk queue untouched, which it could not if a raw
# `save_queue` fallback still ran. `test_kanban_card_enqueue_button_adds_task_
# to_execution_queue` above (and `test_recommendations_enqueue_button_...`
# below) separately cover the unchanged end-to-end behaviour through the real
# locked path.
# --------------------------------------------------------------------------


def _record_save_queue_callers(monkeypatch) -> list[tuple[str, str]]:
    """Record the immediate caller of every queue save during an AppTest run."""
    callers: list[tuple[str, str]] = []
    real_save_queue = execution_queue.save_queue

    def spy(root, entries):
        frame = inspect.currentframe()
        assert frame is not None and frame.f_back is not None
        caller = frame.f_back
        callers.append((Path(caller.f_code.co_filename).name, caller.f_code.co_name))
        return real_save_queue(root, entries)

    monkeypatch.setattr(execution_queue, "save_queue", spy)
    return callers


def test_kanban_card_enqueue_uses_locked_persistence(monkeypatch):
    import app

    _seed_task(id="seeded-task-1", workspace_path=None)

    calls: list[tuple] = []
    save_callers = _record_save_queue_callers(monkeypatch)

    def spy(root, task, tasks_by_id):  # no-op: no persistence at all
        calls.append((root, task, tasks_by_id))

    monkeypatch.setattr(execution_queue, "enqueue_and_persist", spy)

    at = _at_on_page("kanban")
    assert not at.exception
    button = next(b for b in at.button if b.key == "kanban_seeded-task-1_action_queue")
    at = button.click().run()
    assert not at.exception

    assert len(calls) == 1, "card «В очередь» must call enqueue_and_persist exactly once"
    root, task, tasks_by_id = calls[0]
    assert root == app.ROOT
    assert task.get("id") == "seeded-task-1"
    assert tasks_by_id.get("seeded-task-1", {}).get("id") == "seeded-task-1"

    # No raw fallback: with enqueue_and_persist stubbed to a no-op, nothing may
    # have written this task into the queue via a bare save_queue.
    entries = execution_queue.load_queue(Path(os.environ["AICC_DATA_DIR"]))
    assert not any(e.get("task_id") == "seeded-task-1" for e in entries)
    assert save_callers
    assert all(caller == ("execution_queue.py", "_mutate_queue") for caller in save_callers)

    # Success feedback is still emitted (UI behaviour unchanged).
    assert any("очередь" in (msg.value or "").lower() for msg in at.success)


def test_recommendations_enqueue_uses_locked_persistence(monkeypatch):
    import app

    _seed_task(id="seeded-task-1", workspace_path=None)

    calls: list[tuple] = []
    save_callers = _record_save_queue_callers(monkeypatch)

    def spy(root, task, tasks_by_id):  # no-op: no persistence at all
        calls.append((root, task, tasks_by_id))

    monkeypatch.setattr(execution_queue, "enqueue_and_persist", spy)

    at = _at_on_page("kanban")
    assert not at.exception
    button = next(b for b in at.button if b.key == "kanban_reco_seeded-task-1_enqueue")
    at = button.click().run()
    assert not at.exception

    assert len(calls) == 1, "recommendation «В очередь» must call enqueue_and_persist exactly once"
    root, task, tasks_by_id = calls[0]
    assert root == app.ROOT
    assert task.get("id") == "seeded-task-1"
    assert tasks_by_id.get("seeded-task-1", {}).get("id") == "seeded-task-1"

    # No raw fallback and, crucially, the earlier-loaded `queue_entries`
    # snapshot is not re-saved: with enqueue_and_persist stubbed out, the queue
    # must stay empty of this task.
    entries = execution_queue.load_queue(Path(os.environ["AICC_DATA_DIR"]))
    assert not any(e.get("task_id") == "seeded-task-1" for e in entries)
    assert save_callers
    assert all(caller == ("execution_queue.py", "_mutate_queue") for caller in save_callers)


def test_recommendations_enqueue_button_adds_task_to_execution_queue():
    """End-to-end through the real locked path: the recommendation «В очередь»
    action still queues the task (behaviour unchanged by the lock fix)."""
    _seed_task(id="seeded-task-1", workspace_path=None)
    at = _at_on_page("kanban")
    assert not at.exception
    button = next(b for b in at.button if b.key == "kanban_reco_seeded-task-1_enqueue")
    at = button.click().run()
    assert not at.exception

    entries = execution_queue.load_queue(Path(os.environ["AICC_DATA_DIR"]))
    assert any(e["task_id"] == "seeded-task-1" for e in entries)
    rerendered_button = next(b for b in at.button if b.key == "kanban_reco_seeded-task-1_enqueue")
    assert rerendered_button.disabled is True
    assert any(c.value == "В очереди · готово" for c in at.caption)


def test_recommendations_launch_now_uses_locked_enqueue_then_launch_ready(monkeypatch):
    """Launch-now atomically finds-or-creates its queue entry before routing
    that exact entry through `launch_ready`."""
    import app

    _seed_task(id="seeded-task-1", workspace_path=None)

    launch_ready_calls: list[tuple] = []
    real_launch_ready = execution_queue.launch_ready
    enqueue_calls: list[tuple] = []
    real_enqueue = execution_queue.enqueue_and_persist

    def spy_launch_ready(root, entries, *args, **kwargs):
        launch_ready_calls.append((root, entries, args, kwargs))
        return real_launch_ready(root, entries, *args, **kwargs)

    def spy_locked_enqueue(root, task, tasks_by_id):
        enqueue_calls.append((root, task, tasks_by_id))
        return real_enqueue(root, task, tasks_by_id)

    monkeypatch.setattr(execution_queue, "launch_ready", spy_launch_ready)
    monkeypatch.setattr(execution_queue, "enqueue_and_persist", spy_locked_enqueue)

    at = _at_on_page("kanban")
    assert not at.exception
    button = next(b for b in at.button if b.key == "kanban_reco_seeded-task-1_launch")
    at = button.click().run()
    assert not at.exception

    assert len(launch_ready_calls) == 1, "launch-now must route through launch_ready exactly once"
    assert len(enqueue_calls) == 1
    assert enqueue_calls[0][0] == app.ROOT
    assert enqueue_calls[0][1]["id"] == "seeded-task-1"
    root, entries, _, kwargs = launch_ready_calls[0]
    assert root == app.ROOT
    entry_ids = kwargs.get("entry_ids")
    assert len(entry_ids) == 1
    launched_entry = next(e for e in entries if e["id"] == entry_ids[0])
    assert launched_entry["task_id"] == "seeded-task-1"


def test_execution_queue_panel_launch_ready_button_present_once_queued():
    _seed_task(id="seeded-task-1")
    at = _at_on_page("kanban")
    entries = execution_queue.enqueue(
        execution_queue.load_queue(Path(os.environ["AICC_DATA_DIR"])),
        {"id": "seeded-task-1", "status": "Backlog", "depends_on": []},
        {"seeded-task-1": {"id": "seeded-task-1", "status": "Backlog", "depends_on": []}},
    )
    execution_queue.save_queue(Path(os.environ["AICC_DATA_DIR"]), entries)

    at = _at_on_page("kanban")
    assert not at.exception
    assert any(b.key == "kanban_queue_launch_ready" for b in at.button)
    assert any(b.key == "kanban_queue_launch_next" for b in at.button)
