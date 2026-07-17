"""Workspace Home read model — plain Python, no Streamlit dependency.

Composes existing read-only capabilities (`project_config`, `activity_log`,
`agent_runner`, `command_center.artifacts`, `command_center.git_info`, and an
*injected* `ExecutionCenterAPI`) into one bounded, cross-project snapshot
dict for a single new `app.py` page to render.

Dependency direction (enforced, see WORKSPACE_HOME_ARCHITECTURE.md §9.2):
this module imports `command_center.artifacts` for artifact/report
discovery and `command_center.git_info` for per-repository git discovery —
never `app.py`, directly or transitively. `app.py` imports this module, not
the other way around.

Security boundary (§5.1/§13): `sanitize_workspace_project_entry` strips
every non-allowlisted field from a sensitive project's (`BANK`/`LEGAL`)
runs/reports/artifacts/activity *before* they are folded into the snapshot
this module returns. The renderer never receives the raw fields for those
projects — there is no code path back to them.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from command_center import activity_log, agent_runner, project_config, report_parser
from command_center import artifacts as artifact_discovery
from command_center import git_info
from command_center.runtime import db as runtime_db
from command_center.runtime.api import ExecutionCenterAPI

ROOT = Path(__file__).resolve().parent.parent
GENERATED_DIR = ROOT / "generated"
REPORTS_DIR = ROOT / "reports"

# --------------------------------------------------------------------------
# Sensitivity redaction stage (§5.1)
# --------------------------------------------------------------------------

_RUN_ALLOWED_FIELDS: frozenset[str] = frozenset(
    {
        "run_id",
        "source",
        "project",
        "task_type",
        "state",
        "status",
        "created_at",
        "started_at",
        "completed_at",
        "exit_code",
        "duration_seconds",
        "failure_reason",
    }
)

_REPORT_ALLOWED_FIELDS: frozenset[str] = frozenset(
    {"run_id", "source", "project", "verdict", "severity_counts", "created_at"}
)

_ACTIVITY_ALLOWED_FIELDS: frozenset[str] = frozenset(
    {"project", "type", "event_type", "ts", "run_id", "task_id"}
)


def _filter_allowed(entry: dict, allowed: frozenset[str]) -> dict:
    return {key: value for key, value in entry.items() if key in allowed}


def _artifact_created_at(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    except OSError:
        return ""


def _artifact_entry(path: Path, project_id: str, *, full: bool) -> dict:
    entry = {
        "project": project_id,
        "task_type": artifact_discovery.infer_task_type_from_filename(path),
        "created_at": _artifact_created_at(path),
        "nav_target": {"nav": "generated", "project": project_id},
    }
    if full:
        entry["path"] = str(path)
        entry["filename"] = path.name
    return entry


def sanitize_workspace_project_entry(
    project_id: str,
    *,
    runs: list[dict],
    reports: list[dict],
    artifacts: list[Path],
    activity: list[dict],
) -> dict:
    """Returns `{"runs": [...], "reports": [...], "artifacts": [...], "activity": [...]}`.

    For a non-sensitive project (`not project_config.is_sensitive(project_id)`),
    every field of every run/report/activity entry passes through unchanged, and
    each artifact `Path` is expanded into a full entry (including its real path
    and filename). For a sensitive project (`BANK`, `LEGAL`), every entry is
    reduced to its allowlisted fields only — this is the *single* place
    Workspace Home's redaction policy lives, applied uniformly rather than as
    an if-sensitive branch scattered per section.
    """
    sensitive = project_config.is_sensitive(project_id)
    if not sensitive:
        return {
            "runs": [dict(run) for run in runs],
            "reports": [dict(report) for report in reports],
            "artifacts": [_artifact_entry(path, project_id, full=True) for path in artifacts],
            "activity": [dict(event) for event in activity],
        }
    return {
        "runs": [_filter_allowed(run, _RUN_ALLOWED_FIELDS) for run in runs],
        "reports": [_filter_allowed(report, _REPORT_ALLOWED_FIELDS) for report in reports],
        "artifacts": [_artifact_entry(path, project_id, full=False) for path in artifacts],
        "activity": [_filter_allowed(event, _ACTIVITY_ALLOWED_FIELDS) for event in activity],
    }


# --------------------------------------------------------------------------
# Git/worktree discovery (§7)
# --------------------------------------------------------------------------


def _configured_repository_path(cfg: dict) -> str | None:
    """Returns `cfg["repository_path"]` only if it is a non-blank string —
    the one shape `project_config.validate_repository_path`/`git_info` can
    safely accept. A malformed value (wrong type, e.g. from a hand-edited or
    corrupted `project_config.json`) is treated the same as "not configured"
    rather than raised, so it degrades gracefully instead of crashing."""
    repository_path = cfg.get("repository_path")
    if isinstance(repository_path, str) and repository_path.strip():
        return repository_path
    return None


def _project_worktrees(cfg: dict) -> dict:
    repository_path = _configured_repository_path(cfg)
    if repository_path is None:
        # Covers both the normal "unconfigured" case (None / "" / missing key)
        # and a malformed config value (wrong type entirely) — either way,
        # this project degrades to the same "unconfigured" card state rather
        # than raising, so one project's corrupted `repository_path` can
        # never take down the rest of the snapshot (§6 fault isolation).
        return {"status": "unconfigured", "worktrees": []}
    valid, _message = project_config.validate_repository_path(repository_path)
    if not valid:
        return {"status": "invalid_path", "worktrees": []}
    path = Path(repository_path)
    status = git_info.get_status(path)
    if not status.get("is_repo"):
        return {"status": "not_a_git_repository", "worktrees": []}
    return {"status": "ok", "worktrees": git_info.get_worktrees(path)}


# --------------------------------------------------------------------------
# Run tagging / identity (§8)
# --------------------------------------------------------------------------


def _tag_run(run: dict, source: str) -> dict:
    tagged = dict(run)
    tagged["source"] = source
    tagged["run_id"] = run.get("id")
    return tagged


def _run_duration_seconds(run: dict) -> float | None:
    if run.get("duration_seconds") is not None:
        return run["duration_seconds"]
    started, completed = run.get("started_at"), run.get("completed_at")
    if not started or not completed:
        return None
    try:
        return (datetime.fromisoformat(completed) - datetime.fromisoformat(started)).total_seconds()
    except ValueError:
        return None


def _sort_key(entry: dict, field: str) -> str:
    return entry.get(field) or ""


# --------------------------------------------------------------------------
# Activity derivation (§10)
# --------------------------------------------------------------------------


def _derive_run_activity(runs: list[dict]) -> list[dict]:
    """Derive display-only activity rows from an already-tagged, already-
    sanitized run list — never from raw run rows. Because the source list is
    already redacted (§5.1), these derived rows inherit that redaction for
    free; there is no second enforcement point needed for them."""
    derived: list[dict] = []
    for run in runs:
        project = run.get("project")
        run_id = run.get("run_id")
        if run.get("started_at"):
            derived.append(
                {
                    "project": project,
                    "type": "run_started",
                    "event_type": "run_started",
                    "ts": run["started_at"],
                    "run_id": run_id,
                    "task_id": None,
                }
            )
        state = run.get("state") or run.get("status")
        if run.get("completed_at") and state:
            derived.append(
                {
                    "project": project,
                    "type": "run_terminal",
                    "event_type": "run_terminal",
                    "ts": run["completed_at"],
                    "run_id": run_id,
                    "task_id": None,
                }
            )
    return derived


# --------------------------------------------------------------------------
# Snapshot builder (§5)
# --------------------------------------------------------------------------


def build_workspace_home_snapshot(
    *,
    execution_center_api: ExecutionCenterAPI,
    active_runs_limit: int = 20,
    recent_runs_limit: int = 20,
    activity_limit: int = 20,
    artifacts_limit: int = 20,
    reports_limit: int = 20,
) -> dict:
    """`execution_center_api` is injected — this function never constructs its
    own `ExecutionCenterAPI`/`Supervisor`. Every list in the returned dict is
    bounded by the corresponding `*_limit` argument."""
    project_configs = project_config.load_project_configs()

    v2_active_raw = execution_center_api.list_runs(
        states=runtime_db.EXECUTION_CENTER_ACTIVE_STATES, limit=active_runs_limit
    )
    v2_terminal_raw = execution_center_api.list_runs(
        states=runtime_db.TERMINAL_STATES, limit=recent_runs_limit
    )
    v1_2_raw = agent_runner.load_runs()[:recent_runs_limit]

    v2_active_tagged = [_tag_run(run, "v2") for run in v2_active_raw]
    for run in v2_active_tagged:
        run["duration_seconds"] = _run_duration_seconds(run)
    v2_terminal_tagged = [_tag_run(run, "v2") for run in v2_terminal_raw]
    for run in v2_terminal_tagged:
        run["duration_seconds"] = _run_duration_seconds(run)
    v1_2_tagged = [_tag_run(run, "v1.2") for run in v1_2_raw]

    report_files = artifact_discovery.list_markdown_files(REPORTS_DIR)[:reports_limit]
    artifact_files = artifact_discovery.list_markdown_files(GENERATED_DIR)[:artifacts_limit]
    activity_raw = activity_log.load_activity(limit=activity_limit)

    v1_2_by_report_path = {
        run["report_path"]: run for run in agent_runner.load_runs() if run.get("report_path")
    }
    v2_report_rows: dict[str, tuple[dict, dict]] = {}
    for run in v2_terminal_tagged:
        report_row = execution_center_api.get_report(run["run_id"])
        if report_row:
            v2_report_rows[report_row["path"]] = (run, report_row)

    projects_out: list[dict] = []
    worktrees_by_project: dict[str, dict] = {}
    active_runs_out: list[dict] = []
    recent_runs_out: list[dict] = []
    activity_out: list[dict] = []
    artifacts_out: list[dict] = []
    reports_out: list[dict] = []

    for project_id, cfg in project_configs.items():
        proj_active = [run for run in v2_active_tagged if run.get("project") == project_id]
        proj_recent = [run for run in v2_terminal_tagged if run.get("project") == project_id] + [
            run for run in v1_2_tagged if run.get("project") == project_id
        ]

        proj_reports_raw: list[dict] = []
        for path in report_files:
            if artifact_discovery.project_from_path(path, REPORTS_DIR) != project_id:
                continue
            rel = f"reports/{path.relative_to(REPORTS_DIR)}"
            if rel in v1_2_by_report_path:
                run = v1_2_by_report_path[rel]
                parsed = run.get("parsed") or {}
                proj_reports_raw.append(
                    {
                        "run_id": run.get("id"),
                        "source": "v1.2",
                        "project": project_id,
                        "verdict": parsed.get("verdict"),
                        "severity_counts": report_parser.severity_counts(parsed),
                        "created_at": run.get("completed_at") or run.get("created_at"),
                    }
                )
            elif rel in v2_report_rows:
                run, report_row = v2_report_rows[rel]
                parsed = report_parser.parse_report(artifact_discovery.read_text(path))
                proj_reports_raw.append(
                    {
                        "run_id": run.get("run_id"),
                        "source": "v2",
                        "project": project_id,
                        "verdict": parsed.get("verdict"),
                        "severity_counts": report_parser.severity_counts(parsed),
                        "created_at": report_row.get("created_at"),
                    }
                )
            # else: a discovered report file with no known backing run is not
            # included — Reports is defined as run-linked structured reports.

        proj_artifact_paths = [
            path
            for path in artifact_files
            if artifact_discovery.project_from_path(path, GENERATED_DIR) == project_id
        ]

        proj_activity_raw = []
        for event in activity_raw:
            if event.get("project") != project_id:
                continue
            enriched = dict(event)
            enriched.setdefault("event_type", event.get("type"))
            proj_activity_raw.append(enriched)

        sanitized = sanitize_workspace_project_entry(
            project_id,
            runs=proj_active + proj_recent,
            reports=proj_reports_raw,
            artifacts=proj_artifact_paths,
            activity=proj_activity_raw,
        )

        sanitized_by_identity = {(r["source"], r["run_id"]): r for r in sanitized["runs"]}
        proj_active_sanitized = [sanitized_by_identity[(r["source"], r["run_id"])] for r in proj_active]
        proj_recent_sanitized = [sanitized_by_identity[(r["source"], r["run_id"])] for r in proj_recent]

        derived_activity = _derive_run_activity(proj_active_sanitized + proj_recent_sanitized)
        combined_activity = sanitized["activity"] + derived_activity

        projects_out.append(
            {
                "id": project_id,
                "display_name": cfg.get("display_name", project_id),
                "sensitive": project_config.is_sensitive(project_id),
                "repository_path": cfg.get("repository_path"),
                "repository_path_configured": _configured_repository_path(cfg) is not None,
                "active_run_count": len(proj_active_sanitized),
            }
        )
        worktrees_by_project[project_id] = _project_worktrees(cfg)
        active_runs_out.extend(proj_active_sanitized)
        recent_runs_out.extend(proj_recent_sanitized)
        activity_out.extend(combined_activity)
        artifacts_out.extend(sanitized["artifacts"])
        reports_out.extend(sanitized["reports"])

    active_runs_out.sort(key=lambda r: _sort_key(r, "created_at"), reverse=True)
    recent_runs_out.sort(key=lambda r: _sort_key(r, "created_at"), reverse=True)
    activity_out.sort(key=lambda e: _sort_key(e, "ts"), reverse=True)
    artifacts_out.sort(key=lambda a: _sort_key(a, "created_at"), reverse=True)
    reports_out.sort(key=lambda r: _sort_key(r, "created_at"), reverse=True)

    return {
        "projects": projects_out,
        "worktrees_by_project": worktrees_by_project,
        "active_runs": active_runs_out[:active_runs_limit],
        "recent_runs": recent_runs_out[:recent_runs_limit],
        "recent_activity": activity_out[:activity_limit],
        "artifacts": artifacts_out[:artifacts_limit],
        "reports": reports_out[:reports_limit],
    }
