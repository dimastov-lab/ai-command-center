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
STATUS_STARTING = "Starting"
STATUS_RUNNING = "Running"
STATUS_STALE = "Stale"
STATUS_WAITING = "Waiting"
STATUS_REQUIRES_ATTENTION = "Requires Attention"
STATUS_BLOCKED = "Blocked"
STATUS_INCOMPLETE = "Incomplete"
STATUS_COMPLETED = "Completed"
STATUS_FAILED = "Failed"
STATUS_CANCELLED = "Cancelled"

ACTIVE_DISPLAY_STATUSES: frozenset[str] = frozenset(
    {STATUS_LAUNCHING, STATUS_STARTING, STATUS_RUNNING, STATUS_STALE, STATUS_WAITING}
)

# A live OS process either exists or provably existed a moment ago for every
# one of these — the run is spawned and not terminal. `STATUS_STARTING` (PID
# valid, no output yet) and `STATUS_STALE` (PID valid, liveness probe momentarily
# old) are *warnings about a running process*, never failures: the UI treats
# all three exactly like `STATUS_RUNNING` for cancel/heartbeat affordances, and
# `task_sync` keeps their task non-terminal. This is the concrete guard behind
# "a successfully spawned process must never be recorded Failed just because
# early output / a fresh probe has not arrived yet."
LIVE_PROCESS_DISPLAY_STATUSES: frozenset[str] = frozenset(
    {STATUS_STARTING, STATUS_RUNNING, STATUS_STALE}
)
# `STATUS_BLOCKED`/`STATUS_INCOMPLETE` are terminal too — the process has
# already exited (see `runtime.outcome`'s `EvaluatingResult` stage, which is
# what produces these out of a `FAILED`-state run's `failure_reason`), it
# just did not reach a genuine `COMPLETED` outcome. Included here so
# `task_sync._apply_terminal_fields` still runs for them (report path,
# verdict, etc.) exactly as it does for `FAILED`/`CANCELLED`.
TERMINAL_DISPLAY_STATUSES: frozenset[str] = frozenset(
    {STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED, STATUS_BLOCKED, STATUS_INCOMPLETE}
)

_BLOCKED_REASON_PREFIX = "blocked:"
_INCOMPLETE_REASON_PREFIX = "incomplete:"

# Default staleness threshold for the heartbeat liveness probe (see
# `get_heartbeat_age`/`is_heartbeat_stale` below).
DEFAULT_HEARTBEAT_STALE_SECONDS = 90.0


def is_awaiting_handshake(run: dict) -> bool:
    """`True` for a run whose process is spawned and RUNNING but has not yet
    produced any output (`first_output_at` is unset) — the "started but early
    output not yet received" window. Used to distinguish `STATUS_STARTING`
    from `STATUS_RUNNING`. Never `True` for a terminal run (that a run
    completed/failed/was cancelled without ever emitting output is decided on
    exit facts, not on this flag)."""
    return run.get("state") == "RUNNING" and not run.get("first_output_at")


