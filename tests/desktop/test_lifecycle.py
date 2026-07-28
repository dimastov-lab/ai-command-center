"""Application-lifecycle guarantees: no forbidden imports, single QApplication,
bounded clean shutdown.

Covers `ARCHITECTURE.md` §3/§8.1/§13 and `DESKTOP_INCREMENT_1.md` §2's
"no import of app.py/streamlit/HTTP/browser on the startup path" criterion.
"""

from __future__ import annotations

import os
import subprocess
import sys

from PySide6.QtWidgets import QApplication

_STARTUP_IMPORT_PROBE = """
import sys
import command_center.desktop
from command_center.desktop import (
    app, main_window, theme, settings, sidebar, top_bar, navigation_item,
)
bad = [
    m for m in sys.modules
    if m == "streamlit" or m.startswith("streamlit.")
    or m in {"flask", "fastapi", "http.server", "webbrowser"}
]
assert not bad, bad
from PySide6.QtWidgets import QApplication
assert QApplication.instance() is None, "QApplication constructed at import time"
print("ok")
"""


def test_importing_desktop_pulls_no_streamlit_or_http():
    # Run in a clean interpreter so another test having imported streamlit
    # cannot mask a real leak in the desktop startup path.
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    result = subprocess.run(
        [sys.executable, "-c", _STARTUP_IMPORT_PROBE],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_single_qapplication_reused(qapp):
    # build_shell must never construct a second QApplication; it uses the one
    # passed in (pytest-qt's qapp here).
    assert QApplication.instance() is qapp


def test_shutdown_is_bounded_and_persists(shell, settings_store):
    # No background workers in D1 → the pool drains immediately, well within bound.
    drained = shell.shutdown(timeout_ms=2000)
    assert drained is True
    # Shutdown persisted geometry (a non-empty saved blob).
    assert settings_store.geometry() is not None


def test_close_event_triggers_clean_shutdown(shell, settings_store):
    assert not shell.cancel_event.is_set()
    shell.close()
    # Cooperative-cancel flag set for any future workers; state persisted.
    assert shell.cancel_event.is_set()
    assert settings_store.geometry() is not None
