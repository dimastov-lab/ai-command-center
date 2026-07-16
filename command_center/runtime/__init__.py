"""v2 Sprint 1 runtime: SQLite-backed Session Supervisor for Claude Code execution.

This package is additive to `command_center/` (the v1.2 JSON/JSONL runtime is left
completely untouched — see `legacy_import.py` for the one-way, non-destructive import
path from v1.2 data into this package's SQLite store).

Module map:

- `db.py` — SQLite connection/pragma management, idempotent migrations, the
  `Task`/`Session`/`Run`/`RunEvent`/`Report` schema, and compare-and-set run updates.
- `identity.py` — process existence/identity capture (`ps`-based), used both when a
  run is launched (to record what was started) and during startup reconciliation (to
  decide whether a recorded PID is still the same process).
- `stream_parser.py` — turns one `claude --output-format stream-json` line into a
  typed event, never raising on malformed input.
- `supervisor.py` — owns subprocess lifecycle: launch, incremental stream
  persistence, cancellation, and startup reconciliation.
- `context_service.py` — the sensitive-project (BANK/LEGAL) context boundary,
  enforced here rather than trusted to a caller/UI.
- `reports.py` — renders the immutable, never-truncated report artifact for a run.
- `legacy_import.py` — one-way import of v1.2 `data/runs.jsonl` into this schema.
- `api.py` — the minimal Execution Center backend surface described in the Sprint 1
  brief (list sessions/runs, inspect status, read events, request cancellation,
  reconcile).
"""
