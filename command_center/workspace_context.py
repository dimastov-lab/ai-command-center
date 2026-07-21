"""`WorkspaceContext` -- the single shared answer to "what is the user looking
at right now," replacing the ~10 scattered `st.session_state` selection keys
(`create_task_project`, `chat_conv_select`, `exec_center_highlight_run`, ...)
enumerated in `docs/workspace/WORKSPACE_CONTEXT_MODEL.md`.

This module is plain Python: no Streamlit import, no writes to any existing
store. It only ever *reads* `tasks_repository`, `runtime.db`, `project_config`
and `git_info` to validate and fan out a selection -- see
`docs/workspace/DATA_OWNERSHIP.md` rule 5 and
`docs/workspace/MODULE_DEPENDENCY_MAP.md`'s forbidden-edges list. Nothing in
`app.py` calls this module yet (see `docs/workspace/IMPLEMENTATION_ROADMAP.md`,
UW-1's "Feature flag / compatibility strategy: none needed"), so introducing it
cannot regress any existing behavior.

Implementation notes where this module makes a concrete choice the
architecture docs left as a sketch:

- `apply()` takes `root: Path` as its first argument (mirroring
  `reconcile(root, ctx)`), even though
  `WORKSPACE_CONTEXT_MODEL.md`'s abbreviated "Public API" listing omits it --
  `SelectTask`/`OpenRun`/`OpenBranch`/`OpenArtifact` all require reading an
  owning store (`tasks_repository`, `runtime.db`, `git_info`, the filesystem)
  to validate and fan out, which is not possible without a root path.
- `OpenRun`'s "fan out `selected_task_id`" (`EVENT_AND_COMMAND_MODEL.md`) does
  *not* use `run["task_id"]` directly: that column is `runtime.db`'s own
  internal v2 task id, a different id space from `selected_task_id`, which
  (per `DATA_OWNERSHIP.md`'s domain model) is a `tasks_repository.py`
  (`data/tasks.json`) Kanban task id. The only existing bridge between the two
  is `current_run_id`, written onto the Kanban task by
  `launch_service.py`/`runtime/task_sync.py`. This module resolves a run back
  to its Kanban task by scanning for `task["current_run_id"] == run_id` --
  the same direction that bridge already flows, just read instead of written.
  A run with no matching Kanban task (e.g. launched outside the Kanban flow)
  degrades gracefully: the run is still selected, just without a task fan-out.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path

from command_center import git_info, models, panel_registry, project_config, storage, tasks_repository
from command_center.runtime import db as runtime_db

WORKSPACE_STATE_FILENAME = "workspace_state.json"
SCHEMA_VERSION = 1

# Exactly the fields `WORKSPACE_CONTEXT_MODEL.md`'s "Persisted (whitelisted)"
# tier names -- everything else on `WorkspaceContext` is session-only or
# derived, and must never reach `workspace_state.json` (see that document's
# "Persistence tiers" table for the full rationale per field).
PERSISTED_FIELDS: tuple[str, ...] = (
    "active_project",
    "selected_task_id",
    "selected_run_id",
    "selected_repository_path",
)


@dataclass(frozen=True)
class WorkspaceContext:
    # Selection -- see WORKSPACE_CONTEXT_MODEL.md "Selection fields" for the
    # owning store each field resolves against. Ids only, never entities.
    active_project: str | None = None
    selected_task_id: str | None = None
    selected_run_id: str | None = None
    selected_agent_id: str | None = None
    selected_repository_path: str | None = None
    selected_branch: str | None = None
    selected_artifact_path: str | None = None

    # Navigation / panel UI state -- session-only, never persisted.
    active_panel: str = "workspace_home"
    active_filters: dict[str, object] = field(default_factory=dict)
    inspector_mode: str | None = None
    inspector_pinned: bool = False
    drawer_state: str = "collapsed"
    drawer_tab: str | None = None

    # Bookkeeping
    updated_at: str = field(default_factory=models.iso_now)
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True)
class BreadcrumbItem:
    kind: str  # "project" | "task" | "run"
    entity_id: str


# --------------------------------------------------------------------------
# Commands -- one dataclass per `workspace_context.apply` handler in
# EVENT_AND_COMMAND_MODEL.md's command table. Commands whose handler lives
# elsewhere (`QueueTask`, `LaunchTask`, `CancelRun`, `PinContext`,
# `RefreshWorkspace`) are out of scope for this module.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SelectProject:
    project_id: str


@dataclass(frozen=True)
class SelectTask:
    task_id: str


@dataclass(frozen=True)
class OpenRun:
    run_id: str


@dataclass(frozen=True)
class OpenBranch:
    repository_path: str
    branch: str


@dataclass(frozen=True)
class OpenArtifact:
    artifact_path: str


WorkspaceCommand = SelectProject | SelectTask | OpenRun | OpenBranch | OpenArtifact


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------


def _state_file_path(root: Path) -> Path:
    return storage.resolve_data_dir(root) / WORKSPACE_STATE_FILENAME


def load(root: Path) -> WorkspaceContext:
    """Reads `workspace_state.json`'s whitelisted fields; every other field
    starts from its session-only default. An unknown/future `schema_version`
    or a missing file both degrade to defaults rather than raising -- this
    function never crashes app startup."""
    raw = storage.read_json(_state_file_path(root), {})
    if not isinstance(raw, dict):
        raw = {}
    kwargs = {name: raw[name] for name in PERSISTED_FIELDS if name in raw}
    return WorkspaceContext(**kwargs)


def save(root: Path, ctx: WorkspaceContext) -> None:
    """Writes only the persisted-tier fields -- session-only/derived fields
    (`active_panel`, `active_filters`, `selected_branch`, ...) never reach
    disk, per `WORKSPACE_CONTEXT_MODEL.md`'s persistence-tier split."""
    data = {name: getattr(ctx, name) for name in PERSISTED_FIELDS}
    data["updated_at"] = models.iso_now()
    data["schema_version"] = SCHEMA_VERSION
    storage.atomic_write_json(_state_file_path(root), data)


