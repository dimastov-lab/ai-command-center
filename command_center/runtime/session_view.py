"""Live Execution Center v2 — pure projection layer.

Turns one v2 `run` row (plus optional linked Kanban task, project config, and
latest event) into the canonical execution-session view model the dashboard
renders. No Streamlit import, no database write, no subprocess call beyond
one read-only `git status` against the run's own workspace — safe to unit
test without a live Streamlit script context or a real `runtime.db`.

`derive_status` is a *display*-only mapping. `run["state"]`
(`command_center.runtime.db.RUN_STATES`) remains the sole persisted
execution-state enum — nothing in this module is ever written back to
`runtime.db` or to a Kanban task record. No new persisted status value is
introduced anywhere in this project by this module.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from command_center import git_info

# --------------------------------------------------------------------------
# Status vocabulary (display-only — see module docstring)
# --------------------------------------------------------------------------

STATUS_LAUNCHING = "Launching"
STATUS_RUNNING = "Running"
STATUS_WAITING = "Waiting"
STATUS_REQUIRES_ATTENTION = "Requires Attention"
STATUS_COMPLETED = "Completed"
STATUS_FAILED = "Failed"
STATUS_CANCELLED = "Cancelled"

ACTIVE_DISPLAY_STATUSES: frozenset[str] = frozenset({STATUS_LAUNCHING, STATUS_RUNNING, STATUS_WAITING})
TERMINAL_DISPLAY_STATUSES: frozenset[str] = frozenset({STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED})

# Default staleness threshold for the heartbeat liveness probe (see
# `get_heartbeat_age`/`is_heartbeat_stale` below).
DEFAULT_HEARTBEAT_STALE_SECONDS = 90.0


def derive_status(run: dict) -> str:
    """Maps `run["state"]` (+ `cancel_requested`) to the mission's display
    vocabulary. `INTERRUPTED`/`UNKNOWN`/any unrecognized state is
    conservatively mapped to `Requires Attention` — this module never
    guesses a terminal-ok outcome for an ambiguous run, mirroring
    `Supervisor.reconcile()`'s own conservatism."""
    state = run.get("state", "UNKNOWN")
    if state in ("PREPARED", "QUEUED"):
        return STATUS_LAUNCHING
    if state == "RUNNING":
        return STATUS_WAITING if run.get("cancel_requested") else STATUS_RUNNING
    if state == "COMPLETED":
        return STATUS_COMPLETED
    if state == "FAILED":
        return STATUS_FAILED
    if state == "CANCELLED":
        return STATUS_CANCELLED
    return STATUS_REQUIRES_ATTENTION


def format_elapsed(seconds: float | None) -> str:
    """`HH:MM:SS`, or `"—"` for `None`/negative (never happens in practice,
    guarded defensively since `now` can theoretically race a just-recorded
    `started_at` by a few milliseconds)."""
    if seconds is None or seconds < 0:
        return "—"
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def elapsed_seconds(started_at: str | None, finished_at: str | None, now: datetime) -> float | None:
    started = _parse_iso(started_at)
    if started is None:
        return None
    end = _parse_iso(finished_at) or now
    return max(0.0, (end - started).total_seconds())


def live_git_status(workspace_path: str | None) -> dict[str, Any] | None:
    """Read-only, best-effort: never switches branches, never mutates the
    workspace. Returns `None` if the path is missing, not a directory, or not
    a git repo — callers render "—" rather than crashing."""
    if not workspace_path:
        return None
    path = Path(workspace_path).expanduser()
    if not path.is_dir():
        return None
    status = git_info.get_status(path)
    return status if status.get("is_repo") else None


def _summarize_event(event: dict | None) -> dict | None:
    if not event:
        return None
    event_type = event.get("event_type", "?")
    payload = event.get("payload") or {}
    if event_type in ("stdout_line", "stderr_line"):
        summary = (payload.get("line") or "").strip()
    elif event_type == "lifecycle":
        summary = payload.get("lifecycle") or event_type
    elif event_type == "result":
        summary = (payload.get("result") or "").strip()
    elif event_type == "assistant_message":
        summary = "assistant message"
    else:
        summary = event_type
    return {"summary": summary[:200] if summary else event_type, "at": event.get("created_at")}


