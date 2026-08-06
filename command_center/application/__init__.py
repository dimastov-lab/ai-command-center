"""Application layer — GUI-agnostic adapters over `command_center` read models.

This package sits between the plain-Python read models / runtime and the desktop
presentation layer (`command_center.desktop`). Per `docs/desktop/ARCHITECTURE.md`
§3 and §5 it must not import Qt: it stays importable and testable under plain
`pytest` with no `QApplication`, so the presentation layer can depend on it while
the read models stay unaware of any UI.
"""

from __future__ import annotations
