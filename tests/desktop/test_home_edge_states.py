"""Edge states for the native Workspace Home (Desktop D2D, §15).

Covers the all-six-unconfigured empty state, per-project failure isolation
(a malformed project entry must not break the others), and the loading state
shown while an async fetch is in flight.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from command_center.desktop import i18n
from command_center.desktop.components.loading_skeleton import LoadingSkeleton
from command_center.desktop.components.status_badge import StatusVariant
from command_center.desktop.pages.home import HomePage


def _unconfigured(pid: str) -> dict:
    return {
        "id": pid, "display_name": pid, "sensitive": False,
        "repository_path": None, "repository_state": "unconfigured",
        "task_count": 0, "active_run_count": 0,
    }


_ALL_UNCONFIGURED = {
    "projects": [_unconfigured(p) for p in ("AIOS", "AICOS", "BANK", "LEGAL", "BUSINESS", "PERSONAL")],
    "worktrees_by_project": {},
    "active_runs": [], "recent_runs": [], "recent_activity": [], "artifacts": [], "reports": [],
}


def test_all_unconfigured_shows_six_neutral_cards_and_empty_sections(qtbot):
    page = HomePage()
    qtbot.addWidget(page)
    page.render_snapshot(_ALL_UNCONFIGURED)

    cards = page.project_cards()
    assert len(cards) == 6
    assert all(c.health_badge.variant is StatusVariant.NEUTRAL for c in cards)
    assert all(c.configure_button is not None for c in cards)

    empty_labels = [lbl.text() for lbl in page.findChildren(QLabel) if lbl.text() == i18n.HOME_SECTION_EMPTY]
    assert len(empty_labels) >= 1  # the empty run/artifact/report/activity sections say so


def test_per_project_failure_isolation_tolerates_missing_fields(qtbot):
    snapshot = {
        "projects": [{"id": "AIOS"}, _unconfigured("BANK")],  # AIOS missing optional keys
        "worktrees_by_project": {},
        "active_runs": [], "recent_runs": [], "recent_activity": [], "artifacts": [], "reports": [],
    }
    page = HomePage()
    qtbot.addWidget(page)
    page.render_snapshot(snapshot)  # must not raise
    assert [c.project_id for c in page.project_cards()] == ["AIOS", "BANK"]


def test_loading_state_is_shown_until_the_snapshot_arrives(qtbot):
    release = threading.Event()

    class _SlowAdapter:
        def snapshot(self, **_kwargs):
            release.wait(2.0)
            return _ALL_UNCONFIGURED

    page = HomePage()
    qtbot.addWidget(page)
    runnable = page.load(_SlowAdapter())
    assert page.is_loading() is True
    assert page.project_cards() == []
    skeleton = page.loading_skeleton()
    assert isinstance(skeleton, LoadingSkeleton)
    assert skeleton.focusPolicy() == Qt.NoFocus
    assert skeleton.property("busy") is True
    assert skeleton.accessibleName() == i18n.HOME_LOADING
    assert skeleton.accessibleDescription() == i18n.HOME_LOADING_ACCESSIBLE_DESCRIPTION
    assert skeleton.row_heights() == [40] * 5

    with qtbot.waitSignal(runnable.signals.finished, timeout=2000):
        release.set()
    qtbot.waitUntil(lambda: not page.is_loading(), timeout=2000)
    assert page.loading_skeleton() is None
    assert page.findChild(LoadingSkeleton) is None
    assert len(page.project_cards()) == 6
