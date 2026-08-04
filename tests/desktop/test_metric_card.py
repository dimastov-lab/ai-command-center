"""Tests for `command_center/desktop/components/metric_card.py` (Desktop D2C).

`MetricCard` is one tile of the Workspace Home header strip (`WORKSPACE_HOME_SPEC.md`
§1): a large value over a caption label. It is generic — the Home page supplies
the (already-Russian) caption and the aggregate value.
"""

from __future__ import annotations

from command_center.desktop.components.metric_card import MetricCard


def test_metric_card_shows_value_and_label(qtbot):
    card = MetricCard(6, "Проекты")
    qtbot.addWidget(card)
    assert card.value_text() == "6"
    assert card.label_text() == "Проекты"


def test_metric_card_coerces_value_to_text(qtbot):
    card = MetricCard(0, "Отчёты")
    qtbot.addWidget(card)
    assert card.value_text() == "0"


def test_metric_card_accessible_name_combines_label_and_value(qtbot):
    card = MetricCard(2, "Активные запуски")
    qtbot.addWidget(card)
    name = card.accessibleName()
    assert "Активные запуски" in name
    assert "2" in name
