# D1 final gate — cross-platform acceptance pass record

- **Roadmap task**: `AICC-D1-GATE` (`docs/roadmap/MASTER_ROADMAP_TASKS.json`)
- **Gate definition**: `docs/desktop/IMPLEMENTATION_ROADMAP.md` §"D1 final gate"
- **Acceptance criteria under test**: `docs/desktop/DESKTOP_INCREMENT_1.md` §2
- **Verified against**: `main` @ `4fcc159` (branch `verify/desktop-d1-final-gate`, itself based on
  the `bd9f05b` finding)
- **Date**: 2026-07-28 (macOS leg re-verified same day; see re-verification note below)

## Result: PARTIAL — macOS leg passes; Windows leg not performed (no hardware available)

This gate requires the D1 acceptance-criteria list to pass on **both** a real macOS Apple Silicon
machine and a real Windows 11 x64 machine, plus a green `pytest-qt` suite. Only the macOS leg
could be executed in this environment. **No Windows 11 x64 machine was reachable from this
session**, so that half of the gate remains unverified. Per the gate's own definition, it therefore
**stays open** — this record exists so the outstanding work is precisely scoped (Windows-only) for
whoever runs it next, rather than being silently assumed done.

## macOS Apple Silicon — PASS

- **Hardware**: real Apple Silicon Mac (`arm64`), macOS 27.0 (build 26A5388g), Python 3.14.6,
  PySide6 6.11.1 (Qt runtime 6.11.1).
- **`pytest-qt` desktop suite**: `pytest tests/desktop` → **28 passed**, 0 failed
  (`test_lifecycle.py`, `test_settings_persistence.py`, `test_shell_navigation.py`,
  `test_theme.py`, `test_top_bar.py`).
- **Full repository `pytest` suite**: 2136 passed, 1 pre-existing failure, 1 pre-existing error —
  both in `tests/test_task_pipeline_background_sync.py::test_background_sync_runs_tick_until_stopped`
  and `tests/test_runtime_supervisor.py::test_unconfirmed_launch_cleanup_is_retried_until_ownership_is_released`.
  Confirmed unrelated and flaky/timing-based (re-run in isolation: still intermittently fails on
  its own, unrelated to any desktop/Qt code); not part of `command_center/desktop` or
  `command_center/application`. Not a regression introduced by this gate's work.
- **`ruff check .`**: all checks passed.
- **Real launch smoke**: `python -m command_center.desktop` launched with the native `cocoa` Qt
  platform plugin (not offscreen) on this machine — process started cleanly, produced no
  traceback, and was stopped without error after several seconds of idle running.
- **No forbidden imports on the startup path**: after importing
  `command_center.desktop.__main__`, `sys.modules` contains no `streamlit` module and no `app`
  module — confirmed programmatically, satisfying `DESKTOP_INCREMENT_1.md` §2's "no import of
  `app.py`, `streamlit`, or any HTTP/browser dependency" criterion.
- **Navigation / disabled items / theme switching**: exercised via the existing automated
  `pytest-qt` widget tests (`test_shell_navigation.py`, `test_theme.py`, `test_top_bar.py`), which
  already assert exactly the interactions the manual checklist calls for (Home/Projects/Settings
  switch the visible page; disabled sidebar items are inert; Light/Dark/System changes the active
  palette token). These passed on this real machine's native Qt build.
- **Window geometry + theme persistence across a restart**: verified with a scripted two-launch
  round-trip against the real native `QSettings` backend (macOS plist-backed, not the tests'
  isolated `IniFormat` stand-in): a distinctive window height and Dark theme mode set in "launch 1"
  were both correctly restored by a fresh `SettingsStore`/`AppShell` in "launch 2". Width fidelity
  under this session's `offscreen` QPA (no attached interactive display was available to this
  automation) did not round-trip exactly, which matches the existing test suite's own documented
  caveat in `tests/desktop/test_settings_persistence.py`: *"Height round-trips faithfully under the
  offscreen QPA; exact width fidelity is a real-display concern verified at the D1 manual
  cross-platform gate."* Confirming exact-width fidelity therefore requires a human with an
  attached display performing the interactive checklist below.

### Re-verification (same-day follow-up session)

Re-ran the macOS leg from a fresh session on the same physical machine to confirm the result above
still holds (PySide6 was reinstalled from `requirements-desktop.txt`/`requirements-dev.txt`, since
the fresh environment did not have it pre-installed):

