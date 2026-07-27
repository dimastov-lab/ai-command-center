"""Unified runs read layer — one normalized list over both run sources.

AICC persists runs in two places:

* ``data/runtime.db`` (the v2 runtime — the *canonical* source: every launch
  the Execution Center performs lands here, ~100+ runs on a busy install).
* ``data/runs.jsonl`` (the legacy v1.2 synchronous journal — kept for older
  installs and for the manual-field-correction write-back path in the Runs
  page; on current installs it is typically empty).

Four pages (Executive "Метрики запусков", Журнал запусков, Отчёты, Таймлайн)
historically read *only* ``agent_runner.load_runs()`` — the v1.2 journal — so
on any install where launches go through the v2 Execution Center they showed
**zero runs**. This module is the single read they should all go through: it
merges both sources into one list with a stable, page-friendly shape and a
canonical ``(source, run_id)`` identity (bare ``run_id`` collides across
sources because both use ``models.new_id()``).

This is a **read model** — it never writes. The v1.2 manual-correction
write-back (``agent_runner.append_run``) stays in the Runs page and only
applies to ``source == "v1.2"`` rows; v2 runs are read-only here.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from command_center import agent_runner, report_parser
from command_center.runtime import db

# v2 persisted ``state`` (``runtime/db.RUN_STATES``) → the v1.2 lowercase
# ``status`` vocabulary that the Runs/Timeline/Executive pages and
# ``models.RUN_STATUS_LABELS`` / ``RUN_STATUS_COLORS`` are built around. Every
# RUN_STATES value maps; the inverse (v1.2 → v2) is below.
_STATE_TO_STATUS: dict[str, str] = {
    "PREPARED": "queued",
    "QUEUED": "queued",
    "RUNNING": "running",
    "COMPLETED": "completed",
    "FAILED": "failed",
    "INTERRUPTED": "timed_out",
    "UNKNOWN": "failed",
    "CANCELLED": "cancelled",
}

_STATUS_TO_STATE: dict[str, str] = {
    "queued": "QUEUED",
    "running": "RUNNING",
    "completed": "COMPLETED",
    "failed": "FAILED",
    "timed_out": "INTERRUPTED",
    "cancelled": "CANCELLED",
}


def _agent_from_command(command_json: str | None) -> str:
    """v2 runs don't store an executor column; the launched command's argv[0]
    is the closest honest signal (``claude`` / ``codex`` / …). Falls back to
    ``"—"`` so the Runs page "Агент" filter always has a value."""
    if not command_json:
        return "—"
    try:
        argv = json.loads(command_json)
    except (json.JSONDecodeError, TypeError):
        return "—"
    if isinstance(argv, list) and argv:
        first = argv[0]
        if isinstance(first, str) and first:
            return Path(first).name or first
    return "—"


def _duration_seconds(run: dict) -> float | None:
    started = run.get("started_at")
    completed = run.get("completed_at")
    if not started or not completed:
        return None
    try:
        s = datetime.fromisoformat(started)
        c = datetime.fromisoformat(completed)
    except (ValueError, TypeError):
        return None
    delta = (c - s).total_seconds()
    return delta if delta >= 0 else None


def _parse_report_file(report_path: str | None, root: Path) -> dict:
    """Parse a v2 run's report file into the same ``parsed`` shape v1.2 runs
    carry inline, so the Runs/Timeline/Executive pages can treat verdict /
    severity uniformly. Missing or unreadable → empty parsed result (the UI
    renders "не определён"), never raises."""
    if not report_path:
        return report_parser.empty_parsed_result()
    full = root / report_path
    if not full.exists():
        return report_parser.empty_parsed_result()
    try:
        text = full.read_text(encoding="utf-8")
    except OSError:
        return report_parser.empty_parsed_result()
    return report_parser.parse_report(text)


def _git_status_from_json(raw: str | None) -> dict:
    """v2 stores pre/post git status as a JSON blob (``pre_run_git_status`` /
    ``post_run_git_status``); the Runs page's detail expander expects the v1.2
    ``{branch, head}`` shape. Unparse defensively."""
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return obj if isinstance(obj, dict) else {}


def _normalize_v2(run: dict, *, report: dict | None, root: Path) -> dict:
    state = run.get("state") or "UNKNOWN"
    status = _STATE_TO_STATUS.get(state, "failed")
    report_path = (report or {}).get("path")
    return {
        "id": run.get("id"),
        "source": "v2",
        "task_id": run.get("task_id"),
        "project": run.get("project"),
        "repository_path": run.get("repository_path"),
        "task_type": run.get("task_type"),
        "state": state,
        "status": status,
        "created_at": run.get("created_at"),
        "started_at": run.get("started_at"),
        "completed_at": run.get("completed_at"),
        "duration_seconds": _duration_seconds(run),
        "agent": _agent_from_command(run.get("command_json")),
        "report_path": report_path,
        "parsed": _parse_report_file(report_path, root),
        "pre_run": _git_status_from_json(run.get("pre_run_git_status")),
        "post_run": _git_status_from_json(run.get("post_run_git_status")),
        # v2 run lifecycle events (stdout/stderr) are not loaded here — they
        # live in the run_event table and are heavy; the Runs page detail
        # expander renders "—" for v2 stdout/stderr rather than faking it.
        "stdout": "",
        "stderr": "",
        "prompt": run.get("prompt") or "",
        "next_task_id": None,
        "exit_code": run.get("exit_code"),
    }


def _normalize_v1(run: dict) -> dict:
    """v1.2 journal rows already carry the lowercase ``status`` and inline
    ``parsed``/``stdout``/``stderr`` the pages expect; we only add the
    canonical ``state`` and ``source`` so consumers can treat both sources
    uniformly."""
    status = run.get("status") or "failed"
    return {
        **run,
        "source": "v1.2",
        "state": _STATUS_TO_STATE.get(status, "UNKNOWN"),
        "status": status,
    }


def list_unified_runs(db_path: Path, *, root: Path, include_v1: bool = True) -> list[dict]:
    """Merge v2 (``runtime.db``) and v1.2 (``runs.jsonl``) runs into one
    normalized list, newest-created first. ``root`` is the project root used
    to resolve v2 report paths for parsing.

    Both sources are tagged with ``source`` ("v2" / "v1.2") and a normalized
    shape (see module docstring). ``id`` is preserved per-source but the only
    stable cross-source identity is ``(source, id)`` — bare ``id`` collides.
    """
    v2_runs = db.list_runs(db_path)  # no limit — the pages are paged by filters
    reports = db.get_reports_for_runs(db_path, [r["id"] for r in v2_runs])
    unified = [_normalize_v2(r, report=reports.get(r["id"]), root=root) for r in v2_runs]

    if include_v1:
        for run in agent_runner.load_runs():
            # De-dup: a v1.2 run that was also migrated to v2 (same id) is
            # represented by its v2 row — the canonical, richer source.
            rid = run.get("id")
            if rid and any(u["id"] == rid and u["source"] == "v2" for u in unified):
                continue
            unified.append(_normalize_v1(run))

    unified.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return unified