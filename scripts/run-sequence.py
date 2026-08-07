#!/usr/bin/env python3
"""Generic sequential task runner with crash-resilient state file.

Usage:
    python scripts/run-sequence.py TASK-ID-1 TASK-ID-2 TASK-ID-3 ...
    python scripts/run-sequence.py --dry-run TASK-ID-1 TASK-ID-2 TASK-ID-3 ...

State is saved to data/sequence_state.json after each step. If the process
dies, re-run with the same task list — it resumes from the last non-terminal
step automatically.  The state file is removed on successful completion.

With --dry-run, shows what would happen without actually launching tasks or
modifying state.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from command_center import tasks_repository  # noqa: E402

STATE_FILE = ROOT / "data" / "sequence_state.json"
POLL_INTERVAL = 20  # seconds

TERMINAL_STATUSES = frozenset(
    {"Completed", "Awaiting PR", "Needs Review", "Cancelled", "Failed", "Closed", "Done"}
)
RUNNING_STATUSES = frozenset({"Running", "Launching"})


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def _load_state(task_ids: list[str], dry_run: bool = False) -> dict:
    """Load existing state or create fresh state for this sequence.

    If a saved sequence exists for the same ordered task list, resume it.
    Otherwise start fresh (the old state file is overwritten).
    """
    if STATE_FILE.exists():
        try:
            saved = json.loads(STATE_FILE.read_text())
            if saved.get("task_ids") == task_ids:
                print(f"[SEQ] Resuming from step {saved['current_index']} / {len(task_ids)}")
                return saved
        except (json.JSONDecodeError, KeyError):
            pass
    state = {"task_ids": task_ids, "current_index": 0, "steps": {}}
    _save_state(state, dry_run=dry_run)
    return state


def _save_state(state: dict, dry_run: bool = False) -> None:
    if dry_run:
        print(f"[DRY-RUN] Would save state: {json.dumps(state, indent=2, ensure_ascii=False)[:100]}...", flush=True)
        return
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Task helpers
# ---------------------------------------------------------------------------

def _validate_task_ids(task_ids: list[str]) -> None:
    """Validate that all task IDs exist in the repository.

    Raises SystemExit if any task ID is not found.
    """
    tasks = tasks_repository.load_tasks(ROOT)
    task_id_set = {t["id"] for t in tasks}

    missing = [tid for tid in task_ids if tid not in task_id_set]
    if missing:
        print("Error: The following task IDs do not exist:", file=sys.stderr)
        for tid in missing:
            print(f"  - {tid}", file=sys.stderr)
        raise SystemExit(1)


def _task_launch_status(task_id: str) -> str:
    tasks = tasks_repository.load_tasks(ROOT)
    t = next((t for t in tasks if t["id"] == task_id), None)
    return t.get("launch_status", "NOT_FOUND") if t else "NOT_FOUND"


def _launch(task_id: str, dry_run: bool = False) -> None:
    if dry_run:
        print(f"[DRY-RUN] Would launch {task_id} via relaunch-task.py", flush=True)
        return
    print(f"[SEQ] Launching {task_id}…", flush=True)
    subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "relaunch-task.py"), task_id],
        cwd=str(ROOT),
    )


def _wait_for_terminal(task_id: str, dry_run: bool = False) -> str:
    """Poll until task reaches a terminal status. Returns final status."""
    if dry_run:
        status = _task_launch_status(task_id)
        print(f"[DRY-RUN]   {task_id}: {status}", flush=True)
        if status in TERMINAL_STATUSES:
            print("[DRY-RUN]   Would skip polling (already terminal)", flush=True)
        else:
            print(f"[DRY-RUN]   Would poll every {POLL_INTERVAL}s until terminal", flush=True)
        return status
    while True:
        status = _task_launch_status(task_id)
        print(f"[SEQ]   {task_id}: {status}", flush=True)
        if status in TERMINAL_STATUSES:
            return status
        time.sleep(POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(task_ids: list[str], dry_run: bool = False) -> int:
    if not task_ids:
        print("Usage: run-sequence.py [--dry-run] TASK-ID [TASK-ID ...]", file=sys.stderr)
        return 1

    _validate_task_ids(task_ids)

    if dry_run:
        print("[DRY-RUN] Running in dry-run mode — no tasks will be launched or state modified.", flush=True)

    state = _load_state(task_ids, dry_run=dry_run)

    for idx, task_id in enumerate(task_ids):
        if idx < state["current_index"]:
            continue  # already completed in a prior run

        status = _task_launch_status(task_id)
        prefix = "[DRY-RUN]" if dry_run else "[SEQ]"
        print(f"\n{prefix} Step {idx + 1}/{len(task_ids)}: {task_id}  [{status}]", flush=True)

        if status in TERMINAL_STATUSES:
            print(f"{prefix}   Already terminal ({status}), skipping.", flush=True)
            state["steps"][task_id] = {"status": status, "skipped": True}
            state["current_index"] = idx + 1
            _save_state(state, dry_run=dry_run)
            continue

        if status not in RUNNING_STATUSES:
            _launch(task_id, dry_run=dry_run)
            if not dry_run:
                time.sleep(8)  # give supervisor time to pick up
                # If launch was rejected (dirty tree, locked, etc.) retry until accepted
                post_status = _task_launch_status(task_id)
                while post_status not in RUNNING_STATUSES and post_status not in TERMINAL_STATUSES:
                    print(f"[SEQ]   launch rejected ({post_status}), retrying in {POLL_INTERVAL}s…", flush=True)
                    time.sleep(POLL_INTERVAL)
                    _launch(task_id)
                    time.sleep(8)
                    post_status = _task_launch_status(task_id)
            else:
                print("[DRY-RUN]   Would sleep 8s before polling", flush=True)

        final = _wait_for_terminal(task_id, dry_run=dry_run)
        print(f"{prefix}   {task_id} DONE → {final}", flush=True)

        state["steps"][task_id] = {"status": final}
        state["current_index"] = idx + 1
        _save_state(state, dry_run=dry_run)

    prefix = "[DRY-RUN]" if dry_run else "[SEQ]"
    print(f"\n{prefix} All {len(task_ids)} tasks complete.", flush=True)
    for tid, info in state["steps"].items():
        print(f"  {tid}: {info['status']}", flush=True)

    if not dry_run:
        STATE_FILE.unlink(missing_ok=True)
    else:
        print(f"[DRY-RUN] Would delete state file: {STATE_FILE}", flush=True)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run a sequence of tasks with crash-resilient state tracking."
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without modifying state or launching tasks")
    parser.add_argument("task_ids", nargs="+", help="Task IDs to run in sequence")
    args = parser.parse_args()

    raise SystemExit(main(args.task_ids, dry_run=args.dry_run))
