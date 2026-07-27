"""Roadmap reformat button and duplicate filtering (task 63633657).

The reformat button reuses the shared candidate pipeline (backlog_proposals);
what is specific here is `filter_new_candidates` — a reformatted roadmap must not
re-propose work that already exists as a task — and the button wiring that
creates and enqueues the rebuild task.
"""
from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from command_center import tasks_repository
from command_center.ui import backlog_proposals as bp

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


def test_filter_drops_candidates_that_match_existing_tasks():
    existing = [
        {"title": "Вынести валидацию в отдельный модуль", "goal": "убрать дублирование", "status": "Done"},
    ]
    candidates = [
        bp.CandidateTask("Вынести валидацию в отдельный модуль", "убрать дублирование"),  # duplicate
        bp.CandidateTask("Совершенно новая функциональность отчётов", "то, чего ещё нет"),  # fresh
    ]
    fresh = bp.filter_new_candidates(candidates, existing)
    titles = [c.title for c in fresh]
    assert "Совершенно новая функциональность отчётов" in titles
    assert "Вынести валидацию в отдельный модуль" not in titles


def test_filter_keeps_everything_when_nothing_matches():
    candidates = [bp.CandidateTask("Уникальная задача", "новое")]
    assert bp.filter_new_candidates(candidates, []) == candidates


def test_roadmap_button_creates_and_enqueues_a_reformat_task(isolated_data_dir):
    at = AppTest.from_file(APP_PATH, default_timeout=40)
    at.session_state["nav_page"] = "projects"
    at.run()
    at.selectbox(key="project_browser_select").set_value("AICC").run()
    at.text_area(key="proj_roadmap_wishes_AICC").set_value("надёжность пайплайна и UX").run()
    at.button(key="proj_roadmap_run_AICC").click().run()
    assert not at.exception
    tasks = tasks_repository.load_tasks(isolated_data_dir)
    reformat = [t for t in tasks if t.get("project") == "AICC" and "Roadmap" in (t.get("title") or "")]
    assert reformat, "roadmap reformat task was not created"
    assert reformat[0].get("status") == "Next"
