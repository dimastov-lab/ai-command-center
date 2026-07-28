"""Streamlit AppTest coverage for the Workspace Home page (WORKSPACE_HOME_ARCHITECTURE.md
§14/§17 step 8/§18). Mirrors `test_execution_center_ui.py`'s structure, including its
`_fresh_execution_center_singleton` cache-clearing pattern.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from command_center import activity_log, agent_runner, models, workspace_home

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture(autouse=True)
def _fresh_execution_center_singleton(isolated_data_dir):
    st.cache_resource.clear()
    yield


@pytest.fixture(autouse=True)
def _isolated_workspace_home_artifact_dirs(isolated_data_dir, monkeypatch):
    """`workspace_home.GENERATED_DIR`/`REPORTS_DIR` are module-level constants
    derived from the real repository `ROOT`, not `AICC_DATA_DIR` — same class of
    gap `conftest.isolated_reports_dir` exists to close for `agent_runner`/
    `runtime.reports`. Without this, these UI tests would scan this developer's
    real `generated/`/`reports/` directories instead of a throwaway one."""
    generated_dir = isolated_data_dir / "generated"
    reports_dir = isolated_data_dir / "reports"
    generated_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(workspace_home, "GENERATED_DIR", generated_dir)
    monkeypatch.setattr(workspace_home, "REPORTS_DIR", reports_dir)
    return generated_dir, reports_dir


def _at_on_workspace_home() -> AppTest:
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.session_state["nav_page"] = "workspace_home"
    at.run()
    return at


# --------------------------------------------------------------------------
# Empty state — the default fresh-checkout scenario (§7.1/F3)
# --------------------------------------------------------------------------


def test_workspace_home_deep_link_renders_but_is_consolidated_into_dashboard():
    at = _at_on_workspace_home()
    assert not at.exception
    assert at.subheader[0].value == "Workspace Home"
    assert not any(b.key == "nav_btn_workspace_home" for b in at.sidebar.button)


def test_workspace_home_empty_state_all_six_unconfigured():
    at = _at_on_workspace_home()
    assert not at.exception
    infos = [el.value for el in at.info]
    assert any("Активных прогонов нет" in text for text in infos)
    assert any("Прогонов пока нет" in text for text in infos)
    assert any("Артефактов пока нет" in text for text in infos)
    assert any("Отчётов пока нет" in text for text in infos)
    assert any("Активности пока нет" in text for text in infos)


def test_workspace_home_shows_shared_health_metrics_and_recommendations():
    at = _at_on_workspace_home()
    assert not at.exception

    metric_labels = {metric.label for metric in at.metric}
    assert {
        "Здоровье",
        "Прогресс спринта",
        "Roadmap",
        "Осталось",
        "Заблокировано",
        "Завершено",
    } <= metric_labels
    assert any("Рекомендованные задачи" in markdown.value for markdown in at.markdown)


# --------------------------------------------------------------------------
# Quick Actions (§11/§17 step 9) — every action lands on the correct existing
# page/form, pre-filled where specified, with zero auto-submitted mutation.
# --------------------------------------------------------------------------


def test_quick_action_open_project_navigates_to_projects_page():
    at = _at_on_workspace_home()
    at = at.button(key="home_open_AIOS").click().run()
    assert at.session_state["nav_page"] == "projects"
    assert at.session_state["project_browser_select"] == "AIOS"


def test_quick_action_new_task_navigates_to_create_page_prefilled():
    at = _at_on_workspace_home()
    at = at.button(key="home_new_task_BANK").click().run()
    assert at.session_state["nav_page"] == "create"
    assert at.session_state["create_task_project"] == "BANK"


def test_quick_action_launch_run_navigates_to_execution_center_prefilled():
    at = _at_on_workspace_home()
    at = at.button(key="home_launch_LEGAL").click().run()
    assert at.session_state["nav_page"] == "execution_center"
    assert at.session_state["exec_center_launch_project"] == "LEGAL"
    # No mutation was auto-submitted — landing on the form is not the same as
    # launching a run from it.
    assert not at.exception


# --------------------------------------------------------------------------
# Populated state
# --------------------------------------------------------------------------


def test_workspace_home_populated_state_shows_project_and_artifact(_isolated_workspace_home_artifact_dirs):
    generated_dir, _reports_dir = _isolated_workspace_home_artifact_dirs
    (generated_dir / "AIOS").mkdir(parents=True, exist_ok=True)
    (generated_dir / "AIOS" / "abc123_implementation.md").write_text("# Task\n")

    activity_log.log_event("run_completed", project="AIOS", message="done")

    run = models.new_run_record(
        project="AIOS", task_id=None, agent="claude_code", task_type="implementation",
        repository_path="/tmp/x", prompt="p", timeout_seconds=60,
    )
    run["status"] = "completed"
    agent_runner.append_run(run)

    at = _at_on_workspace_home()
    assert not at.exception

    body_text = " ".join(el.value for el in at.markdown) + " ".join(el.value for el in at.caption)
    assert "AIOS" in body_text


# --------------------------------------------------------------------------
# BANK/LEGAL dual-layer redaction regression (§13/§14)
# --------------------------------------------------------------------------

_BANNED_VALUES = ["TOP SECRET INSTRUCTION", "secret-financial-detail"]


def test_workspace_home_bank_only_state_never_renders_banned_values(_isolated_workspace_home_artifact_dirs):
    generated_dir, reports_dir = _isolated_workspace_home_artifact_dirs
    (generated_dir / "BANK").mkdir(parents=True, exist_ok=True)
    (generated_dir / "BANK" / "abc123_implementation.md").write_text("secret-financial-detail")
    (reports_dir / "BANK").mkdir(parents=True, exist_ok=True)
    (reports_dir / "BANK" / "report.md").write_text("TOP SECRET INSTRUCTION report body")

    activity_log.log_event("run_completed", project="BANK", message="secret-financial-detail in message")

    run = models.new_run_record(
        project="BANK", task_id=None, agent="claude_code", task_type="implementation",
        repository_path="/tmp/x", prompt="TOP SECRET INSTRUCTION", timeout_seconds=60,
    )
    run["status"] = "completed"
    run["report_path"] = "reports/BANK/report.md"
    run["parsed"] = {"verdict": "APPROVED_FOR_COMMIT", "findings": {s: [] for s in models.SEVERITIES}}
    agent_runner.append_run(run)

    # Layer 1: the snapshot dict itself, independent of rendering.
    from command_center.runtime.api import ExecutionCenterAPI

    api = ExecutionCenterAPI()
    snapshot = workspace_home.build_workspace_home_snapshot(execution_center_api=api)
    bank_entries = (
        [r for r in snapshot["recent_runs"] if r.get("project") == "BANK"]
        + [r for r in snapshot["reports"] if r.get("project") == "BANK"]
        + [r for r in snapshot["artifacts"] if r.get("project") == "BANK"]
        + [r for r in snapshot["recent_activity"] if r.get("project") == "BANK"]
    )
    snapshot_text = repr(bank_entries)
    for banned in _BANNED_VALUES:
        assert banned not in snapshot_text

    # Layer 2: the rendered page text, as a second, independent line of defense.
    at = _at_on_workspace_home()
    assert not at.exception
    rendered_text = "\n".join(
        el.value
        for group in (at.markdown, at.caption, at.text, at.info, at.warning, at.error, at.success)
        for el in group
    )
    for banned in _BANNED_VALUES:
        assert banned not in rendered_text
    assert "BANK" in rendered_text  # sanity: the project itself is still shown, just redacted