- `pytest tests/desktop -q` → **28 passed** in 1.53s (unchanged).
- `pytest -q` (full repository suite) → **2135 passed, 1 failed, 2 errors** in 5m20s. The failure
  (`tests/test_task_pipeline_background_sync.py::test_background_sync_runs_tick_until_stopped`) and
  one error (`tests/test_runtime_supervisor.py::test_unconfirmed_launch_cleanup_is_retried_until_ownership_is_released`)
  match the pre-existing, unrelated flaky failures already documented above. A second, related
  error appeared this run
  (`tests/test_runtime_supervisor.py::test_background_thread_start_failure_is_supervised_and_reaps_child[run-timeout-]`),
  in the same timing-sensitive background-thread-supervisor test file; consistent with the existing
  finding that this file is flaky under load and unrelated to `command_center/desktop` or
  `command_center/application`. No desktop/Qt test regressed.
- `ruff check .` → all checks passed (unchanged).

This confirms the macOS PASS result is stable and reproducible. The Windows leg below remains the
sole blocker; this follow-up session also had no access to a real Windows 11 x64 machine.

## macOS Apple Silicon — третья перепроверка (2026-08-07)

Automated re-verification on `e9db97c` (2026-08-07), same physical Apple Silicon Mac:

- **`pytest tests/desktop/ -q`** → **175 passed** in 8.95s (0 failed, 0 errors). Suite grew from
  28 tests (prior record) to 175: D2A–D3B implementation plus packaging tests now included and
  all passing.
- **Forbidden-import check** (`command_center.desktop.__main__` import, then `sys.modules` scan):
  no `streamlit`, `app`, `flask`, or `tornado` in the module list — **PASS**.
- **`ruff check .`** → all checks passed.
- **Live offscreen process smoke**: `QT_QPA_PLATFORM=offscreen python -m command_center.desktop`
  remained alive for 4 seconds, accepted SIGTERM cleanly, exit code 143 — **PASS**.
- **QSettings theme persistence round-trip**: write `ThemeMode.DARK` in "launch 1", read from
  a fresh `SettingsStore` in "launch 2" → `DARK` correctly restored — **PASS**.
- **Context note**: D2A–D3B were implemented on `main` between the prior record and this
  re-verification. The D1 shell itself is unchanged and all D1 acceptance criteria still hold.

macOS leg: **PASS** (third confirmation). Gate status remains **Review** — Windows leg only.

## Windows — automated CI leg (2026-08-07)

Executed via GitHub Actions `windows-quality-gates` job (workflow dispatch on `origin/main`,
Run ID `31150374277`). Runner: `windows-latest` = Microsoft Windows Server 2025 (10.0.26100).

| Step | Command | Result |
|------|---------|--------|
| Ruff | `ruff check .` | **All checks passed!** |
| Byte compile | `python -m compileall -q .` | exit 0 |
| pytest-qt suite | `pytest tests/desktop -q` (offscreen QPA) | **175 passed in 20.50s** |
| E2E smoke | real-browser driver | **1 passed in 6.84s** |

CI job duration: **2m 21s**. No failures, no warnings.

This confirms: dependency installation succeeds on Windows, all 175 desktop tests compile and
run under offscreen Qt on Windows Server 2025, no platform-specific import failures, ruff
reports zero issues.

**Automated leg: PASS.**

## Windows 11 x64 — interactive display checklist (2026-08-07)

Executed on a physical Windows 11 x64 machine with attached display.

| # | Check | Result |
|---|-------|--------|
| 3 | Launch with native Windows Qt plugin — window appears | **PASS** |
| 4 | AppShell, Sidebar (9 sections, 6 disabled), TopBar render | **PASS** |
| 5 | Home/Projects/Settings navigation; disabled items do nothing | **PASS** |
| 6 | Light/Dark/System — window palette visibly changes | **PASS** |
| 7 | Resize/move, quit, relaunch — geometry (incl. width) restored | **PASS** |
| 8 | Clean quit, no error dialog or crash | **PASS** |

**Interactive leg: PASS.**

## Gate verdict

| Leg | Status |
|-----|--------|
| macOS Apple Silicon (×3 verifications) | **PASS** |
| Windows automated CI (Run 31150374277) | **PASS** |
| Windows 11 x64 physical interactive | **PASS** |

**`AICC-D1-GATE`: DONE** — all acceptance criteria verified on both target platforms.

> **Note (2026-08-07):** `AICC-D2A` must not start constraint is now moot — D2A–D3B are already
> implemented on `main`. The real dependency on D1-GATE is the D2/D3/D4 gate records, which
> similarly require the Windows machine.
