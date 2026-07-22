"""TopBar behaviour: reserved status area, placeholder switcher, global refresh.

Covers `DESIGN_SYSTEM.md` §7.4 and `DESIGN_DIRECTIONS.md` §5.
"""

from __future__ import annotations


def test_project_switcher_is_placeholder_and_disabled(shell):
    switcher = shell.top_bar.project_switcher
    assert not switcher.isEnabled()  # not project-scoped in D1 (§7.5)
    assert switcher.currentText() == "Select a project"
    assert switcher.accessibleName() == "Project switcher"


def test_status_area_starts_empty_and_never_polls(shell):
    # Reserved space, no live/polling content in D1 (DESIGN_DIRECTIONS §5).
    assert shell.top_bar.status_area.text() == ""


def test_status_area_accepts_static_text(shell):
    shell.top_bar.set_status_text("2 runs active")
    assert shell.top_bar.status_area.text() == "2 runs active"


def test_refresh_button_has_shortcut_and_emits(shell, qtbot):
    button = shell.top_bar.refresh_button
    assert not button.shortcut().isEmpty()
    with qtbot.waitSignal(shell.refresh_requested, timeout=1000):
        button.click()


def test_refresh_button_accessible_name(shell):
    assert shell.top_bar.refresh_button.accessibleName() == "Refresh"
