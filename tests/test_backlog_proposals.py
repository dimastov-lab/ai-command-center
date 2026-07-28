"""Candidate-task proposals from an agent report
(`command_center.ui.backlog_proposals`) and the project Audit button (d58b56f3).

Covers the pure parser (section detection, bullet shapes, dedupe, no-section),
the render action (Применить creates a Backlog task), and the Audit tab wiring
(the button creates and enqueues a read-only architecture_review task).
"""
from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from command_center import tasks_repository
from command_center.ui import backlog_proposals as bp

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


# --------------------------------------------------------------------------
# parse_candidate_tasks
# --------------------------------------------------------------------------


def test_parses_titles_and_goals_from_a_proposals_section():
    md = (
        "# Отчёт аудита\nВводный текст.\n\n"
        "## Предлагаемые задачи\n"
        "- **Вынести валидацию** — дублируется в трёх местах\n"
        "- Добавить тесты для парсера: покрытие почти нулевое\n"
        "1) Просто заголовок\n\n"
        "## Прочее\n- это не должно попасть\n"
    )
    candidates = bp.parse_candidate_tasks(md)
    titles = [c.title for c in candidates]
    assert "Вынести валидацию" in titles
    assert "Добавить тесты для парсера" in titles
    assert "Просто заголовок" in titles
    assert all("не должно попасть" not in t for t in titles)
    validation = next(c for c in candidates if c.title == "Вынести валидацию")
    assert "дублируется" in validation.goal


def test_no_proposals_section_returns_empty():
    assert bp.parse_candidate_tasks("# Report\nProse only, no list of work.") == []


def test_candidates_are_deduped_by_title():
    md = "## Задачи\n- Alpha — первый вариант\n- Alpha — второй вариант\n"
    assert len(bp.parse_candidate_tasks(md)) == 1


# --------------------------------------------------------------------------
# render_candidate_tasks
# --------------------------------------------------------------------------


def _render_script() -> None:
    import os
    from pathlib import Path

    from command_center.ui import backlog_proposals as bp

    root = Path(os.environ["AICC_DATA_DIR"])
    candidates = [
        bp.CandidateTask("Вынести валидацию", "дублируется в трёх местах"),
        bp.CandidateTask("Добавить тесты", "нет покрытия"),
    ]
    bp.render_candidate_tasks(candidates, root, "AICC", key_prefix="t")


def test_applying_a_candidate_creates_a_backlog_task(isolated_data_dir):
    at = AppTest.from_function(_render_script, default_timeout=30).run()
    at.button(key="t_apply_0").click().run()
    tasks = tasks_repository.load_tasks(isolated_data_dir)
    assert any(t["title"] == "Вынести валидацию" and t["status"] == "Backlog" for t in tasks)


def test_rejecting_a_candidate_creates_nothing(isolated_data_dir):
    at = AppTest.from_function(_render_script, default_timeout=30).run()
    at.button(key="t_skip_0").click().run()
    tasks = tasks_repository.load_tasks(isolated_data_dir)
    assert not any(t["title"] == "Вынести валидацию" for t in tasks)


# --------------------------------------------------------------------------
# Audit tab button
# --------------------------------------------------------------------------


def test_audit_button_creates_and_enqueues_a_readonly_audit_task(isolated_data_dir):
    at = AppTest.from_file(APP_PATH, default_timeout=40)
    at.session_state["nav_page"] = "projects"
    at.run()
    at.selectbox(key="project_browser_select").set_value("AICC").run()
    at.button(key="proj_audit_run_AICC").click().run()
    assert not at.exception
    tasks = tasks_repository.load_tasks(isolated_data_dir)
    audit = [t for t in tasks if t.get("task_type") == "architecture_review" and t.get("project") == "AICC"]
    assert audit, "audit task was not created"
    assert audit[0].get("status") == "Next"
