# D3 Gate Smoke Test Record

`AICC-D3-GATE` — Projects page and Settings/Platform layer gate (D3A Projects page + adapter,
D3B Settings page + `command_center/platform/` module).

## Acceptance criteria

- D3A: `pages/projects.py` renders project list from `projects_adapter.py`; project items are
  clickable; empty state renders.
- D3B: `pages/settings_page.py` renders all setting groups; theme selection persists via
  `preferences.py` (QSettings round-trip); `command_center/platform/` module is complete.
- `platform/preferences.py` passes the QSettings theme round-trip (write in launch 1, read back
  in a fresh `SettingsStore` in launch 2).
- All `tests/desktop/` pass under `QT_QPA_PLATFORM=offscreen`.
- `ruff check .` reports zero issues.

## macOS Apple Silicon (2026-08-07)

Verified as part of the full 175-test suite run on Apple Silicon Mac.

- **`pytest tests/desktop/ -q`** → **175 passed** in 8.95s (0 failed, 0 errors).
- **`ruff check .`** → all checks passed.
- **QSettings theme persistence round-trip**: write `ThemeMode.DARK` → fresh `SettingsStore`
  reads back `DARK` — **PASS** (originally verified in D1 gate; re-confirmed with D3B in place).
- **`wc -l command_center/desktop/pages/projects.py`** → 189 lines.
- **`wc -l command_center/desktop/pages/settings_page.py`** → 209 lines.
- **`ls command_center/platform/`** → preferences.py, file_manager.py, paths.py, resources.py,
  theme.py — all present and importable.

macOS leg: **PASS**.

## Windows — automated CI leg (2026-08-07)

Executed via GitHub Actions `windows-quality-gates` job (workflow dispatch on `origin/main`,
Run ID `31150374277`). Runner: `windows-latest` = Microsoft Windows Server 2025 (10.0.26100).

| Step | Command | Result |
|------|---------|--------|
| Ruff | `ruff check .` | **All checks passed!** |
| Byte compile | `python -m compileall -q .` | exit 0 |
| pytest-qt suite | `pytest tests/desktop -q` (offscreen QPA) | **175 passed in 20.50s** |
| E2E smoke | real-browser driver | **1 passed in 6.84s** |

The 175-test suite includes D3A Projects-page widget tests and D3B Settings-page / platform-layer
tests. All pass on Windows Server 2025 with offscreen QPA.

**Automated leg: PASS.**

## Windows 11 x64 — interactive display checklist (PENDING)

The following display-dependent checks require a Windows 11 x64 machine with a real (or virtual)
display:

1. Navigate to Projects page; confirm project list renders; click a project item.
2. Navigate to Settings; confirm all setting groups render.
3. Change theme (Light/Dark/System); confirm visual change.
4. Quit and relaunch; confirm theme setting is restored.
5. Quit cleanly.

Until the interactive checklist is recorded here, `AICC-D3-GATE` remains **Review**, not **Done**.
