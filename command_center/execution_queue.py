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

Cross-process consistency: `storage.atomic_write_json`'s temp-file +
`os.replace` makes any *single* queue write atomic, but does nothing to stop a
lost update across a read-modify-write *cycle* — two concurrent callers (two
Streamlit sessions, a session plus the future Supervisor-driven poller the
module docstring above anticipates) can both `load_queue`, each recompute from
that same pre-write snapshot, and the second `save_queue` silently discards the
first's change. Every read-modify-write cycle this module owns therefore runs
inside `queue_lock` (a thin wrapper over `command_center.storage.file_lock`, the
same OS advisory-lock primitive `portfolio_launch` and `task_import` already use
for their JSON stores): `reevaluate_and_persist`, the `*_and_persist` helpers,
and `launch_ready`'s state-commit all hold it across their whole load→save span.
`load_queue`/`save_queue`/`enqueue`/`dequeue`/`evaluate_readiness` keep their
existing signatures unchanged — a caller that already serializes its own access
(or genuinely wants a lock-free read) can still use them directly; the locked
`*_and_persist` variants are the safe default for a bare read-modify-write.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from pathlib import Path

from command_center import (
    agent_runner,
    launch,
    launch_service,
    models,
    project_config,
    storage,
    workspace_provisioning,
)
from command_center.runtime import supervisor as runtime_supervisor

QUEUE_FILE_NAME = "execution_queue.json"

STATE_WAITING = "waiting"
STATE_READY = "ready"
STATE_LAUNCHED = "launched"
STATE_CANCELLED = "cancelled"

# Non-terminal — still tracked, still re-evaluated on every checkpoint.
OPEN_STATES: frozenset[str] = frozenset({STATE_WAITING, STATE_READY})


QUEUE_LOCK_FILE_NAME = "execution_queue.lock"
QUEUE_LOCK_TIMEOUT_SECONDS = 30.0
_QUEUE_LOCK_POLL_SECONDS = 0.05


def queue_file_path(root: Path) -> Path:
    return storage.resolve_data_dir(root) / QUEUE_FILE_NAME


def queue_lock_path(root: Path) -> Path:
    """The dedicated lock file guarding `execution_queue.json`'s
    read-modify-write cycle — a sibling of the queue file, never the queue file
    itself, so a lock holder never blocks a plain unlocked `load_queue` read."""
    return storage.resolve_data_dir(root) / QUEUE_LOCK_FILE_NAME


def load_queue(root: Path) -> list[dict]:
    return storage.read_json(queue_file_path(root), [])


def save_queue(root: Path, entries: list[dict]) -> None:
    storage.atomic_write_json(queue_file_path(root), entries)


@contextlib.contextmanager
def queue_lock(root: Path, *, timeout: float = QUEUE_LOCK_TIMEOUT_SECONDS):
    """Cross-process, cross-thread mutual exclusion for the *entire*
    read-modify-write cycle on `data/execution_queue.json` — acquire this
    before the `load_queue` and hold it through the `save_queue`, never just
    around the write, or two callers can still both read the same pre-write
    snapshot and each write back a version missing the other's change (the
    lost update this lock exists to prevent).

    Thin wrapper over `command_center.storage.file_lock` (see that function's
    docstring for the underlying `fcntl`/`msvcrt` OS advisory-lock mechanism,
    including that a crashed holder never wedges future callers). A
    `storage.LockTimeoutError` propagates unchanged — a bounded, descriptive
    failure instead of an indefinite hang."""
    with storage.file_lock(queue_lock_path(root), timeout=timeout, poll_seconds=_QUEUE_LOCK_POLL_SECONDS):
        yield


