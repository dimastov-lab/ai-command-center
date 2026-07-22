"""Execution Queue: an explicit, persisted "what should launch next" list —
distinct from both the Kanban planning lane (`task["status"]`) and the real
execution state (`runtime.db`, per ADR 0003).

Design intent, per the founder decision this module implements:

- **No hidden scheduler.** Nothing in this module launches anything on its
  own initiative. `evaluate_readiness`/`reevaluate_and_persist` only ever
  *recompute a label* (waiting vs. ready) from the existing dependency graph
  — deterministic, side-effect-free besides persisting that label. Only
  `launch_ready`, called from an explicit button click in the UI layer,
  starts a process, and only for entries already in the `READY` state.
- **Deterministic readiness, not a background poll.** A queue entry becomes
  `READY` the moment its evaluation is *run* after its dependencies are
  satisfied — not automatically the instant that happens. The caller is
  responsible for calling `reevaluate_and_persist` at the checkpoints that
  matter (app load, manual refresh, right after a task-state change, right
  after a run reaches a terminal state) — see `command_center.ui.queue_panel`
  for where those checkpoints are wired into the Streamlit app today.
- **A future background worker can reuse this unmodified.** Every function
  here is plain Python over plain dicts and the existing `ExecutionCenterAPI`
  / `launch` / `launch_service` primitives — no Streamlit import, no
  in-process state. A future Supervisor-driven poller can call
  `reevaluate_and_persist` and `launch_ready` on a timer against the same
  `data/execution_queue.json` this module already writes, without this
  module (or its persisted shape) changing at all. That poller does not
  exist yet and is explicitly out of scope here — see the module's ADR.

Storage: `data/execution_queue.json`, one JSON list of queue-entry dicts,
using the same atomic whole-file read/write convention as `tasks.json` (see
`command_center.storage`). Deliberately *not* a new `runtime.db` table: the
queue is a planning-level "what do I want to launch, in what order" list,
upstream of `runtime.db`'s job (ADR 0003) of being the sole source of truth
for "is this actually running" — putting it in the same store as run/session
state would blur that boundary for no benefit; a JSON list is exactly as
reusable by a future worker and carries zero migration risk to the frozen
Supervisor schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from command_center import agent_runner, launch, launch_service, models, storage

QUEUE_FILE_NAME = "execution_queue.json"

STATE_WAITING = "waiting"
STATE_READY = "ready"
STATE_LAUNCHED = "launched"
STATE_CANCELLED = "cancelled"

# Non-terminal — still tracked, still re-evaluated on every checkpoint.
OPEN_STATES: frozenset[str] = frozenset({STATE_WAITING, STATE_READY})


def queue_file_path(root: Path) -> Path:
    return storage.resolve_data_dir(root) / QUEUE_FILE_NAME


def load_queue(root: Path) -> list[dict]:
    return storage.read_json(queue_file_path(root), [])


def save_queue(root: Path, entries: list[dict]) -> None:
    storage.atomic_write_json(queue_file_path(root), entries)


def _new_entry(task: dict) -> dict:
    return {
        "id": models.new_id(),
        "task_id": task.get("id"),
        "project": task.get("project"),
        "state": STATE_WAITING,
        "reason": None,
        "run_id": None,
        "added_at": models.iso_now(),
        "evaluated_at": None,
        "launched_at": None,
    }


def enqueue(entries: list[dict], task: dict, tasks_by_id: dict[str, dict]) -> list[dict]:
    """Adds `task` to the queue unless it already has an open (waiting/ready)
    entry — re-enqueuing an already-queued task is a no-op, not a duplicate.
    A task already `Done` is never enqueued. Returns the new entry list;
    readiness is evaluated immediately so the caller never has to render a
    freshly-added entry with a stale state."""
    task_id = task.get("id")
    if task_id is None:
        return entries
    if task.get("status") == "Done":
        return entries
    if any(e.get("task_id") == task_id and e.get("state") in OPEN_STATES for e in entries):
        return entries
    updated = [*entries, _new_entry(task)]
    return evaluate_readiness(updated, tasks_by_id)


def dequeue(entries: list[dict], entry_id: str) -> list[dict]:
    """Removes an entry outright (used for waiting/ready entries the user no
    longer wants queued). Launched/cancelled entries are left alone by every
    other function in this module and are expected to age out via whatever
    retention policy the caller chooses — this module keeps no opinion on
    that beyond "never delete a launched entry silently"."""
    return [e for e in entries if e.get("id") != entry_id]


def evaluate_readiness(entries: list[dict], tasks_by_id: dict[str, dict]) -> list[dict]:
    """Pure recomputation of every open entry's `state`/`reason` from the
    *current* dependency graph and task status — never launches anything,
    never touches the filesystem. Safe to call on every Streamlit rerun.

    - A task that no longer exists, or has reached `Done` outside the queue
      (e.g. a manual Kanban move), is cancelled — its dependents were the
      point of tracking it, and a finished task has nothing left to wait on.
    - Otherwise: `READY` if `models.is_blocked` says no, else `WAITING` with
      a human-readable reason naming the unmet dependencies.
    """
    updated: list[dict] = []
    for entry in entries:
        if entry.get("state") not in OPEN_STATES:
            updated.append(entry)
            continue

        task = tasks_by_id.get(entry.get("task_id"))
        entry = dict(entry)
        entry["evaluated_at"] = models.iso_now()

        if task is None:
            entry["state"] = STATE_CANCELLED
            entry["reason"] = "задача больше не существует"
        elif task.get("status") == "Done":
            entry["state"] = STATE_CANCELLED
            entry["reason"] = "задача уже завершена"
        else:
            unmet = models.unmet_dependencies(task, tasks_by_id)
            if unmet:
                names = ", ".join((tasks_by_id.get(dep_id, {}).get("title") or dep_id) for dep_id in unmet)
                entry["state"] = STATE_WAITING
                entry["reason"] = f"ожидает: {names}"
            else:
                entry["state"] = STATE_READY
                entry["reason"] = None

        updated.append(entry)
    return updated


def reevaluate_and_persist(root: Path, tasks_by_id: dict[str, dict]) -> list[dict]:
    """The single call the UI layer makes at each of the four checkpoints
    (app load, manual refresh, after a task-state change, after a run
    reaches a terminal state) — loads, re-evaluates, saves, returns. Never
    launches anything; see `launch_ready` for the only function in this
    module that does."""
    entries = evaluate_readiness(load_queue(root), tasks_by_id)
    save_queue(root, entries)
    return entries


def ready_entries(entries: list[dict]) -> list[dict]:
    return [e for e in entries if e.get("state") == STATE_READY]


def waiting_entries(entries: list[dict]) -> list[dict]:
    return [e for e in entries if e.get("state") == STATE_WAITING]


@dataclass
class LaunchAttemptResult:
    entry_id: str
    task_id: str | None
    launched: bool
    run_id: str | None = None
    message: str = ""
    # Populated only for the "blocked by validation warnings" case (dirty
    # tree, detached HEAD, branch mismatch) — the exact strings from
    # `launch.LaunchValidation.warnings`, so the UI layer can render each as
    # its own bullet instead of collapsing them into `message`'s generic
    # summary. Empty for every other skip/failure reason (missing workspace,
    # task not found, launch exception).
    warnings: list[str] = field(default_factory=list)
    # Full `launch.validate_launch` result for the same case, for an
    # optional "Details" expander in the UI — never used to gate anything,
    # purely informational.
    validation_report: dict | None = None


def _validation_report(validation: launch.LaunchValidation, *, expected_branch: str | None) -> dict:
    """The complete pre-flight picture for one skipped launch — everything
    `validate_launch` observed, not just the warning strings — for the UI's
    "Details" expander. Read-only summary; never used for control flow."""
    git_status = validation.git_status or {}
    return {
        "workspace_path": validation.workspace_path,
        "expected_branch": expected_branch,
        "actual_branch": git_status.get("branch"),
        "errors": list(validation.errors),
        "warnings": list(validation.warnings),
        "git_status": dict(git_status),
    }


def launch_ready(
    root: Path,
    entries: list[dict],
    tasks: list[dict],
    tasks_by_id: dict[str, dict],
    project_configs: dict[str, dict],
    execution_center_api,
    *,
    entry_ids: list[str] | None = None,
) -> tuple[list[dict], list[LaunchAttemptResult]]:
    """Launches every `READY` entry (or, if `entry_ids` is given, just those
    — the "launch next ready task" action passes a single id). Never called
    from a plain render path — only from an explicit button-click handler in
    the UI layer (see `command_center.ui.queue_panel`), and never invoked
    implicitly by `evaluate_readiness`/`reevaluate_and_persist`.

    An entry whose validation carries *warnings* (dirty tree, detached HEAD,
    branch mismatch) is deliberately **not** auto-launched — those normally
    require an explicit human checkbox acknowledgement in
    `render_agent_launcher`, and a batch/queue action has no per-task human
    in the loop to give it. Such an entry is left `READY` (still queued,
    still visible) with a message pointing at the task's own launcher for a
    manual, confirmed launch. An entry with a blocking *error* (missing/
    invalid workspace) is treated the same way — left in place, reported,
    never silently dropped.

    Returns the updated entry list (callers persist it via `save_queue`, and
    must separately persist `tasks` via `tasks_repository.save_tasks` — this
    function mutates `task` dicts in place exactly like
    `launch_service.execute_agent_launch_v2` already does) and one
    `LaunchAttemptResult` per entry considered, in order, for the caller to
    render a per-entry outcome."""
    targets = ready_entries(entries)
    if entry_ids is not None:
        wanted = set(entry_ids)
        targets = [e for e in targets if e.get("id") in wanted]

    results: list[LaunchAttemptResult] = []
    updated_by_id = {e["id"]: dict(e) for e in entries}

    for entry in targets:
        task_id = entry.get("task_id")
        task = tasks_by_id.get(task_id)
        if task is None:
            results.append(LaunchAttemptResult(entry["id"], task_id, False, message="задача не найдена"))
            continue

        cfg = project_configs.get(task.get("project"), {})
        selection = launch.resolve_workspace_path(task=task, project_config=cfg)
        if not selection.path:
            results.append(
                LaunchAttemptResult(entry["id"], task_id, False, message="workspace не настроен для задачи")
            )
            continue

        expected_branch = launch.resolve_expected_branch(task=task, project_config=cfg)
        base_branch = launch.resolve_base_branch(task=task, project_config=cfg)
        source_repository_path = cfg.get("repository_path")
        validation = launch.validate_launch(workspace_path=selection.path, expected_branch=expected_branch)
        if not validation.can_launch:
            results.append(
                LaunchAttemptResult(
                    entry["id"], task_id, False, message="; ".join(validation.errors) or "запуск заблокирован"
                )
            )
            continue
        if validation.warnings:
            results.append(
                LaunchAttemptResult(
                    entry["id"],
                    task_id,
                    False,
                    message="требует подтверждения предупреждений — запустите вручную из карточки задачи",
                    warnings=list(validation.warnings),
                    validation_report=_validation_report(validation, expected_branch=expected_branch),
                )
            )
            continue

        resolved_workspace = Path(selection.path).expanduser().resolve()
        try:
            run = launch_service.execute_agent_launch_v2(
                project=task.get("project"),
                task_type=task.get("task_type") or "implementation",
                prompt=task.get("prompt") or task.get("goal") or task.get("title") or "",
                timeout_seconds=agent_runner.DEFAULT_TIMEOUT_SECONDS,
                repository_path=resolved_workspace,
                execution_center_api=execution_center_api,
                confirmed=True,
                task=task,
                executor_id=task.get("executor") or "claude_code",
                validation=validation,
                expected_branch=expected_branch,
                base_branch=base_branch,
                source_repository_path=source_repository_path,
            )
        except launch_service.DuplicateActiveLaunchError as exc:
            results.append(LaunchAttemptResult(entry["id"], task_id, False, message=str(exc)))
            continue
        except Exception as exc:  # noqa: BLE001 — one bad entry must not abort the batch
            results.append(LaunchAttemptResult(entry["id"], task_id, False, message=str(exc)))
            continue

        launched_entry = updated_by_id[entry["id"]]
        launched_entry["state"] = STATE_LAUNCHED
        launched_entry["run_id"] = run["id"]
        launched_entry["launched_at"] = models.iso_now()
        launched_entry["reason"] = None
        results.append(LaunchAttemptResult(entry["id"], task_id, True, run_id=run["id"]))

    updated_entries = [updated_by_id[e["id"]] for e in entries]
    save_queue(root, updated_entries)
    return updated_entries, results
