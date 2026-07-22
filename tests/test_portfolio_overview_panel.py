"""Smoke tests for the read-only Portfolio Overview page wiring.

Confirms the page renders without exceptions, surfaces a missing-checkout
error, shows computed content for a real card, and — critically — performs no
mutation (it is a read-only surface).
"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from command_center import git_info, portfolio_config
from command_center.ui import portfolio_panel

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")

CARD = """---
schema_version: "1.0"
task_id: "AICC-OVR-001"
title: "Overview test task"
project: "AICC"
type: "implementation"
capability: "none"
priority: "high"
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

# Overview test task
"""


def _at_on_overview_page() -> AppTest:
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.session_state["nav_page"] = "portfolio_overview"
    at.run()
    return at


def _seed(tmp_path, monkeypatch, git_repo) -> None:
    portfolio_root = tmp_path / "Portfolio"
    lane_dir = portfolio_root / "tasks" / "ready" / "AICC"
    lane_dir.mkdir(parents=True)
    base_branch = git_info.get_status(git_repo)["branch"]
    (lane_dir / "AICC-OVR-001.md").write_text(
        CARD.format(base_branch=base_branch, repository=str(git_repo)), encoding="utf-8"
    )
    monkeypatch.setenv(portfolio_panel.PORTFOLIO_ROOT_ENV, str(portfolio_root))
    portfolio_config.save_repository_path("AICC", str(git_repo))


def test_overview_page_shows_error_when_portfolio_missing(tmp_path, monkeypatch):
    monkeypatch.setenv(portfolio_panel.PORTFOLIO_ROOT_ENV, str(tmp_path / "nope"))
    at = _at_on_overview_page()
    assert not at.exception
    assert any("не найден" in e.value for e in at.error)


def test_overview_page_renders_ready_task_and_health(tmp_path, monkeypatch, git_repo):
    _seed(tmp_path, monkeypatch, git_repo)
    branches_before = set(git_info.get_branches(git_repo))

    at = _at_on_overview_page()

    assert not at.exception
    text = "\n".join(md.value for md in at.markdown)
    assert "AICC-OVR-001" in text
    assert "Portfolio Overview" in text
    # Read-only surface: no branch/worktree created by rendering it.
    assert set(git_info.get_branches(git_repo)) == branches_before
