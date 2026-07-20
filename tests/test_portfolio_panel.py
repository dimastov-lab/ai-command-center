from __future__ import annotations

import subprocess
from pathlib import Path

from streamlit.testing.v1 import AppTest

from command_center import git_info, portfolio_config, portfolio_launch
from command_center.runtime import db as runtime_db
from command_center.runtime import identity
from command_center.ui import portfolio_panel

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")

CARD = """---
schema_version: "1.0"
task_id: "AICC-UI-777"
title: "Panel test task"
project: "AICC"
type: "implementation"
capability: "none"
priority: "medium"
status: "ready"
repository: "{repository}"
base_branch: "{base_branch}"
branch: null
worktree: null
agent: null
autonomy: "confirmed"
parallel_group: null
requires: []
blocks: []
conflicts_with: []
deliverables: ["a thing gets done"]
validation: ["true"]
stop_conditions: ["Stop once done."]
evidence: []
confidence: null
gated_by: []
---

# Panel test task

## Objective

Do the panel test thing.
"""

# `AICC` ships a built-in default repository mapping (see
# `command_center.portfolio_config.DEFAULT_REPOSITORY_PATHS`), so it cannot be
# used to test the "project not mapped" case — a project with no default at
# all (`AIOS`) is used there instead.
UNMAPPED_PROJECT_CARD = CARD.replace('project: "AICC"', 'project: "AIOS"')


def _at_on_portfolio_page() -> AppTest:
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.session_state["nav_page"] = "portfolio"
    at.run()
    return at


def _seed_portfolio(tmp_path, monkeypatch, git_repo) -> None:
    portfolio_root = tmp_path / "Portfolio"
    lane_dir = portfolio_root / "tasks" / "ready" / "AICC"
    lane_dir.mkdir(parents=True)
    base_branch = git_info.get_status(git_repo)["branch"]
    (lane_dir / "AICC-UI-777.md").write_text(
        CARD.format(base_branch=base_branch, repository=str(git_repo)), encoding="utf-8"
    )
    monkeypatch.setenv(portfolio_panel.PORTFOLIO_ROOT_ENV, str(portfolio_root))
    monkeypatch.setenv("AICC_PORTFOLIO_WORKTREES_ROOT", str(tmp_path / "worktrees"))
    portfolio_config.save_repository_path("AICC", str(git_repo))


def test_portfolio_page_shows_error_when_portfolio_missing(tmp_path, monkeypatch):
    monkeypatch.setenv(portfolio_panel.PORTFOLIO_ROOT_ENV, str(tmp_path / "does-not-exist"))
    at = _at_on_portfolio_page()
    assert not at.exception
    assert any("не найден" in e.value for e in at.error)


def test_portfolio_page_lists_ready_task(tmp_path, monkeypatch, git_repo):
    _seed_portfolio(tmp_path, monkeypatch, git_repo)

    at = _at_on_portfolio_page()
    assert not at.exception
    markdown_text = "\n".join(md.value for md in at.markdown)
    assert "AICC-UI-777" in markdown_text


def test_portfolio_page_dry_run_makes_no_mutation(tmp_path, monkeypatch, git_repo):
    _seed_portfolio(tmp_path, monkeypatch, git_repo)
    branches_before = set(git_info.get_branches(git_repo))

    at = _at_on_portfolio_page()
    at = at.button(key="portfolio_dryrun_AICC-UI-777").click().run()

    assert not at.exception
    assert set(git_info.get_branches(git_repo)) == branches_before
    assert not (tmp_path / "worktrees" / "aicc-ui-777").exists()


def test_portfolio_page_launch_button_disabled_when_repository_not_mapped(tmp_path, monkeypatch, git_repo):
    portfolio_root = tmp_path / "Portfolio"
    lane_dir = portfolio_root / "tasks" / "ready" / "AIOS"
    lane_dir.mkdir(parents=True)
    base_branch = git_info.get_status(git_repo)["branch"]
    (lane_dir / "AICC-UI-777.md").write_text(
        UNMAPPED_PROJECT_CARD.format(base_branch=base_branch, repository=str(git_repo)), encoding="utf-8"
    )
    monkeypatch.setenv(portfolio_panel.PORTFOLIO_ROOT_ENV, str(portfolio_root))
    # `AIOS` deliberately has no repository mapping configured or seeded.

    at = _at_on_portfolio_page()
    assert not at.exception
    launch_button = at.button(key="portfolio_launch_open_AICC-UI-777")
    assert launch_button.disabled is True


def test_portfolio_page_launch_requires_explicit_confirmation_checkbox(tmp_path, monkeypatch, git_repo):
    _seed_portfolio(tmp_path, monkeypatch, git_repo)

    at = _at_on_portfolio_page()
    at = at.button(key="portfolio_launch_open_AICC-UI-777").click().run()
    assert not at.exception

    confirm_button = at.button(key="portfolio_confirm_launch_AICC-UI-777")
    assert confirm_button.disabled is True

    branches_before = set(git_info.get_branches(git_repo))
    at = confirm_button.click().run()
    assert not at.exception
    # A forced click on a disabled button must still not launch anything —
    # the same server-side re-check discipline as every other launch path
    # in this app (see test_app_streamlit.py's equivalent Kanban assertion).
    assert set(git_info.get_branches(git_repo)) == branches_before