def build_session_view(
    run: dict,
    *,
    kanban_task: dict | None,
    project_cfg: dict | None,
    latest_event: dict | None,
    report_path: str | None,
    now: datetime,
) -> dict:
    """The canonical execution-session view model (mission field list). The
    only I/O performed here is one read-only `git status` call against the
    run's own resolved workspace — never a switch/checkout/write."""
    status = derive_status(run)
    started_at = run.get("started_at")
    finished_at = run.get("completed_at")
    workspace_path = run.get("repository_path")
    git_status = live_git_status(workspace_path)

    task_title = (kanban_task or {}).get("title") or (run.get("prompt") or "")[:80] or (run.get("id") or "")[:8]
    executor = (kanban_task or {}).get("executor") or "claude_code"

    last_error = run.get("failure_reason")
    if not last_error and status == STATUS_FAILED and latest_event and latest_event.get("event_type") == "stderr_line":
        last_error = (latest_event.get("payload") or {}).get("line")

    return {
        "session_id": run.get("session_id"),
        "run_id": run.get("id"),
        "task_id": run.get("task_id"),
        "task_title": task_title,
        "project_id": run.get("project"),
        "executor": executor,
        "workspace_path": workspace_path,
        "repository_path": (project_cfg or {}).get("repository_path") or workspace_path,
        "expected_branch": run.get("expected_branch"),
        "actual_branch": (git_status or {}).get("branch"),
        "git_status": git_status,
        "launch_source": run.get("launch_source") or "execution_center_adhoc",
        "process_id": run.get("pid"),
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_seconds": elapsed_seconds(started_at, finished_at, now),
        "status": status,
        "current_stage": (kanban_task or {}).get("current_stage"),
        "progress": (kanban_task or {}).get("progress"),
        "exit_code": run.get("exit_code"),
        "last_error": last_error,
        "prompt_version": run.get("prompt_version"),
        "prompt": run.get("prompt"),
        "report_path": report_path,
        "commit_hash": run.get("commit_hash"),
        "pull_request_url": run.get("pull_request_url"),
        "latest_event": _summarize_event(latest_event),
    }


# --------------------------------------------------------------------------
# Heartbeat — a derived liveness *probe*, never an agent-emitted signal.
#
# The runtime has no heartbeat concept: no component here ever pings, and no
# `run`/`session` row carries a "last alive at" column. What this section
# provides instead is honestly a different thing wearing the same word the
# mission's mock uses: the wall-clock time the UI itself last *confirmed*
# (via a cheap, read-only `identity.capture_identity(pid) is not None`
# check — the exact primitive `Supervisor.reconcile()` already uses, just
# not mutating anything) that a Running session's PID still exists. It is
# intentionally kept in `st.session_state` by the caller, not in
# `runtime.db` — persisting a real timestamp on every 2-5s refresh tick for
# the life of every run would mean one new row (or write) per tick, an
# unbounded growth this project's log-tailing design elsewhere explicitly
# avoids. Every rendering of this value must be labeled "derived liveness
# probe", never "heartbeat" alone, per the mission's explicit instruction.
# --------------------------------------------------------------------------


def heartbeat_age_seconds(last_probe_at: datetime | None, now: datetime) -> float | None:
    if last_probe_at is None:
        return None
    return max(0.0, (now - last_probe_at).total_seconds())


def is_heartbeat_stale(
    last_probe_at: datetime | None, now: datetime, *, threshold_seconds: float = DEFAULT_HEARTBEAT_STALE_SECONDS
) -> bool:
    """`True` if there is no probe yet, or the last one is older than
    `threshold_seconds` — both cases mean the UI cannot currently vouch for
    this session's liveness."""
    age = heartbeat_age_seconds(last_probe_at, now)
    return age is None or age > threshold_seconds
