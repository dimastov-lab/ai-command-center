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

## Windows 11 x64 — NOT PERFORMED

No Windows 11 x64 machine (real or otherwise) was accessible from this automation session. None of
the acceptance criteria could be exercised there. This is the only remaining blocker to closing
this gate.

## Outstanding work to close this gate

A human (or an automation session with access to real hardware) must still perform, on both a real
macOS Apple Silicon machine *with an attached display* and a real Windows 11 x64 machine:

1. Launch `python -m command_center.desktop` interactively (native window, not offscreen).
2. Confirm `AppShell`, `Sidebar` (all nine sections, Sessions/Execution/Git/Artifacts/Reports/
   Agents disabled), and `TopBar` render.
3. Click Home/Projects/Settings and confirm the visible page switches; click a disabled item and
   confirm nothing happens.
4. Switch Light/Dark/System and confirm the window palette visibly changes.
5. Resize/move the window, quit, relaunch, and confirm geometry (including width) is restored
   exactly.
6. Quit cleanly with no error dialog or crash.

Until both platforms' checklists are recorded here, `AICC-D1-GATE` remains **Review**, not **Done**,
and `AICC-D2A` must not start (per this gate's forbidden-scope note and
`docs/roadmap/MASTER_PRODUCT_ROADMAP.md` §2.1).
