# D2 Gate Smoke Test Record

`AICC-D2-GATE` — Workspace Home integration gate (D2A application adapter + D2B async workers
+ D2C Workspace Home layout + D2D edge states / accessibility / redaction).

## Acceptance criteria

- D2A: `workspace_home_adapter.py` surfaces real AIOS data (or offline stubs) to the UI layer.
- D2B: `workers.py` async framework runs background fetches without blocking the Qt event loop.
- D2C: `pages/home.py` (≥ 400 lines) renders Workspace Home with all widget regions.
- D2D: BANK/LEGAL content is redacted; empty/error states render; keyboard navigation works.
- All `tests/desktop/` pass under `QT_QPA_PLATFORM=offscreen`.
- `ruff check .` reports zero issues.

## macOS Apple Silicon (2026-08-07)

Verified on the same Apple Silicon Mac used for D1 re-verification. D2A–D2D were implemented
and committed to `main` between the D1 gate and this record; the full suite was re-run.

- **`pytest tests/desktop/ -q`** → **175 passed** in 8.95s (0 failed, 0 errors).
- **`ruff check .`** → all checks passed.
- **`wc -l command_center/desktop/pages/home.py`** → 490 lines — D2C size criterion met.
- **Offline-stub smoke**: `WorkspaceHomeAdapter(offline=True)` returned populated `WorkspaceSummary`
  with all required fields — PASS.
- **Redaction check**: BANK/LEGAL pattern blocked in adapter layer — PASS.

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

The 175-test suite includes D2A adapter unit tests, D2B worker frame tests, D2C widget layout
tests, and D2D edge-state tests. All pass on Windows Server 2025.

**Automated leg: PASS.**

## Windows 11 x64 — interactive display checklist (2026-08-07)

Executed on a physical Windows 11 x64 machine with attached display.

| # | Check | Result |
|---|-------|--------|
| 1 | Launch with native Windows Qt plugin | **PASS** |
| 2 | Navigate to Home — Workspace Home layout with all widget regions renders | **PASS** |
| 3 | Offline state — empty/error widget renders correctly | **PASS** |
| 4 | BANK/LEGAL content not shown in plain text (redaction active) | **PASS** |
| 5 | Async data load does not block UI | **PASS** |
| 6 | Clean quit | **PASS** |

**Interactive leg: PASS.**

## Gate verdict

| Leg | Status |
|-----|--------|
| macOS Apple Silicon | **PASS** |
| Windows automated CI (Run 31150374277) | **PASS** |
| Windows 11 x64 physical interactive | **PASS** |

**`AICC-D2-GATE`: DONE** — all D2 acceptance criteria verified on both target platforms.
