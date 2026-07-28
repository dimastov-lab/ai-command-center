"""AI Command Center — native desktop shell (PySide6 / Qt Widgets).

This is the presentation layer described by ``docs/desktop/ARCHITECTURE.md``. It
depends only on ``PySide6`` and the Python standard library; it imports nothing
from ``app.py``/Streamlit and starts no HTTP listener or browser
(`docs/desktop/ARCHITECTURE.md` §8.1). The data-adapter layer that bridges to the existing
``command_center`` core (``command_center.application``) lands in a later
increment; this package is a pure shell.

Importing this package has no side effects — in particular it constructs no
``QApplication``. Launch the app with ``python -m command_center.desktop``.
"""

from __future__ import annotations

__all__ = ["run", "build_shell"]


def run(argv: list[str] | None = None) -> int:
    """Launch the desktop application. Imported lazily so importing the package
    stays free of Qt construction side effects."""
    from .app import run as _run

    return _run(argv)


def build_shell(app, settings=None):
    """Wire the shell against an existing ``QApplication`` (see
    :func:`command_center.desktop.app.build_shell`). Lazy import, as above."""
    from .app import build_shell as _build_shell

    return _build_shell(app, settings)
