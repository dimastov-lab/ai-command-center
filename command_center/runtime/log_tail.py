"""Bounded, safe log access for the Live Execution Center v2 dashboard.

There is no separate stdout/stderr log *file* for a v2 run — the Supervisor
streams every stdout/stderr line and lifecycle event straight into the
`run_event` SQLite table (see `supervisor.py`'s `_drain_stdout`/
`_drain_stderr`). "Tailing logs" here means a bounded, `LIMIT`-ed read of
that table — never the whole table, never a raw file, and never anything
from process environment/config, so there is nothing here that could leak a
secret.
"""

from __future__ import annotations

import json
from pathlib import Path

from command_center.runtime import db as runtime_db

DEFAULT_MAX_LINES = 200


def tail_events(db_path: Path, run_id: str, *, max_lines: int = DEFAULT_MAX_LINES) -> list[dict]:
    """The last `max_lines` events for a run, oldest-first. Thin passthrough
    to `db.tail_run_events` — kept as its own function so callers depend on
    `log_tail`, not on `runtime.db` internals, for this read."""
    return runtime_db.tail_run_events(db_path, run_id, limit=max_lines)


def _format_line(event: dict) -> str:
    """Same line format the original single-run watch panel used, kept
    identical so this move changes nothing about what the user sees."""
    preview = json.dumps(event["payload"], ensure_ascii=False)[:500]
    return f"[{event['seq']:>5}] {event.get('created_at', '—')} {event['event_type']}: {preview}"


def render_log_lines(events: list[dict]) -> list[str]:
    return [_format_line(event) for event in events]


def latest_event(db_path: Path, run_id: str) -> dict | None:
    """The single most recent event for a run, of any type — used by
    `session_view.build_session_view`'s `latest_event` field, so the
    dashboard card and the log expander never disagree about "what happened
    most recently."""
    events = tail_events(db_path, run_id, max_lines=1)
    return events[-1] if events else None


def latest_events_for_runs(db_path: Path, run_ids: list[str]) -> dict[str, dict]:
    """Batch of `latest_event` keyed by run_id — one query for a whole board
    instead of one per run (audit H5 N+1). Thin passthrough to
    `db.latest_events_for_runs`, keeping callers off `runtime.db` internals."""
    return runtime_db.latest_events_for_runs(db_path, run_ids)


# Every lifecycle event the Supervisor ever writes already covers the
# mission's "Execution History" (Launch/Status changes/Completion/Failure/
# Cancellation/Timeline) — see the `stream_parser.lifecycle_event(...)` call
# sites in `supervisor.py`: `process_started`, `cancel_requested`,
# `cancel_sigterm_sent`, `cancel_sigkill_sent`, `process_exited`,
# `timeout_exceeded`/`timeout_sigterm_sent`/`timeout_sigkill_sent`,
# `reconciliation_orphaned`, `reconciliation_classified`. No new persistence
# is added here — this is a filtered *view* of what already exists.
_LIFECYCLE_EVENT_TYPE = "lifecycle"


def session_timeline(db_path: Path, run_id: str, *, max_lines: int = DEFAULT_MAX_LINES) -> list[dict]:
    """Only the `lifecycle`-typed events for a run, oldest-first, filtered
    and bounded directly in SQL (`db.list_run_events`'s `event_type`
    parameter) — never a full-table read followed by an in-Python filter."""
    return runtime_db.list_run_events(db_path, run_id, after_seq=0, limit=max_lines, event_type=_LIFECYCLE_EVENT_TYPE)
