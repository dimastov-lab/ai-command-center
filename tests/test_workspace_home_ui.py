"""Streamlit AppTest coverage for the Workspace Home page.

Mirrors `tests/test_execution_center_ui.py`'s structure (including its
`_fresh_execution_center_singleton` cache-clearing pattern) and
`tests/test_workspace_home.py`'s own directory-isolation fixture, since
`workspace_home.GENERATED_DIR`/`REPORTS_DIR` are separate module-level
constants from `agent_runner.REPORTS_ROOT`/`runtime.reports.REPORTS_ROOT`
and are not covered by `conftest.py`'s `isolated_reports_dir` fixture.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from command_center import models, project_config, workspace_home
from command_center.runtime import db

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture(autouse=True)
def _fresh_execution_center_singleton(isolated_data_dir):
    st.cache_resource.clear()
    yield


@pytest.fixture(autouse=True)
def isolated_workspace_home_dirs(tmp_path, monkeypatch):
    generated_dir = tmp_path / "generated"
    reports_dir = tmp_path / "reports"
    generated_dir.mkdir()
    reports_dir.mkdir()
    monkeypatch.setattr(workspace_home, "GENERATED_DIR", generated_dir)
    monkeypatch.setattr(workspace_home, "REPORTS_DIR", reports_dir)
    return generated_dir, reports_dir


def _at_on_page(page_key: str, **extra_session_state) -> AppTest:
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.session_state["nav_page"] = page_key
    for key, value in extra_session_state.items():
        at.session_state[key] = value
    at.run()
    return at


def _all_rendered_text(at: AppTest) -> str:
    chunks: list[str] = []
    chunks.extend(md.value for md in at.markdown)
    chunks.extend(c.value for c in at.caption)
    chunks.extend(i.value for i in at.info)
    chunks.extend(w.value for w in at.warning)
    chunks.extend(e.value for e in at.error)
    chunks.extend(s.value for s in at.success)
    chunks.extend(f"{m.label}={m.value}" for m in at.metric)
    return "\n".join(chunks)


def test_workspace_home_page_renders_and_nav_entry_exists():
    import app

    assert "workspace_home" in app.NAV
    at = _at_on_page("workspace_home")
    assert not at.exception
    assert at.subheader[0].value == "Workspace Home"


def test_empty_state_all_projects_unconfigured_renders_without_exception():
    at = _at_on_page("workspace_home")
    assert not at.exception
    text = _all_rendered_text(at)
    assert "AIOS" in text
    assert "Bank Strategy" in text
    assert "чувствительный" in text


def test_populated_state_renders_without_exception(tmp_path):
    project_config.save_repository_path("AIOS", str(tmp_path))
    (workspace_home.GENERATED_DIR / "AIOS").mkdir(parents=True)
    (workspace_home.GENERATED_DIR / "AIOS" / "abc_implementation.md").write_text("x")

    at = _at_on_page("workspace_home")
    assert not at.exception


def test_bank_legal_only_state_dual_layer_regression(tmp_path):
    """§13/§14 dual-layer regression: assert both (1) the snapshot passed to
    the renderer contains no banned field for a sensitive project, and (2)
    the rendered page text/markup contains no banned value either."""
    from command_center.runtime.api import ExecutionCenterAPI

    api = ExecutionCenterAPI(db_path=tmp_path / "runtime.db")
    task = db.create_task(api.db_path, project="BANK", title="t", task_type="implementation")
    session = db.create_session(api.db_path, task_id=task["id"], project="BANK", repository_path="/tmp/x")
    run = db.create_run(
        api.db_path,
        session_id=session["id"],
        task_id=task["id"],
        project="BANK",
        task_type="implementation",
        repository_path="/tmp/very-secret-bank-repo-path",
        prompt="THIS-IS-A-BANNED-SECRET-PROMPT",
        is_resume=False,
    )
    run = db.update_run_state(api.db_path, run["id"], expected_version=run["version"], new_state="QUEUED")
    run = db.update_run_state(
        api.db_path, run["id"], expected_version=run["version"], new_state="RUNNING",
        fields={"started_at": models.iso_now()},
    )
    db.update_run_state(
        api.db_path, run["id"], expected_version=run["version"], new_state="COMPLETED",
        fields={"completed_at": models.iso_now()},
    )

    reports_dir = workspace_home.REPORTS_DIR / "BANK"
    reports_dir.mkdir(parents=True)
    (reports_dir / "report.md").write_text(
        "NOT APPROVED FOR COMMIT\n- **Blocker:** THIS-IS-A-BANNED-FINDING-TEXT\n"
    )
    db.create_report(api.db_path, run["id"], "reports/BANK/report.md")

    generated_dir = workspace_home.GENERATED_DIR / "BANK"
    generated_dir.mkdir(parents=True)
    (generated_dir / "abc_implementation.md").write_text("secret content")

    # 1. Snapshot-level assertion, reusing the read-model's own boundary.
    snapshot = workspace_home.build_workspace_home_snapshot(execution_center_api=api)
    banned = ("THIS-IS-A-BANNED-SECRET-PROMPT", "THIS-IS-A-BANNED-FINDING-TEXT", "very-secret-bank-repo-path")
    for section in ("active_runs", "recent_runs", "reports", "artifacts", "recent_activity"):
        for entry in snapshot[section]:
            if entry.get("project") != "BANK":
                continue
            for value in entry.values():
                if isinstance(value, str):
                    for phrase in banned:
                        assert phrase not in value

    # 2. Rendering-level assertion — independent of the snapshot check above.
    at = _at_on_page("workspace_home")
    assert not at.exception
    rendered_text = _all_rendered_text(at)
    for phrase in banned:
        assert phrase not in rendered_text


# --------------------------------------------------------------------------
# Quick Actions (§11) — every action stages an existing `pending_nav` target
# and pre-fills a selector; none auto-submits a mutation.
# --------------------------------------------------------------------------


def test_quick_action_launch_run_navigates_to_execution_center_prefilled():
    at = _at_on_page("workspace_home")
    at.button(key="home_launch_BANK").click().run()

    assert at.session_state["nav_page"] == "execution_center"
    assert at.subheader[0].value == "Live Execution Center"
    assert at.selectbox(key="exec_center_launch_project").value == "BANK"
    # Landing on the launch form pre-filled must not itself start a run.
    assert not any(m.label == "Статус" for m in at.metric)


def test_quick_action_new_task_navigates_to_create_prefilled():
    at = _at_on_page("workspace_home")
    at.button(key="home_new_task_AIOS").click().run()

    assert at.session_state["nav_page"] == "create"
    assert at.selectbox(key="create_task_project").value == "AIOS"


def test_quick_action_open_project_navigates_to_projects_prefilled():
    at = _at_on_page("workspace_home")
    at.button(key="home_open_LEGAL").click().run()

    assert at.session_state["nav_page"] == "projects"
    assert at.selectbox(key="project_browser_select").value == "LEGAL"


def test_quick_action_view_all_generated_navigates_to_generated_page():
    at = _at_on_page("workspace_home")
    at.button(key="home_view_all_generated").click().run()
    assert at.session_state["nav_page"] == "generated"
    assert at.subheader[0].value == "Сгенерированные задачи"


def test_quick_action_view_all_reports_navigates_to_reports_page():
    at = _at_on_page("workspace_home")
    at.button(key="home_view_all_reports").click().run()
    assert at.session_state["nav_page"] == "reports"
    assert at.subheader[0].value == "Отчёты"


# --------------------------------------------------------------------------
# B-1 remediation — no misleading Kanban-shaped task count anywhere on the
# rendered page, in either the empty or a populated state.
# --------------------------------------------------------------------------


def test_no_task_count_wording_in_empty_state():
    at = _at_on_page("workspace_home")
    assert not at.exception
    text = _all_rendered_text(at)
    assert "Открытые задачи" not in text
    assert "Задачи:" not in text


def test_no_task_count_wording_in_populated_state(tmp_path):
    from command_center.runtime.api import ExecutionCenterAPI
    from command_center.runtime import db as runtime_db

    project_config.save_repository_path("AIOS", str(tmp_path))
    api = ExecutionCenterAPI(db_path=tmp_path / "runtime.db")
    task = runtime_db.create_task(api.db_path, project="AIOS", title="t", task_type="implementation")
    session = runtime_db.create_session(
        api.db_path, task_id=task["id"], project="AIOS", repository_path=str(tmp_path)
    )
    runtime_db.create_run(
        api.db_path,
        session_id=session["id"],
        task_id=task["id"],
        project="AIOS",
        task_type="implementation",
        repository_path=str(tmp_path),
        prompt="p",
        is_resume=False,
    )

    at = _at_on_page("workspace_home")
    assert not at.exception
    text = _all_rendered_text(at)
    assert "Открытые задачи" not in text
    assert "Задачи:" not in text
    # The rest of the page must still render normally.
    assert "AIOS" in text
    assert "Активные прогоны" in text


# --------------------------------------------------------------------------
# B-2 remediation — a malformed `repository_path` must not crash the page.
# --------------------------------------------------------------------------


def test_malformed_repository_path_does_not_crash_the_page():
    import json
    import os

    config_path = Path(os.environ["AICC_DATA_DIR"]) / "project_config.json"
    config_path.write_text(json.dumps({"AIOS": {"repository_path": 12345}}), encoding="utf-8")

    at = _at_on_page("workspace_home")
    assert not at.exception
    text = _all_rendered_text(at)
    assert "AIOS" in text
