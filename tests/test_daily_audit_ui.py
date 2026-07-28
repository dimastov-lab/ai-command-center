from pathlib import Path

from streamlit.testing.v1 import AppTest

from command_center.ui import daily_audit_panel

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def test_daily_audit_deep_link_renders_but_is_not_duplicated_in_navigation():
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.session_state["nav_page"] = "daily_audit"
    at.run()

    assert not at.exception
    assert at.subheader[0].value == "Ежедневный аудит"
    assert not any(button.key == "nav_btn_daily_audit" for button in at.sidebar.button)
    assert any("Запустить аудит сейчас" in button.label for button in at.button)


def test_launch_agent_status_is_portable_when_launchctl_is_absent(monkeypatch):
    monkeypatch.setattr(daily_audit_panel.shutil, "which", lambda _: None)
    assert daily_audit_panel.launch_agent_status() == (False, "launchd недоступен")
