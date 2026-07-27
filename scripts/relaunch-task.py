#!/usr/bin/env python3
"""Relaunch an existing task through the Command Center launch pipeline.

Usage: scripts/relaunch-task.py <task_id>

Mirrors `_launch_task_from_board` in app.py: marks the task ready, enqueues it,
re-evaluates readiness, and calls `execution_queue.launch_ready` for that one
entry. The actual agent process is spawned by the same v2 launcher the UI uses.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from command_center import execution_queue, project_config, tasks_repository
from command_center.runtime import api as runtime_api

TASK_ID = sys.argv[1] if len(sys.argv) > 1 else "e5f7fb697e6a4245bdb10a43a654a706"


def main() -> int:
    # 1. Mark the task ready for relaunch (same as the "К перезапуску" button).
    tasks_repository.set_manual_launch_status(
        ROOT, TASK_ID, "Ready", "Отмечено для перезапуска (скрипт relaunch-task)."
    )

    # 2. Fresh load — the mutation above wrote to disk.
    tasks = tasks_repository.load_tasks(ROOT)
    tasks_by_id = {t["id"]: t for t in tasks}
    task = tasks_by_id.get(TASK_ID)
    if task is None:
        print(f"Задача {TASK_ID} не найдена.")
        return 1
    print(f"Задача: {task.get('title') or TASK_ID}  [{task.get('launch_status')}]")

    # 3. Enqueue + re-evaluate (idempotent per task).
    execution_queue.enqueue_and_persist(ROOT, task, tasks_by_id)
    entries = execution_queue.reevaluate_and_persist(ROOT, tasks_by_id)
    entry = next(
        (
            e
            for e in entries
            if e.get("task_id") == TASK_ID and e.get("state") in execution_queue.OPEN_STATES
        ),
        None,
    )
    if entry is None:
        print("Задача не попала в очередь запуска (возможно, не READY).")
        return 1
    print(f"Запись очереди: {entry['id']}  state={entry['state']}")

    # 4. Launch that one entry — same locked path the board uses.
    project_configs = project_config.load_project_configs()
    api = runtime_api.ExecutionCenterAPI()
    _, results = execution_queue.launch_ready(
        ROOT,
        entries,
        tasks,
        tasks_by_id,
        project_configs,
        api,
        entry_ids=[entry["id"]],
    )
    tasks_repository.upsert_tasks(ROOT, tasks)

    for r in results:
        flag = "OK" if r.launched else "SKIP"
        print(f"  [{flag}] {r.entry_id}: {r.message}")
    launched = [r for r in results if r.launched]
    if launched:
        print(f"Запущено: {task.get('title') or TASK_ID}.")
        return 0
    print("Запуск не выполнен — задача осталась в очереди.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())