# --------------------------------------------------------------------------
# Reconciliation
# --------------------------------------------------------------------------


def reconcile(root: Path, ctx: WorkspaceContext) -> WorkspaceContext:
    """Pure (aside from the read-only lookups it performs) validation pass,
    run on every load before a panel renders against `ctx`. Never raises --
    every step degrades a stale/contradictory field to `None`/a safe default,
    per `WORKSPACE_CONTEXT_MODEL.md`'s reconciliation flowchart. Order
    matters: run is resolved first (the most specific signal), which may
    rewrite `selected_task_id`; task is resolved next, which may rewrite
    `active_project`; project validity is checked last of the selection
    fields, using whatever `active_project` the earlier steps landed on."""
    tasks = tasks_repository.load_tasks(root)
    tasks_by_id = {task["id"]: task for task in tasks if task.get("id")}

    # Run -- most specific signal; may fan out selected_task_id.
    if ctx.selected_run_id:
        db_path = runtime_db.resolve_db_path(root)
        runtime_db.migrate(db_path)
        run = runtime_db.get_run(db_path, ctx.selected_run_id)
        if run is None:
            ctx = dataclasses.replace(ctx, selected_run_id=None)
        else:
            matched_task = next(
                (task for task in tasks if task.get("current_run_id") == ctx.selected_run_id),
                None,
            )
            matched_task_id = matched_task.get("id") if matched_task else None
            if matched_task_id is not None and matched_task_id != ctx.selected_task_id:
                ctx = dataclasses.replace(ctx, selected_task_id=matched_task_id)

    # Task -- may fan out active_project.
    if ctx.selected_task_id:
        task = tasks_by_id.get(ctx.selected_task_id)
        if task is None:
            ctx = dataclasses.replace(ctx, selected_task_id=None, selected_run_id=None)
        else:
            # Canonicalize the task's stored `project` (shared helper): a task
            # may store a display name ("AI Command Center") while
            # `active_project` — and the `models.PROJECT_IDS` guard just below —
            # are canonical ids ("AICC"). Without this a display-name task set a
            # non-canonical `active_project` that the guard then nulled out, so
            # its project could never stay selected in the workspace.
            task_project = project_config.canonical_project_id(task.get("project"))
            if task_project != ctx.active_project:
                ctx = dataclasses.replace(ctx, active_project=task_project)

    # Project.
    if ctx.active_project is not None and ctx.active_project not in models.PROJECT_IDS:
        ctx = dataclasses.replace(ctx, active_project=None)

    # Repository / branch (branch is always derived from repository).
    if ctx.selected_repository_path is not None and not Path(ctx.selected_repository_path).is_dir():
        ctx = dataclasses.replace(ctx, selected_repository_path=None, selected_branch=None)

    # Artifact.
    if ctx.selected_artifact_path is not None and not Path(ctx.selected_artifact_path).exists():
        ctx = dataclasses.replace(ctx, selected_artifact_path=None)

    # Panel.
    if ctx.active_panel not in panel_registry.PANELS:
        ctx = dataclasses.replace(ctx, active_panel="workspace_home")

    return ctx


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def _apply_select_project(root: Path, ctx: WorkspaceContext, command: SelectProject) -> WorkspaceContext:
    if command.project_id not in models.PROJECT_IDS:
        raise ValueError(f"Unknown project: {command.project_id!r}")

    updates: dict[str, object] = {"active_project": command.project_id}
    if ctx.selected_task_id:
        tasks = tasks_repository.load_tasks(root)
        task = next((t for t in tasks if t.get("id") == ctx.selected_task_id), None)
        # Compare the task's project on canonical ids (shared helper): a
        # display-name task ("AI Command Center") must stay selected when its
        # canonical project ("AICC") is chosen, not be deselected as a mismatch.
        if task is None or not project_config.project_matches(task.get("project"), command.project_id):
            updates["selected_task_id"] = None
            updates["selected_run_id"] = None
    return dataclasses.replace(ctx, **updates)