def _mutate_queue(root: Path, transform, *, timeout: float = QUEUE_LOCK_TIMEOUT_SECONDS) -> list[dict]:
    """Run one `load_queue -> transform -> save_queue` cycle entirely inside
    `queue_lock`, so the read `transform` is computed from and the write that
    commits it are adjacent and no concurrent writer can interleave between
    them. `transform` takes the freshly-loaded entries and returns the entries
    to persist."""
    with queue_lock(root, timeout=timeout):
        updated = transform(load_queue(root))
        save_queue(root, updated)
        return updated


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
    reaches a terminal state) — loads, re-evaluates, saves, returns, with the
    whole load→save cycle held under `queue_lock` so a concurrent writer's
    update is never lost. Never launches anything; see `launch_ready` for the
    only function in this module that does."""
    return _mutate_queue(root, lambda entries: evaluate_readiness(entries, tasks_by_id))


def enqueue_and_persist(root: Path, task: dict, tasks_by_id: dict[str, dict]) -> list[dict]:
    """Lost-update-safe `load_queue -> enqueue -> save_queue`: the whole cycle
    runs under `queue_lock`, so adding a task to the queue never clobbers a
    concurrent re-evaluation or another enqueue in a different process. Prefer
    this over hand-rolling the `load_queue`/`enqueue`/`save_queue` triple."""
    return _mutate_queue(root, lambda entries: enqueue(entries, task, tasks_by_id))


def dequeue_and_persist(root: Path, entry_id: str) -> list[dict]:
    """Lost-update-safe `load_queue -> dequeue -> save_queue`: removing an
    entry re-reads the current on-disk queue under `queue_lock` before writing,
    so it never silently reverts a concurrent writer's change back to the stale
    snapshot the caller happened to be holding."""
    return _mutate_queue(root, lambda entries: dequeue(entries, entry_id))


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

    Persistence is cross-process safe: the launched-state transitions are
    committed under `queue_lock`, re-reading the current on-disk queue and
    merging onto it (see `_commit_launch_results`), so a concurrent
    `reevaluate_and_persist`/enqueue in another process is preserved rather
    than clobbered by this call's snapshot. The actual process launches happen
    *outside* the lock — it is held only for the final merge-and-save, never
    across a subprocess spawn. This function still saves the queue itself; the
    returned entry list is the merged result (callers may persist it again
    harmlessly), and callers must separately persist `tasks` via
    `tasks_repository.save_tasks` — this function mutates `task` dicts in place
    exactly like `launch_service.execute_agent_launch_v2` already does. One
    `LaunchAttemptResult` per entry considered is returned, in order, for the
    caller to render a per-entry outcome."""
    targets = ready_entries(entries)
    if entry_ids is not None:
        wanted = set(entry_ids)
        targets = [e for e in targets if e.get("id") in wanted]

    results: list[LaunchAttemptResult] = []
    # entry_id -> the field patch to apply on a successful launch. Collected
    # here rather than mutated straight into a working copy so the state commit
    # (and its `queue_lock`) can happen once, after every launch, outside the
    # lock — never holding the queue lock across an `execute_agent_launch_v2`
    # subprocess spawn.
    launched_patches: dict[str, dict] = {}

    for entry in targets:
        task_id = entry.get("task_id")
        task = tasks_by_id.get(task_id)
        if task is None:
            results.append(LaunchAttemptResult(entry["id"], task_id, False, message="задача не найдена"))
            continue

        # `project_configs` is keyed by canonical id, but a task may store a
        # display name / alias. Resolve it before preparing the launch so the
        # queue receives the correct repository and workspace configuration.
        canonical_project = project_config.canonical_project_id(task.get("project"))
        cfg = project_configs.get(canonical_project, {})

        # Shared classification — same service the Kanban launcher uses. A
        # missing but provisionable workspace is routed through the isolated
        # worktree provisioning and fail-closed verification path.
        prep = launch_service.prepare_task_launch(task=task, project_config=cfg)
        if not prep.selection.path:
            results.append(
                LaunchAttemptResult(entry["id"], task_id, False, message="workspace не настроен для задачи")
            )
            continue

        expected_branch = prep.expected_branch
        base_branch = prep.base_branch
        source_repository_path = prep.source_repository_path
        validation = prep.validation

        if prep.decision == launch_service.LAUNCH_DECISION_BLOCKED:
            results.append(
                LaunchAttemptResult(
                    entry["id"],
                    task_id,
                    False,
                    message="; ".join(prep.fatal_messages) or "запуск заблокирован",
                )
            )
            continue
        if prep.decision == launch_service.LAUNCH_DECISION_NEEDS_CONFIRMATION:
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

        # READY or PROVISIONABLE — proceed through the shared launcher.
        resolved_workspace = Path(prep.resolved_workspace)
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
        except (
            workspace_provisioning.WorkspaceVerificationError,
            runtime_supervisor.WorkspaceVerificationFailed,
        ) as exc:
            # Provisioning or the fail-closed isolation gate rejected this
            # workspace — the agent was never started. Record an explicit,
            # structured Requires-Attention reason; never continue as launched.
            structured = (
                exc.structured
                if isinstance(exc, runtime_supervisor.WorkspaceVerificationFailed)
                else exc.as_dict()
            )
            reason = structured.get("detail") or structured.get("remediation") or "проверка изоляции не пройдена"
            results.append(
                LaunchAttemptResult(
                    entry["id"],
                    task_id,
                    False,
                    message=f"workspace не прошёл проверку изоляции ({structured['failed_step']}): {reason}",
                    validation_report=structured,
                )
            )
            continue
        except Exception as exc:  # noqa: BLE001 — one bad entry must not abort the batch
            results.append(LaunchAttemptResult(entry["id"], task_id, False, message=str(exc)))
            continue

        launched_patches[entry["id"]] = {
            "state": STATE_LAUNCHED,
            "run_id": run["id"],
            "launched_at": models.iso_now(),
            "reason": None,
        }
        results.append(LaunchAttemptResult(entry["id"], task_id, True, run_id=run["id"]))

    updated_entries = _commit_launch_results(root, entries, launched_patches)
    return updated_entries, results


def _commit_launch_results(
    root: Path, base_entries: list[dict], patches: dict[str, dict]
) -> list[dict]:
    """Persist the launched-state `patches` (keyed by queue entry id) atomically
    under `queue_lock`, merged onto whatever is *currently* on disk rather than
    blindly overwriting it.

    `base_entries` is the caller's in-memory view (what it passed to
    `launch_ready`). The on-disk queue is re-read under the lock and wins for
    every entry present in both, so a concurrent `reevaluate_and_persist` /
    enqueue in another process is preserved, not clobbered (the lost update
    this lock closes). Entries the caller holds that are absent from disk are
    still kept (an empty/never-persisted queue file is the in-memory-only case
    the unit tests exercise), and disk entries the caller never saw are
    appended, never dropped. The launch patches are applied last, so a
    freshly-launched entry always reflects its `LAUNCHED` state regardless of
    which copy won the merge — and a patch for an entry a concurrent process
    deleted in the meantime is simply dropped (the run itself is already
    tracked in `runtime.db`; the queue is not resurrected behind that delete)."""
    with queue_lock(root):
        disk = load_queue(root)
        disk_by_id = {e.get("id"): e for e in disk}
        merged: list[dict] = []
        seen: set[str | None] = set()
        for entry in base_entries:
            entry_id = entry.get("id")
            seen.add(entry_id)
            merged.append(dict(disk_by_id.get(entry_id, entry)))
        for entry in disk:
            if entry.get("id") not in seen:
                merged.append(dict(entry))

        by_id = {e.get("id"): e for e in merged}
        for entry_id, patch in patches.items():
            target = by_id.get(entry_id)
            if target is not None:
                target.update(patch)

        save_queue(root, merged)
    return merged
