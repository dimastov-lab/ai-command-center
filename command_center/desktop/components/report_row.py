"""``ReportRow`` — one report entry on Workspace Home (D2C, §8).

Shows project + created_at + a verdict `StatusBadge`. The verdict variant and
label reuse the canonical `command_center.models` verdict registry (already
Russian) via :func:`verdict_badge`, so the desktop page never becomes a second
source of truth for verdict semantics. An unmatched report (``run_id is None``)
renders without a verdict badge rather than a misleading blank one.
"""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from command_center import models

from .. import i18n, tokens
from ..tokens import Palette
from .status_badge import StatusBadge, StatusVariant


def verdict_badge(verdict: str | None) -> tuple[StatusVariant, str]:
    """Map a report ``verdict`` to its badge variant and (Russian) label.

    Reuses ``models.is_passing_verdict`` and ``models.VERDICT_LABELS`` so this
    never drifts from the canonical verdict registry.
    """
    if models.is_passing_verdict(verdict):
        return StatusVariant.SUCCESS, models.VERDICT_LABELS.get(verdict, verdict)
    if verdict in models.VERDICT_LABELS:
        return StatusVariant.DANGER, models.VERDICT_LABELS[verdict]
    return StatusVariant.NEUTRAL, (verdict or "")


class ReportRow(QFrame):
    def __init__(self, report: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ReportRow")
        run_id = report.get("run_id")
        verdict = report.get("verdict")
        self.is_unmatched: bool = run_id is None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            tokens.SPACE_MD, tokens.SPACE_XS, tokens.SPACE_MD, tokens.SPACE_XS
        )
        layout.setSpacing(tokens.SPACE_MD)

        summary_parts = [
            str(p)
            for p in (report.get("project"), report.get("created_at"))
            if p not in (None, "")
        ]
        if self.is_unmatched:
            summary_parts.append(i18n.REPORT_UNMATCHED_NOTE)
        self._summary = QLabel(" · ".join(summary_parts))
        self._summary.setObjectName("RowTitle")
        layout.addWidget(self._summary)
        layout.addStretch(1)

        self.verdict_badge_widget: StatusBadge | None = None
        if not self.is_unmatched and verdict is not None:
            variant, label = verdict_badge(verdict)
            self.verdict_badge_widget = StatusBadge(label, variant)
            layout.addWidget(self.verdict_badge_widget)

    def summary_text(self) -> str:
        return self._summary.text()

    def apply_palette(self, palette: Palette) -> None:
        if self.verdict_badge_widget is not None:
            self.verdict_badge_widget.apply_palette(palette)