def _apply_select_task(root: Path, ctx: WorkspaceContext, command: SelectTask) -> WorkspaceContext:
    tasks = tasks_repository.load_tasks(root)
    task = next((t for t in tasks if t.get("id") == command.task_id), None)
    if task is None:
        raise ValueError(f"Task not found: {command.task_id!r}")

    # Canonical id (shared helper) so a display-name task resolves to its real
    # project config and sets a canonical `active_project` that survives the
    # `models.PROJECT_IDS` reconciliation in `reconcile_context`.
    project_id = project_config.canonical_project_id(task.get("project"))
    cfg = project_config.get_project_config(project_id) if project_id else None
    repository_path = task.get("workspace_path") or (cfg.get("repository_path") if cfg else None)

    updates: dict[str, object] = {
        "selected_task_id": command.task_id,
        "active_project": project_id,
        "selected_repository_path": repository_path,
    }
    if ctx.selected_run_id and task.get("current_run_id") != ctx.selected_run_id:
        updates["selected_run_id"] = None
        updates["selected_branch"] = None
    return dataclasses.replace(ctx, **updates)


def _apply_open_run(root: Path, ctx: WorkspaceContext, command: OpenRun) -> WorkspaceContext:
    db_path = runtime_db.resolve_db_path(root)
    runtime_db.migrate(db_path)
    run = runtime_db.get_run(db_path, command.run_id)
    if run is None:
        raise ValueError(f"Run not found: {command.run_id!r}")

    updates: dict[str, object] = {
        "selected_run_id": command.run_id,
        "active_panel": "runtime",
        "inspector_mode": "run",
    }
    tasks = tasks_repository.load_tasks(root)
    matched_task = next((t for t in tasks if t.get("current_run_id") == command.run_id), None)
    if matched_task is not None:
        updates["selected_task_id"] = matched_task.get("id")
        # Canonical id (shared helper) so a display-name task/run sets a
        # canonical `active_project` that survives `models.PROJECT_IDS`
        # reconciliation instead of being nulled out.
        updates["active_project"] = project_config.canonical_project_id(matched_task.get("project"))
    elif run.get("project"):
        updates["active_project"] = project_config.canonical_project_id(run.get("project"))
    return dataclasses.replace(ctx, **updates)


def _apply_open_branch(root: Path, ctx: WorkspaceContext, command: OpenBranch) -> WorkspaceContext:
    del root  # validation is filesystem/git-only; no owning-store lookup needed
    path = Path(command.repository_path)
    if not path.is_dir():
        raise ValueError(f"Repository path does not exist: {command.repository_path!r}")
    branches = git_info.get_branches(path)
    if branches and command.branch not in branches:
        raise ValueError(f"Branch not found in repository: {command.branch!r}")
    return dataclasses.replace(
        ctx,
        selected_repository_path=str(path),
        selected_branch=command.branch,
        active_panel="git",
    )


def _apply_open_artifact(root: Path, ctx: WorkspaceContext, command: OpenArtifact) -> WorkspaceContext:
    del root  # validation is filesystem-only; no owning-store lookup needed
    path = Path(command.artifact_path)
    if not path.exists():
        raise ValueError(f"Artifact not found: {command.artifact_path!r}")
    return dataclasses.replace(ctx, selected_artifact_path=str(path), inspector_mode="artifact")


def apply(root: Path, ctx: WorkspaceContext, command: WorkspaceCommand) -> WorkspaceContext:
    """Every command is a plain function of `(root, ctx, command) ->
    WorkspaceContext`; none import Streamlit, none write to any store (they
    only validate against one), and an invalid command (unknown project,
    deleted task, missing run, invalid branch/artifact) raises `ValueError`
    rather than silently no-op-ing -- unlike `reconcile()`, which exists
    precisely to silently heal *already-persisted* staleness, `apply()` is
    called against a live, user-initiated selection, where a caller passing a
    bad id is a bug worth surfacing immediately."""
    if isinstance(command, SelectProject):
        return _apply_select_project(root, ctx, command)
    if isinstance(command, SelectTask):
        return _apply_select_task(root, ctx, command)
    if isinstance(command, OpenRun):
        return _apply_open_run(root, ctx, command)
    if isinstance(command, OpenBranch):
        return _apply_open_branch(root, ctx, command)
    if isinstance(command, OpenArtifact):
        return _apply_open_artifact(root, ctx, command)
    raise TypeError(f"Unsupported workspace command: {command!r}")


# --------------------------------------------------------------------------
# Derived, never persisted
# --------------------------------------------------------------------------


def breadcrumbs(ctx: WorkspaceContext) -> list[BreadcrumbItem]:
    """Project -> Task -> Run, truncated to whatever prefix is non-`None`.
    Ids only -- resolving an id to a human-readable label is the caller's
    (a panel's, via `workspace_service`) job, not this function's, per the
    identifier-based-state rule in `WORKSPACE_PRINCIPLES.md`."""
    items: list[BreadcrumbItem] = []
    if not ctx.active_project:
        return items
    items.append(BreadcrumbItem("project", ctx.active_project))
    if not ctx.selected_task_id:
        return items
    items.append(BreadcrumbItem("task", ctx.selected_task_id))
    if not ctx.selected_run_id:
        return items
    items.append(BreadcrumbItem("run", ctx.selected_run_id))
    return items
