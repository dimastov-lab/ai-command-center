"""Task persistence (`data/tasks.json`) as a plain-Python repository, with
zero Streamlit coupling.

`app.py`'s `load_tasks`/`save_tasks`/`new_task_record`/`update_task_status`/
`delete_task` are thin wrappers around this module (same names, same
behavior, `ROOT`/`TASKS_FILE` bound in from `app.py`'s own constants) — this
mirrors `docs/desktop/ARCHITECTURE.md` §14 ("Run/session/report/task state
is owned entirely by... the existing v1.2 JSON/JSONL stores"): a future
desktop adapter reads/writes tasks through this module, never through a
second store or by touching the JSON file directly.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path

from command_center import models, storage, task_view


def tasks_file_path(root: Path) -> Path:
    return storage.resolve_data_dir(root) / "tasks.json"


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
) -> dict:
    """`title` is the short, dedicated heading (Название задачи); `goal`
    (Цель задачи) is the independent objective description. If `goal` is
    omitted it defaults to `title` for call sites that don't yet collect a
    separate objective — this keeps every caller trivially valid without
    silently losing the objective text."""
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
    models.append_timeline_event(record, "task_created", f"Задача создана: {title}")
    return record


def update_task_status(root: Path, tasks: list[dict], task_id: str, new_status: str) -> None:
    for task in tasks:
        if task.get("id") == task_id:
            task["status"] = new_status
            task["updated_at"] = models.iso_now()
            if new_status == "Done":
                models.set_current_stage(task, "Merged")
                models.append_timeline_event(task, "merged", "Задача перемещена в статус Done.")
            break
    save_tasks(root, tasks)


def set_manual_launch_status(root: Path, tasks: list[dict], task_id: str, status: str, note: str) -> None:
    """Pause/Resume/Restart find-and-persist — same shape as
    `update_task_status`/`delete_task` above. The actual per-task mutation
    (advisory-only; see `command_center.launch`'s module docstring) lives in
    `command_center.task_view.set_manual_launch_status`."""
    for task in tasks:
        if task.get("id") == task_id:
            task_view.set_manual_launch_status(task, status, note)
            break
    save_tasks(root, tasks)


def delete_task(root: Path, tasks: list[dict], task_id: str) -> None:
    remaining = [task for task in tasks if task.get("id") != task_id]
    save_tasks(root, remaining)


def task_label(task: dict) -> str:
    title = (task.get("title") or "—")[:50]
    return f"[{task.get('project')}] {title} · {task.get('status')}"