# --------------------------------------------------------------------------
# Card status model — a task with an existing run must never render its
# workflow status (`ready`) as the headline while also showing `Blocked`
# (the duplicate-status regression this module's fix addresses).
# --------------------------------------------------------------------------


def _seed_run(*, project: str, repository_path: str, final_state: str | None, request=None) -> str:
    """Creates a real v2 run row via `command_center.runtime.db` — the exact
    SQLite database `app.py`'s cached `ExecutionCenterAPI` reads from
    (`AICC_DATA_DIR`, set by the autouse `isolated_data_dir` fixture, makes
    both resolve to the same file) — and drives it through
    `db.ALLOWED_TRANSITIONS` up to `final_state`, via the same official
    `db.update_run_state` transition function `Supervisor` itself calls
    (never a raw SQL statement). For a terminal `final_state`
    (`COMPLETED`/`FAILED`/`CANCELLED`) no process is involved at all — a
    terminal row's `pid`/liveness are irrelevant to it ever being displayed.

    `app.py`'s cached `get_execution_center_api()` calls `Supervisor.
    reconcile()` once at startup, which conservatively reclassifies any
    `RUNNING` row as `INTERRUPTED` unless its `pid` is both alive *and* its
    `process_start_identity` matches what was recorded at launch time (see
    `Supervisor.reconcile()`'s own docstring for this exact contract). To
    land on a `RUNNING` row this test can rely on surviving that check, a
    real throwaway process is spawned and its pid/identity captured via
    `command_center.runtime.identity.capture_identity` — the very same
    function `Supervisor._launch_process_unguarded` uses when it launches a
    run for real — and persisted onto the row via `update_run_state`, so
    `reconcile()` verifies it exactly as it would a genuine launch. `request`
    (a test's `pytest.FixtureRequest`) terminates that process once the test
    ends. Returns the run id."""
    db_path = runtime_db.resolve_db_path()
    runtime_db.migrate(db_path)
    task = runtime_db.create_task(db_path, project=project, title="t", task_type="implementation")
    session = runtime_db.create_session(
        db_path, task_id=task["id"], project=project, repository_path=repository_path
    )
    run = runtime_db.create_run(
        db_path,
        session_id=session["id"],
        task_id=task["id"],
        project=project,
        task_type="implementation",
        repository_path=repository_path,
        prompt="p",
        is_resume=False,
    )
    for next_state in ("QUEUED", "RUNNING"):
        if final_state is None or run["state"] == final_state:
            return run["id"]
        fields = None
        if next_state == "RUNNING" and final_state == "RUNNING":
            proc = subprocess.Popen(["sleep", "5"])
            if request is not None:
                request.addfinalizer(lambda p=proc: (p.terminate(), p.wait()))
            recorded_identity = identity.capture_identity(proc.pid)
            fields = {
                "pid": proc.pid,
                "process_start_identity": recorded_identity.as_string() if recorded_identity else None,
            }
        run = runtime_db.update_run_state(
            db_path, run["id"], expected_version=run["version"], new_state=next_state, fields=fields
        )
    if final_state and run["state"] != final_state:
        run = runtime_db.update_run_state(db_path, run["id"], expected_version=run["version"], new_state=final_state)
    return run["id"]


def _seed_registry_entry(
    tmp_path, *, task_id: str, run_id: str, project: str = "AICC", branch: str = "task/aicc-ui-777"
) -> None:
    portfolio_launch.save_registry(
        tmp_path,
        {task_id: {"run_id": run_id, "project": project, "branch": branch, "launched_at": "2026-01-01T00:00:00"}},
    )


def _badges(at: AppTest) -> str:
    return " ".join(m.value for m in at.markdown if "-badge[" in m.value)


def test_portfolio_page_shows_ready_status_with_no_duplicate_warning_for_new_task(tmp_path, monkeypatch, git_repo):
    _seed_portfolio(tmp_path, monkeypatch, git_repo)

    at = _at_on_portfolio_page()
    assert not at.exception
    assert ":green-badge[Ready]" in _badges(at)
    all_messages = " ".join(m.value for m in list(at.info) + list(at.warning) + list(at.error))
    assert "запущен" not in all_messages
    assert at.button(key="portfolio_launch_open_AICC-UI-777").disabled is False


