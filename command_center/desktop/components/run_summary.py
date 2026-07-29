"""``RunSummary`` — one run row on Workspace Home (D2C, §5/§6).

Shows project + task_type + a state `StatusBadge`. A run's identity is the
composite ``(source, run_id)`` key (never bare ``run_id``), matching the read
model. All fields are read defensively so a BANK/LEGAL entry — carrying only the
allowlisted run fields — renders without error and without a state badge if the
state field is absent.
"""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from .. import i18n, tokens
from ..tokens import Palette
from .status_badge import StatusBadge, StatusVariant

_RUN_STATE_VARIANTS: dict[str, StatusVariant] = {
    "RUNNING": StatusVariant.ACTIVE,
    "PREPARED": StatusVariant.INFO,
    "QUEUED": StatusVariant.INFO,
    "COMPLETED": StatusVariant.SUCCESS,
    "FAILED": StatusVariant.DANGER,
    "CANCELLED": StatusVariant.CANCELLED,
    "INTERRUPTED": StatusVariant.WARNING,
    "UNKNOWN": StatusVariant.NEUTRAL,
}


def run_state_badge(state: str | None) -> tuple[StatusVariant, str]:
    """Map a run ``state`` to its badge variant and Russian label (§5)."""
    return _RUN_STATE_VARIANTS.get(state, StatusVariant.NEUTRAL), i18n.RUN_STATE_LABELS.get(
        state, state or ""
    )


class RunSummary(QFrame):
    def __init__(self, run: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RunSummary")
        self.run_key: tuple = (run.get("source"), run.get("run_id"))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            tokens.SPACE_MD, tokens.SPACE_XS, tokens.SPACE_MD, tokens.SPACE_XS
        )
        layout.setSpacing(tokens.SPACE_MD)

        parts = [str(p) for p in (run.get("project"), run.get("task_type")) if p]
        self._title = QLabel(" · ".join(parts))
        self._title.setObjectName("RowTitle")
        layout.addWidget(self._title)
        layout.addStretch(1)

        self.state_badge: StatusBadge | None = None
        state = run.get("state")
        if state:
            variant, label = run_state_badge(state)
            self.state_badge = StatusBadge(label, variant)
            layout.addWidget(self.state_badge)

    def title_text(self) -> str:
        return self._title.text()

    def apply_palette(self, palette: Palette) -> None:
        if self.state_badge is not None:
            self.state_badge.apply_palette(palette)