def derive_status(run: dict, *, awaiting_handshake: bool = False, heartbeat_stale: bool = False) -> str:
    """Maps `run["state"]` (+ `cancel_requested`/`failure_reason`) to the
    mission's display vocabulary. `INTERRUPTED`/`UNKNOWN`/any unrecognized
    state is conservatively mapped to `Requires Attention` — this module
    never guesses a terminal-ok outcome for an ambiguous run, mirroring
    `Supervisor.reconcile()`'s own conservatism.

    A `FAILED` run whose `failure_reason` carries the `"blocked:"`/
    `"incomplete:"` prefix `runtime.outcome.classify_process_result`
    produces (via `Supervisor._supervise`) is displayed as `Blocked`/
    `Incomplete` rather than the generic `Failed` — both are still process
    exits that did not deliver the requested work, but the reason differs
    (a denied tool call / explicit blocker language, vs. a clean exit that
    simply didn't produce the required repository changes) and the UI must
    be able to tell them apart (Required fix 7).

    `awaiting_handshake`/`heartbeat_stale` are **opt-in** refinements of the
    `RUNNING` case only, and both default to `False` so every existing
    single-argument caller keeps its exact prior behavior (a plain RUNNING run
    is still `Running`). When the live projection layer passes them:

    - `heartbeat_stale=True` -> `STATUS_STALE`: the process is still recorded
      RUNNING but the UI's liveness probe is momentarily old. A *warning about
      a running process*, not a failure.
    - `awaiting_handshake=True` -> `STATUS_STARTING`: a valid PID exists but no
      output has arrived yet ("agent started but early output was not
      received"). Also a warning, never a failure — this is exactly the state
      a healthy-but-slow-to-first-token run occupies, and it must never be
      shown as `Failed`.

    Neither ever applies to a non-RUNNING state, and an explicit
    `cancel_requested` still wins over both (a cancel in flight is `Waiting`,
    regardless of handshake/probe)."""
    state = run.get("state", "UNKNOWN")
    if state in ("PREPARED", "QUEUED"):
        return STATUS_LAUNCHING
    if state == "RUNNING":
        if run.get("cancel_requested"):
            return STATUS_WAITING
        if heartbeat_stale:
            return STATUS_STALE
        if awaiting_handshake:
            return STATUS_STARTING
        return STATUS_RUNNING
    if state == "COMPLETED":
        return STATUS_COMPLETED
    if state == "FAILED":
        reason = run.get("failure_reason") or ""
        if reason.startswith(_BLOCKED_REASON_PREFIX):
            return STATUS_BLOCKED
        if reason.startswith(_INCOMPLETE_REASON_PREFIX):
            return STATUS_INCOMPLETE
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
    heartbeat_stale: bool = False,
) -> dict:
    """The canonical execution-session view model (mission field list). The
    only I/O performed here is one read-only `git status` call against the
    run's own resolved workspace — never a switch/checkout/write.

    `heartbeat_stale` is the caller's (UI-owned) liveness-probe verdict for
    this run — kept out of this pure module and passed in, exactly like
    `project_overview` already takes `stale_run_ids`. When it is `True` for a
    still-RUNNING run, `status` becomes `STATUS_STALE` (a warning about a
    running process, never a failure)."""
    awaiting_handshake = is_awaiting_handshake(run)
    status = derive_status(run, awaiting_handshake=awaiting_handshake, heartbeat_stale=heartbeat_stale)
    started_at = run.get("started_at")
    finished_at = run.get("completed_at")
    workspace_path = run.get("repository_path")
    git_status = live_git_status(workspace_path)

    task_title = (kanban_task or {}).get("title") or (run.get("prompt") or "")[:80] or (run.get("id") or "")[:8]
    executor = (kanban_task or {}).get("executor") or "claude_code"

    last_error = run.get("failure_reason")
    if not last_error and status == STATUS_FAILED and latest_event and latest_event.get("event_type") == "stderr_line":
        last_error = (latest_event.get("payload") or {}).get("line")

    # Explicit, unambiguous field for `Blocked`/`Incomplete` — distinct from
    # `last_error` (which also covers plain `Failed` stderr output) so the UI
    # can render "why this was blocked" without having to re-derive it from
    # `status` + `last_error` itself (Required fix 7).
    blocker_reason = run.get("failure_reason") if status in (STATUS_BLOCKED, STATUS_INCOMPLETE) else None

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
        # Spawn confirmation vs. startup/handshake, kept as two distinct,
        # explicit fields so the UI can say "started but early output not yet
        # received" without re-deriving it: `process_id`/`started_at` prove
        # the process was *created*; `first_output_at`/`handshake_received`
        # prove it is *alive and talking*. `awaiting_handshake` is the window
        # between the two.
        "first_output_at": run.get("first_output_at"),
        "handshake_received": bool(run.get("first_output_at")),
        "awaiting_handshake": awaiting_handshake,
        "elapsed_seconds": elapsed_seconds(started_at, finished_at, now),
        "status": status,
        "current_stage": (kanban_task or {}).get("current_stage"),
        "progress": (kanban_task or {}).get("progress"),
        "exit_code": run.get("exit_code"),
        "last_error": last_error,
        "blocker_reason": blocker_reason,
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
