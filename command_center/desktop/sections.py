"""Canonical navigation sections for the desktop shell.

The nine top-level sections and their D1 activation state, transcribed from
`INFORMATION_ARCHITECTURE.md` §1 (order) and §2 (which three are active in
Desktop Increment 1). This is the single place the sidebar, the page stack, and
the tests agree on — no second, drifting copy.
"""

from __future__ import annotations

from dataclasses import dataclass


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
    Section("home", "Home", enabled=True),
    Section("projects", "Projects", enabled=True),
    Section("sessions", "Sessions", enabled=False),
    Section("execution", "Execution", enabled=False),
    Section("git", "Git", enabled=False),
    Section("artifacts", "Artifacts", enabled=False),
    Section("reports", "Reports", enabled=False),
    Section("agents", "Agents", enabled=False),
    Section("settings", "Settings", enabled=True),
)

ACTIVE_SECTION_KEYS: tuple[str, ...] = tuple(s.key for s in SECTIONS if s.enabled)
DEFAULT_SECTION_KEY = "home"
