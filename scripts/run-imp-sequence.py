#!/usr/bin/env python3
"""Run IMP tasks in dependency order.

IMP-001 already running → wait → IMP-002 → IMP-003 → IMP-004
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from command_center import tasks_repository  # noqa: E402

RUN_DB = ROOT / "data" / "runtime.db"
POLL_INTERVAL = 20  # seconds


def task_launch_status(task_id: str) -> str:
    tasks = tasks_repository.load_tasks(ROOT)
    t = next((t for t in tasks if t["id"] == task_id), None)
    return t.get("launch_status", "?") if t else "NOT_FOUND"


def wait_for_task(task_id: str) -> str:
    """Poll until task leaves Running/Launching state. Returns final status."""
    print(f"\n⏳ Ожидаю завершения {task_id}…")
    while True:
        status = task_launch_status(task_id)
        print(f"  {task_id}: {status}")
        if status not in ("Running", "Launching"):
            return status
        time.sleep(POLL_INTERVAL)


def launch_and_wait(task_id: str) -> int:
    """Launch task via relaunch-task.py and wait for completion."""
    print(f"\n🚀 Запускаю {task_id}…")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "relaunch-task.py"), task_id],
        cwd=str(ROOT),
    )
    return result.returncode


def main() -> int:
    sequence = [
        ("AICC-IMP-001", "wait"),   # already running
        ("AICC-IMP-002", "launch"),
        ("AICC-IMP-003", "launch"),
        ("AICC-IMP-004", "launch"),
    ]

    for task_id, action in sequence:
        status = task_launch_status(task_id)
        print(f"\n{'='*60}")
        print(f"Задача: {task_id}  [{status}]")

        if action == "wait":
            if status in ("Done", "Cancelled"):
                print("  Уже завершена — пропускаю.")
                continue
            final = wait_for_task(task_id)
            print(f"  Завершена со статусом: {final}")
            if final == "Requires Attention":
                print(f"  ⚠️  {task_id} требует внимания. Проверьте и нажмите Enter для продолжения…")
                input()
        else:
            if status in ("Done", "Cancelled"):
                print("  Уже завершена — пропускаю.")
                continue
            rc = launch_and_wait(task_id)
            final = task_launch_status(task_id)
            print(f"  Завершена: rc={rc}  status={final}")
            if final == "Requires Attention":
                print(f"  ⚠️  {task_id} требует внимания. Проверьте и нажмите Enter для продолжения…")
                input()

    print("\n✅ Все задачи обработаны.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
