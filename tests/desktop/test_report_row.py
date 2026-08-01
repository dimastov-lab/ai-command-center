"""Tests for `command_center/desktop/components/report_row.py` (Desktop D2C).

`ReportRow` renders one report entry (`WORKSPACE_HOME_SPEC.md` §8): project +
created_at + a verdict `StatusBadge`. Verdict variant/label reuse the canonical
`command_center.models` verdict registry (already Russian) so the two never drift.
An unmatched report (``run_id is None``) renders without a verdict badge rather
than a misleading blank one.
"""

from __future__ import annotations

from command_center import models
from command_center.desktop import i18n, tokens
from command_center.desktop.components.report_row import ReportRow, verdict_badge
from command_center.desktop.components.status_badge import StatusVariant


def test_verdict_badge_reuses_models_labels_and_passing_semantics():
    passing = models.VERDICT_APPROVED_FOR_COMMIT
    assert verdict_badge(passing) == (StatusVariant.SUCCESS, models.VERDICT_LABELS[passing])
    assert verdict_badge(models.VERDICT_FAILED) == (
        StatusVariant.DANGER,
        models.VERDICT_LABELS[models.VERDICT_FAILED],
    )


def test_report_row_matched_shows_verdict_badge(qtbot):
    row = ReportRow(
        {"run_id": "r1", "source": "v2", "project": "AIOS", "verdict": models.VERDICT_FAILED,
         "created_at": 1730000000}
    )
    qtbot.addWidget(row)
    assert row.verdict_badge_widget is not None
    assert row.verdict_badge_widget.variant is StatusVariant.DANGER


def test_report_row_unmatched_has_no_verdict_badge(qtbot):
    # run_id is None → unmatched report file: no misleading blank verdict badge.
    row = ReportRow({"run_id": None, "source": None, "project": "AIOS", "created_at": 1730000000})
    qtbot.addWidget(row)
    assert row.verdict_badge_widget is None
    assert i18n.REPORT_UNMATCHED_NOTE in row.summary_text()


def test_report_row_apply_palette_propagates_to_verdict_badge(qtbot):
    row = ReportRow({"run_id": "r1", "project": "AIOS", "verdict": models.VERDICT_APPROVED_FOR_COMMIT})
    qtbot.addWidget(row)
    row.apply_palette(tokens.DARK)
    assert tokens.DARK.status_success in row.verdict_badge_widget.styleSheet()
