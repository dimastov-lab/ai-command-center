"""Project view consolidation (task 02661825): a project opens *inside* the
project — status, tasks, reports, context, settings and chat as tabs — and those
concerns are no longer separate sidebar entries.

Covers: the sidebar no longer lists the folded pages (their handlers stay
reachable), the project view renders a Чат tab, 'Все' shows a cross-project
overview instead of tabs, and the kept chat page delegates to the same renderer.
"""
from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from command_center.ui import sidebar

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


def _at_on_page(page_key: str) -> AppTest:
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.session_state["nav_page"] = page_key
    at.run()
    return at


def test_folded_pages_are_not_sidebar_entries():
    nav = {k: (k, "") for k in ["dashboard", "projects", "kanban", "chat", "reports", "generated", "context"]}
    shown = {key for _title, keys, _expanded in sidebar._grouped(nav) for key in keys}
    assert not ({"chat", "reports", "generated", "context"} & shown)
    # ...but the core destinations are still there.
    assert {"dashboard", "projects", "kanban"} <= shown


def test_all_option_shows_a_cross_project_overview(isolated_data_dir):
    at = _at_on_page("projects")  # default selection is "Все"
    assert not at.exception
    assert any("Обзор всех проектов" in c.value for c in at.caption)


def test_selecting_a_project_opens_it_with_a_chat_tab(isolated_data_dir):
    at = _at_on_page("projects")
    at.selectbox(key="project_browser_select").set_value("AICC").run()
    assert not at.exception
    # The Чат tab body runs render_project_chat("AICC", ...), whose conversation
    # selector is keyed by project — its presence proves the tab is wired in.
    assert any(s.key == "chat_conv_select_AICC" for s in at.selectbox)


def test_chat_page_is_kept_and_delegates(isolated_data_dir):
    at = _at_on_page("chat")
    assert not at.exception
    assert any("встроен во вкладку" in c.value for c in at.caption)
