"""Canonical navigation sections for the desktop shell.

The nine top-level sections and their D1 activation state, transcribed from
`INFORMATION_ARCHITECTURE.md` §1 (order) and §2 (which three are active in
Desktop Increment 1). This is the single place the sidebar, the page stack, and
the tests agree on — no second, drifting copy.
"""

from __future__ import annotations

from dataclasses import dataclass

from .i18n import SECTION_LABELS


@dataclass(frozen=True)
class Section:
    key: str
    label: str
    enabled: bool  # False → rendered disabled ("Available in a future release")


# Order is binding (`INFORMATION_ARCHITECTURE.md` §1). Home / Projects / Settings
# are the three sections active in Desktop Increment 1 (§2); the other six are
# rendered visibly disabled rather than hidden, so the sidebar never reflows
# between increments (§2.1).
SECTIONS: tuple[Section, ...] = (
    Section("home", SECTION_LABELS["home"], enabled=True),
    Section("projects", SECTION_LABELS["projects"], enabled=True),
    Section("sessions", SECTION_LABELS["sessions"], enabled=False),
    Section("execution", SECTION_LABELS["execution"], enabled=False),
    Section("git", SECTION_LABELS["git"], enabled=False),
    Section("artifacts", SECTION_LABELS["artifacts"], enabled=False),
    Section("reports", SECTION_LABELS["reports"], enabled=False),
    Section("agents", SECTION_LABELS["agents"], enabled=False),
    Section("settings", SECTION_LABELS["settings"], enabled=True),
)

ACTIVE_SECTION_KEYS: tuple[str, ...] = tuple(s.key for s in SECTIONS if s.enabled)
DEFAULT_SECTION_KEY = "home"
