from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from command_center import git_info, portfolio_config
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
