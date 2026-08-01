"""Tests for `command_center/desktop/components/status_badge.py` (Desktop D2C).

`StatusBadge` is the shared status primitive (`DESIGN_SYSTEM.md` §7.9): a small
pill whose colour comes from the current palette's semantic ``status_*`` token for
its variant, so it re-themes with everything else. The variant→colour mapping is a
pure function, unit-testable without a widget.
"""

from __future__ import annotations

import pytest

from command_center.desktop import tokens
from command_center.desktop.components.status_badge import (
    StatusBadge,
    StatusVariant,
    status_color,
)


@pytest.mark.parametrize(
    "variant,attr",
    [
        (StatusVariant.NEUTRAL, "status_neutral"),
        (StatusVariant.INFO, "status_info"),
        (StatusVariant.ACTIVE, "status_active"),
        (StatusVariant.SUCCESS, "status_success"),
        (StatusVariant.WARNING, "status_warning"),
        (StatusVariant.DANGER, "status_danger"),
        (StatusVariant.CANCELLED, "status_cancelled"),
        (StatusVariant.SENSITIVE, "status_sensitive"),
    ],
)
def test_status_color_maps_each_variant_to_its_palette_token(variant, attr):
    assert status_color(variant, tokens.LIGHT) == getattr(tokens.LIGHT, attr)
    assert status_color(variant, tokens.DARK) == getattr(tokens.DARK, attr)


def test_badge_exposes_its_text_and_variant(qtbot):
    badge = StatusBadge("Настроен", StatusVariant.SUCCESS)
    qtbot.addWidget(badge)
    assert badge.text() == "Настроен"
    assert badge.variant is StatusVariant.SUCCESS
    # A screen reader gets the same label sighted users see.
    assert badge.accessibleName() == "Настроен"


def test_badge_colours_itself_from_the_applied_palette(qtbot):
    badge = StatusBadge("Настроен", StatusVariant.SUCCESS)
    qtbot.addWidget(badge)
    badge.apply_palette(tokens.DARK)
    assert tokens.DARK.status_success in badge.styleSheet()


def test_badge_recolours_when_variant_changes(qtbot):
    badge = StatusBadge("Ожидание", StatusVariant.NEUTRAL)
    qtbot.addWidget(badge)
    badge.apply_palette(tokens.LIGHT)
    badge.set_variant(StatusVariant.WARNING)
    assert badge.variant is StatusVariant.WARNING
    assert tokens.LIGHT.status_warning in badge.styleSheet()
