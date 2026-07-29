"""Tests for `command_center/desktop/components/activity_item.py` (Desktop D2C).

`ActivityItem` renders one recent-activity event (`WORKSPACE_HOME_SPEC.md` §6):
a Russian event description + project + timestamp, read defensively.
"""

from __future__ import annotations

from command_center.desktop import i18n
from command_center.desktop.components.activity_item import ActivityItem, activity_label


def test_activity_label_maps_known_events_to_russian():
    assert activity_label("run_completed") == i18n.ACTIVITY_EVENT_LABELS["run_completed"]
    assert activity_label("run_failed") == i18n.ACTIVITY_EVENT_LABELS["run_failed"]


def test_activity_label_does_not_expose_raw_event_type():
    assert activity_label("some_future_event") == i18n.UNKNOWN_ACTIVITY_EVENT


def test_activity_item_shows_description_project_and_timestamp(qtbot):
    item = ActivityItem(
        {"event_type": "run_completed", "project": "AIOS", "ts": "2026-07-29T10:00:00"}
    )
    qtbot.addWidget(item)
    assert item.description_text() == i18n.ACTIVITY_EVENT_LABELS["run_completed"]
    assert "AIOS" in item.detail_text()
    assert "2026-07-29T10:00:00" in item.detail_text()


def test_activity_item_tolerates_missing_fields(qtbot):
    item = ActivityItem({})
    qtbot.addWidget(item)
    assert item.description_text() == ""
