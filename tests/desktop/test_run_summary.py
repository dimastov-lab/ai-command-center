"""Tests for `command_center/desktop/components/run_summary.py` (Desktop D2C).

`RunSummary` renders a run row (`WORKSPACE_HOME_SPEC.md` §5/§6): project +
task_type + a state `StatusBadge` (RUNNING→active, PREPARED/QUEUED→info, terminal
states mapped by outcome). A run's identity is the composite ``(source, run_id)``
key, never bare ``run_id``. Every field is read defensively so a BANK/LEGAL entry
carrying only allowlisted fields renders without error.
"""

from __future__ import annotations

from command_center.desktop import i18n, tokens
from command_center.desktop.components.run_summary import RunSummary, run_state_badge
from command_center.desktop.components.status_badge import StatusVariant


def test_run_state_badge_maps_states_to_variant_and_label():
    assert run_state_badge("RUNNING") == (StatusVariant.ACTIVE, i18n.RUN_STATE_LABELS["RUNNING"])
    assert run_state_badge("QUEUED") == (StatusVariant.INFO, i18n.RUN_STATE_LABELS["QUEUED"])
    assert run_state_badge("PREPARED") == (StatusVariant.INFO, i18n.RUN_STATE_LABELS["PREPARED"])
    assert run_state_badge("COMPLETED") == (StatusVariant.SUCCESS, i18n.RUN_STATE_LABELS["COMPLETED"])
    assert run_state_badge("FAILED") == (StatusVariant.DANGER, i18n.RUN_STATE_LABELS["FAILED"])


def test_run_summary_uses_composite_source_run_id_identity(qtbot):
    row = RunSummary({"source": "v2", "run_id": "r1", "project": "AIOS", "state": "RUNNING"})
    qtbot.addWidget(row)
    assert row.run_key == ("v2", "r1")


def test_run_summary_shows_state_badge(qtbot):
    row = RunSummary({"source": "v2", "run_id": "r1", "project": "AIOS", "state": "RUNNING"})
    qtbot.addWidget(row)
    assert row.state_badge is not None
    assert row.state_badge.variant is StatusVariant.ACTIVE
    assert row.state_badge.text() == i18n.RUN_STATE_LABELS["RUNNING"]


def test_run_summary_without_state_has_no_badge(qtbot):
    row = RunSummary({"source": "v1.2", "run_id": "r2"})
    qtbot.addWidget(row)
    assert row.state_badge is None  # no state → no badge, no crash


def test_run_summary_apply_palette_propagates_to_badge(qtbot):
    row = RunSummary({"source": "v2", "run_id": "r1", "state": "FAILED"})
    qtbot.addWidget(row)
    row.apply_palette(tokens.DARK)
    assert tokens.DARK.status_danger in row.state_badge.styleSheet()