def test_portfolio_page_shows_running_status_not_ready_or_blocked_for_active_run(
    tmp_path, monkeypatch, git_repo, request
):
    _seed_portfolio(tmp_path, monkeypatch, git_repo)
    run_id = _seed_run(project="AICC", repository_path=str(git_repo), final_state="RUNNING", request=request)
    _seed_registry_entry(tmp_path, task_id="AICC-UI-777", run_id=run_id)

    at = _at_on_portfolio_page()
    assert not at.exception
    badges = _badges(at)
    assert ":blue-badge[Running]" in badges
    assert ":green-badge[Ready]" not in badges
    assert ":red-badge[Blocked]" not in badges
    assert at.button(key="portfolio_launch_open_AICC-UI-777").disabled is True
    assert at.button(key="portfolio_open_run_AICC-UI-777")
    assert run_id in " ".join(m.value for m in list(at.info) + list(at.warning))


def test_portfolio_page_open_run_button_navigates_to_execution_center(tmp_path, monkeypatch, git_repo, request):
    _seed_portfolio(tmp_path, monkeypatch, git_repo)
    run_id = _seed_run(project="AICC", repository_path=str(git_repo), final_state="RUNNING", request=request)
    _seed_registry_entry(tmp_path, task_id="AICC-UI-777", run_id=run_id)

    at = _at_on_portfolio_page()
    at = at.button(key="portfolio_open_run_AICC-UI-777").click().run()
    assert not at.exception
    # Reuses the app's existing cross-page navigation staging
    # (`pending_nav`/`pending_exec_center_run`, see `app.py`) — no new page,
    # no bespoke navigation mechanism.
    assert at.session_state["nav_page"] == "execution_center"
    assert at.session_state["exec_center_highlight_run"] == run_id


def test_portfolio_page_shows_completed_status_not_blocked_or_ready(tmp_path, monkeypatch, git_repo):
    _seed_portfolio(tmp_path, monkeypatch, git_repo)
    run_id = _seed_run(project="AICC", repository_path=str(git_repo), final_state="COMPLETED")
    _seed_registry_entry(tmp_path, task_id="AICC-UI-777", run_id=run_id)

    at = _at_on_portfolio_page()
    assert not at.exception
    badges = _badges(at)
    assert ":green-badge[Completed]" in badges
    assert ":red-badge[Blocked]" not in badges
    assert ":green-badge[Ready]" not in badges
    assert at.button(key="portfolio_launch_open_AICC-UI-777").disabled is True


def test_portfolio_page_shows_failed_status_not_blocked_and_keeps_duplicate_prevention(
    tmp_path, monkeypatch, git_repo
):
    _seed_portfolio(tmp_path, monkeypatch, git_repo)
    run_id = _seed_run(project="AICC", repository_path=str(git_repo), final_state="FAILED")
    _seed_registry_entry(tmp_path, task_id="AICC-UI-777", run_id=run_id)
    registry_before = portfolio_launch.load_registry(tmp_path)

    at = _at_on_portfolio_page()
    assert not at.exception
    badges = _badges(at)
    assert ":red-badge[Failed]" in badges
    assert ":red-badge[Blocked]" not in badges
    assert at.button(key="portfolio_launch_open_AICC-UI-777").disabled is True
    # The existing-run message is informational/warning, never a red error —
    # even though the run itself failed (task description §4).
    assert not at.error

    assert portfolio_launch.load_registry(tmp_path) == registry_before


def test_portfolio_page_falls_back_to_already_launched_when_run_lookup_fails(tmp_path, monkeypatch, git_repo):
    _seed_portfolio(tmp_path, monkeypatch, git_repo)
    _seed_registry_entry(tmp_path, task_id="AICC-UI-777", run_id="run-does-not-exist-in-db")

    at = _at_on_portfolio_page()
    assert not at.exception
    badges = _badges(at)
    assert ":orange-badge[Already launched]" in badges
    assert ":red-badge[Blocked]" not in badges
    assert at.button(key="portfolio_launch_open_AICC-UI-777").disabled is True


def test_portfolio_page_shows_blocked_with_real_reason_for_precondition_error(tmp_path, monkeypatch, git_repo):
    """A genuine precondition failure (here: unmapped repository) must still
    read as `Blocked`, with its real reason surfaced — never masked by, or
    confused with, the "already launched" status (task description §6)."""
    portfolio_root = tmp_path / "Portfolio"
    lane_dir = portfolio_root / "tasks" / "ready" / "AIOS"
    lane_dir.mkdir(parents=True)
    base_branch = git_info.get_status(git_repo)["branch"]
    (lane_dir / "AICC-UI-777.md").write_text(
        UNMAPPED_PROJECT_CARD.format(base_branch=base_branch, repository=str(git_repo)), encoding="utf-8"
    )
    monkeypatch.setenv(portfolio_panel.PORTFOLIO_ROOT_ENV, str(portfolio_root))

    at = _at_on_portfolio_page()
    assert not at.exception
    assert ":red-badge[Blocked]" in _badges(at)
    assert any("не сопоставлен" in e.value for e in at.error)
    assert at.button(key="portfolio_launch_open_AICC-UI-777").disabled is True
