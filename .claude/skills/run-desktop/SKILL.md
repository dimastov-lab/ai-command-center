---
name: run-desktop
description: Use when launching, running, or smoke-testing the native PySide6 desktop app (command_center.desktop) on macOS — starting the GUI, preparing its venv, or running the desktop test suite.
---

# Run the native desktop app (macOS)

Verified cold-start recipe (2026-08, Python 3.14 arm64, PySide6 6.11.1).

## Launch

```bash
# One-time env: name .venv-desktop is the project convention
# (scripts/build-desktop-macos.sh looks for it by default)
python3.14 -m venv .venv-desktop
./.venv-desktop/bin/python -m pip install -r requirements-dev.txt

# Run FROM THE REPO ROOT (data paths resolve from CWD).
# Foreground process — background it (&) or use a separate session.
./.venv-desktop/bin/python -m command_center.desktop
```

Entry point: `command_center/desktop/__main__.py` → `app.run()`. Healthy start = window "AI Command Center", empty log, no traceback.

## Tests

```bash
# Also from the repo root (imports and tool configs resolve from it)
./.venv-desktop/bin/python -m pytest tests/desktop -q
```

Expect all green, three-digit count — 126 passed in ~8s as of 2026-08 (grows over time).

## Gotchas

- **`QT_QPA_PLATFORM` must be UNSET for a visible window.** `tests/desktop/conftest.py` uses `os.environ.setdefault(..., "offscreen")`: an inherited `offscreen` makes the GUI invisible; an empty string crashes Qt. Never export it manually.
- **Missing PySide6 = false green**: conftest `pytest.importorskip("PySide6")` silently skips the whole desktop suite. Check for `passed`, not for absence of red.
- **`AICC_DATA_DIR`** redirects runtime storage (default `./data`) — set it to keep a scratch run off real data.
- `AICC_WORKSPACE_ROOT`/`AICC_GENERATED_ROOT`/`AICC_REPORTS_ROOT` are auto-set only in the packaged `.app` (`packaging/macos/entrypoint.py`), not by `python -m`.
- Packaged builds: `./scripts/build-desktop-macos.sh` → `dist/macos/AI Command Center.app` (unsigned; first open via right-click → Open). Needs `pip install -r requirements-desktop-build.txt` (PyInstaller is NOT in requirements-dev) and an **arm64** Python — the script exits(2) otherwise; override the interpreter with `DESKTOP_PYTHON=/path/to/python`.

Windows leg: see `docs/desktop/WINDOWS_RUNBOOK.md`.
