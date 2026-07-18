"""Per-project rollup cards for the Live Execution Center v2 dashboard.

Pure aggregation over already-built `session_view.build_session_view` dicts —
no new signal, no I/O, no persistence. `health` is a derivation of statuses
already computed elsewhere, never an independent probe.
"""

from __future__ import annotations

from datetime import datetime

from command_center.runtime import session_view

HEALTH_OK = "OK"
HEALTH_ATTENTION = "Attention"
HEALTH_DEGRADED = "Degraded"


def _is_today(iso_ts: str | None, now: datetime) -> bool:
    if not iso_ts:
        return False
    try:
        ts = datetime.fromisoformat(iso_ts)
    except (ValueError, TypeError):
        return False
    return ts.date() == now.date()


def build_project_overview(
    project_id: str,
    *,
    sessions: list[dict],
    project_cfg: dict | None,
    now: datetime,
    stale_run_ids: frozenset[str] = frozenset(),
) -> dict:
    """`sessions` must already be filtered to this project's session-view
    dicts (from `session_view.build_session_view`). `stale_run_ids` is the
    caller's session-state-derived set of runs whose heartbeat probe is
    currently stale (see `session_view.is_heartbeat_stale`) — passed in
    rather than recomputed here, since heartbeat state is intentionally kept
    out of this pure module."""
    running = [s for s in sessions if s["status"] == session_view.STATUS_RUNNING]
    waiting = [s for s in sessions if s["status"] in (session_view.STATUS_WAITING, session_view.STATUS_REQUIRES_ATTENTION)]
    failed = [s for s in sessions if s["status"] == session_view.STATUS_FAILED]
    requires_attention = [s for s in sessions if s["status"] == session_view.STATUS_REQUIRES_ATTENTION]
    completed_today = [
        s for s in sessions if s["status"] == session_view.STATUS_COMPLETED and _is_today(s.get("finished_at"), now)
    ]

    active = [s for s in sessions if s["status"] in session_view.ACTIVE_DISPLAY_STATUSES]
    active.sort(key=lambda s: s.get("started_at") or "", reverse=True)
    most_recent_active = active[0] if active else None

    cfg = project_cfg or {}
    current_executor = (most_recent_active or {}).get("executor") or cfg.get("default_executor")
    current_workspace = (most_recent_active or {}).get("workspace_path") or cfg.get("default_workspace_path") or cfg.get(
        "repository_path"
    )
    current_branch = (most_recent_active or {}).get("actual_branch") or cfg.get("default_branch")

    stale_running = any(s["run_id"] in stale_run_ids for s in running)

    if failed or requires_attention:
        health = HEALTH_DEGRADED
    elif waiting or stale_running:
        health = HEALTH_ATTENTION
    else:
        health = HEALTH_OK

    return {
        "project_id": project_id,
        "running_count": len(running),
        "waiting_count": len(waiting),
        "completed_today_count": len(completed_today),
        "current_executor": current_executor,
        "current_workspace": current_workspace,
        "current_branch": current_branch,
        "health": health,
    }
