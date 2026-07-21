"""Task persistence (`data/tasks.json`) as a plain-Python repository, with
zero Streamlit coupling.

`app.py`'s `load_tasks`/`save_tasks`/`new_task_record` are thin wrappers
around this module (same names, same behavior, `ROOT`/`TASKS_FILE` bound in
from `app.py`'s own constants) — this mirrors `docs/desktop/ARCHITECTURE.md`
§14 ("Run/session/report/task state is owned entirely by... the existing
v1.2 JSON/JSONL stores"): a future desktop adapter reads/writes tasks
through this module, never through a second store or by touching the JSON
file directly.

Every *write* to `tasks.json` — creating a task, changing its status,
deleting it, a manual launch-status toggle, a batch package import
(`command_center.task_import.apply_task_package`), any future bulk
operation — must go through `mutate_tasks` (or one of the verb-named
helpers below that are themselves built on it), never a hand-rolled
`load_tasks()` ... mutate ... `save_tasks()` sequence. `save_tasks`'s
`tempfile` + `os.replace` makes a single write atomic, but does nothing to
prevent a *lost update* across a read-modify-write cycle: two callers that
each `load_tasks()` before either `save_tasks()`s will silently discard one
another's change, whichever writes last "winning" with a snapshot that
never saw the other's update. This is exactly the shape of the bug
Founder Gate reproduced (a batch import racing a manual Kanban edit or
another concurrent import, expected record count higher than what actually
landed on disk) — `mutate_tasks` closes it by holding `tasks_lock` (an OS
advisory `fcntl.flock`/`msvcrt.locking` lock — see `storage.file_lock`'s
docstring) across the *entire* load-mutate-save cycle, for every write path
in this application, so no two writers can ever interleave their read and
write halves.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from command_center import models, storage, task_view

T = TypeVar("T")

TASKS_LOCK_FILE_NAME = "tasks.lock"
TASKS_LOCK_TIMEOUT_SECONDS = 30.0
_TASKS_LOCK_POLL_SECONDS = 0.05


def tasks_file_path(root: Path) -> Path:
    return storage.resolve_data_dir(root) / "tasks.json"


def tasks_lock_path(root: Path) -> Path:
    return storage.resolve_data_dir(root) / TASKS_LOCK_FILE_NAME


def normalize_task(task: dict) -> dict:
    task.setdefault("priority", "Medium")
    task.setdefault("owner", "")
    task.setdefault("estimate_hours", 0.0)
    task.setdefault("depends_on", [])
    task.setdefault("updated_at", task.get("created_at", ""))
    models.normalize_task_workflow(task)
    models.normalize_task_execution(task)
    return task


def load_tasks(root: Path, *, example_file: Path | None = None) -> list[dict]:
    data_dir = storage.resolve_data_dir(root)
    data_dir.mkdir(parents=True, exist_ok=True)
    tasks_file = tasks_file_path(root)
    if not tasks_file.exists():
        if example_file and example_file.exists():
            shutil.copyfile(example_file, tasks_file)
        else:
            save_tasks(root, [])
    try:
        data = json.loads(tasks_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    return [normalize_task(task) for task in data]


def save_tasks(root: Path, tasks: list[dict]) -> None:
    data_dir = storage.resolve_data_dir(root)
    data_dir.mkdir(parents=True, exist_ok=True)
    tasks_file = tasks_file_path(root)
    fd, tmp_name = tempfile.mkstemp(dir=data_dir, prefix=".tasks_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(tasks, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_name, tasks_file)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise


@contextlib.contextmanager
def tasks_lock(root: Path, *, timeout: float = TASKS_LOCK_TIMEOUT_SECONDS):
    """The single cross-process/cross-thread lock every write to
    `tasks.json` must hold for its *entire* read-modify-write cycle —
    shared by every mutation helper in this module and by
    `command_center.task_import.apply_task_package` (never a second,
    import-specific lock file), so a manual Kanban edit/creation and a
    package import can never race each other and lose an update. See
    `storage.file_lock`'s docstring for the underlying `fcntl`/`msvcrt`
    mechanism and why `save_tasks`'s own atomic write is not, by itself,
    sufficient."""
    with storage.file_lock(tasks_lock_path(root), timeout=timeout, poll_seconds=_TASKS_LOCK_POLL_SECONDS):
        yield


def mutate_tasks(
    root: Path,
    mutator: Callable[[list[dict]], T],
    *,
    timeout: float = TASKS_LOCK_TIMEOUT_SECONDS,
    persist_if: Callable[[T], bool] | None = None,
) -> T:
    """The single transactional primitive every write to `tasks.json` goes
    through: acquire `tasks_lock` -> load the *current* list fresh from disk
    (never a caller-held snapshot, which may already be stale by the time
    this runs — that staleness is exactly what causes a lost update) -> call
    `mutator(tasks)`, which mutates the list in place (append/remove/find-
    and-edit) and may return a value -> persist via one `save_tasks` call
    (skipped only if `persist_if` is given and returns falsy for the
    mutator's result — an opt-in for callers that want to write only when
    something actually changed, e.g. a polling reconciler) -> release.
    Returns whatever `mutator` returned.

    If `mutator` raises, nothing is saved and the exception propagates after
    the lock is released (the `with` block's `finally` always runs) — a
    partially-applied mutation is never persisted.

    Every verb-named helper below (`create_task`/`upsert_task`/
    `update_task_status`/`set_manual_launch_status`/`delete_task`) is built
    on this; so is `command_center.task_import.apply_task_package`.
    """
    with tasks_lock(root, timeout=timeout):
        tasks = load_tasks(root)
        result = mutator(tasks)
        if persist_if is None or persist_if(result):
            save_tasks(root, tasks)
        return result


def new_task_record(
    project: str,
    title: str,
    task_type: str,
    status: str,
    *,
    goal: str | None = None,
    notes: str = "",
    priority: str = "Medium",
    owner: str = "",
    estimate_hours: float = 0.0,
    depends_on: list[str] | None = None,
    parent_task_id: str | None = None,
    prior_run_id: str | None = None,
    workflow_stage: str = "Draft",
    workspace_path: str | None = None,
    branch: str | None = None,
    executor: str | None = None,
    prompt: str | None = None,
) -> dict:
    """`title` is the short, dedicated heading (Название задачи); `goal`
    (Цель задачи) is the independent objective description. If `goal` is
    omitted it defaults to `title` for call sites that don't yet collect a
    separate objective — this keeps every caller trivially valid without
    silently losing the objective text.

    `workspace_path`/`branch`/`executor`/`prompt` are the engineering
    environment fields a task inherits from its project at creation time
    (see `command_center.project_config.task_defaults_from_project`) — the
    caller (the Create Task UI) resolves inherited-vs-overridden before
    calling this, so this function just persists whatever final values it is
    given. Omitting them (the pre-existing call signature) leaves the
    pre-existing defaults from `models.default_task_execution_fields`/
    `default_task_workflow_fields` untouched — `workspace_path=None` still
    means "unset, resolve via Launch's own fallback chain," exactly as
    before."""
    now = models.iso_now()
    record = {
        "id": uuid.uuid4().hex,
        "project": project,
        "title": title,
        "task_type": task_type,
        "status": status,
        "priority": priority,
        "owner": owner,
        "estimate_hours": estimate_hours,
        "depends_on": depends_on or [],
        "created_at": now,
        "updated_at": now,
    }
    record.update(models.default_task_workflow_fields())
    record["parent_task_id"] = parent_task_id
    record["prior_run_id"] = prior_run_id
    record["workflow_stage"] = workflow_stage
    record.update(models.default_task_execution_fields())
    record["goal"] = goal if goal is not None else title
    record["notes"] = notes
    if workspace_path:
        record["workspace_path"] = workspace_path
    if branch:
        record["branch"] = branch
    if executor:
        record["executor"] = executor
        record["agent"] = executor
    if prompt:
        record["prompt"] = prompt
    models.append_timeline_event(record, "task_created", f"Задача создана: {title}")
    return record


def create_task(root: Path, project: str, title: str, task_type: str, status: str, **kwargs) -> dict:
    """Locked equivalent of `tasks.append(new_task_record(...)); save_tasks(...)`
    — every caller that creates a task must go through here rather than
    appending to its own possibly-stale in-memory list and saving that,
    which is exactly the pattern that silently drops a concurrent writer's
    task (see this module's docstring). `**kwargs` forwards verbatim to
    `new_task_record` (`goal`, `priority`, `depends_on`, `workspace_path`,
    ...). Returns the created record."""

    def _mutator(tasks: list[dict]) -> dict:
        record = new_task_record(project, title, task_type, status, **kwargs)
        tasks.append(record)
        return record

    return mutate_tasks(root, _mutator)


def upsert_task(root: Path, task: dict, *, timeout: float = TASKS_LOCK_TIMEOUT_SECONDS) -> None:
    """Persist `task` — already fully mutated in place by the caller (e.g.
    `command_center.launch_service`, which threads one task dict through
    several in-place edits across a multi-step launch) — as the current
    state for its id: replaces any existing entry with that id in a
    freshly-loaded list, or appends it if absent. Only this one task's
    record is touched; every other task in the fresh list (including one a
    concurrent writer added since the caller's own load) survives
    untouched — the right merge semantics for "commit this one already-
    computed task's latest state," as opposed to `mutate_tasks`'s general
    "run this mutator against the fresh list."""
    task_id = task.get("id")

    def _mutator(tasks: list[dict]) -> None:
        for index, existing in enumerate(tasks):
            if existing.get("id") == task_id:
                tasks[index] = task
                return
        tasks.append(task)

    mutate_tasks(root, _mutator, timeout=timeout)


def upsert_tasks(root: Path, tasks_to_upsert: list[dict], *, timeout: float = TASKS_LOCK_TIMEOUT_SECONDS) -> None:
    """Bulk form of `upsert_task`: persists every task in `tasks_to_upsert`
    (each already fully mutated in place by the caller — e.g.
    `command_center.execution_queue.launch_ready`, which mutates one `task`
    dict per launched queue entry) as the current state for its id, all
    within a *single* locked read-modify-write cycle — never one lock
    acquisition per task. Every task in the fresh list not present in
    `tasks_to_upsert` (including ones a concurrent writer added since the
    caller's own load) survives untouched. Safe to call with a caller's
    entire task list, not just the subset that actually changed — every
    task is upserted by id, so an unchanged task is simply overwritten with
    an identical copy, never duplicated."""
    by_id = {t["id"]: t for t in tasks_to_upsert if t.get("id")}

    def _mutator(tasks: list[dict]) -> None:
        seen: set[str] = set()
        for index, existing in enumerate(tasks):
            existing_id = existing.get("id")
            if existing_id in by_id:
                tasks[index] = by_id[existing_id]
                seen.add(existing_id)
        for task_id, task in by_id.items():
            if task_id not in seen:
                tasks.append(task)

    mutate_tasks(root, _mutator, timeout=timeout)


def update_task_status(root: Path, task_id: str, new_status: str) -> dict | None:
    """Returns the updated task record, or `None` if `task_id` was not
    found (a no-op — nothing is saved in that case since `mutate_tasks`
    still runs `save_tasks` on the unchanged fresh list, harmless but see
    `persist_if` if that ever needs to change)."""

    def _mutator(tasks: list[dict]) -> dict | None:
        for task in tasks:
            if task.get("id") == task_id:
                task["status"] = new_status
                task["updated_at"] = models.iso_now()
                if new_status == "Done":
                    models.set_current_stage(task, "Merged")
                    models.append_timeline_event(task, "merged", "Задача перемещена в статус Done.")
                return task
        return None

    return mutate_tasks(root, _mutator)


def set_manual_launch_status(root: Path, task_id: str, status: str, note: str) -> dict | None:
    """Pause/Resume/Restart find-and-persist — same shape as
    `update_task_status`/`delete_task` above. The actual per-task mutation
    (advisory-only; see `command_center.launch`'s module docstring) lives in
    `command_center.task_view.set_manual_launch_status`. Returns the updated
    record, or `None` if `task_id` was not found."""

    def _mutator(tasks: list[dict]) -> dict | None:
        for task in tasks:
            if task.get("id") == task_id:
                task_view.set_manual_launch_status(task, status, note)
                return task
        return None

    return mutate_tasks(root, _mutator)


def delete_task(root: Path, task_id: str) -> None:
    def _mutator(tasks: list[dict]) -> None:
        tasks[:] = [task for task in tasks if task.get("id") != task_id]

    mutate_tasks(root, _mutator)


def task_label(task: dict) -> str:
    title = (task.get("title") or "—")[:50]
    return f"[{task.get('project')}] {title} · {task.get('status')}"
