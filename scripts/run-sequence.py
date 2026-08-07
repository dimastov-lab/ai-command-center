#!/usr/bin/env python3
"""Generic sequential task runner with crash-resilient state file.

Usage:
    python scripts/run-sequence.py TASK-ID-1 TASK-ID-2 TASK-ID-3 ...

State is saved to data/sequence_state.json after each step. If the process
dies, re-run with the same task list — it resumes from the last non-terminal
step automatically.  The state file is removed on successful completion.
"""
from __future__ import annotations

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

def _load_state(task_ids: list[str]) -> dict:
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
    _save_state(state)
    return state


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Task helpers
# ---------------------------------------------------------------------------

def _task_launch_status(task_id: str) -> str:
    tasks = tasks_repository.load_tasks(ROOT)
    t = next((t for t in tasks if t["id"] == task_id), None)
    return t.get("launch_status", "NOT_FOUND") if t else "NOT_FOUND"


def _launch(task_id: str) -> None:
    print(f"[SEQ] Launching {task_id}…", flush=True)
    subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "relaunch-task.py"), task_id],
        cwd=str(ROOT),
    )


def _wait_for_terminal(task_id: str) -> str:
    """Poll until task reaches a terminal status. Returns final status."""
    while True:
        status = _task_launch_status(task_id)
        print(f"[SEQ]   {task_id}: {status}", flush=True)
        if status in TERMINAL_STATUSES:
            return status
        time.sleep(POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(task_ids: list[str]) -> int:
    if not task_ids:
        print("Usage: run-sequence.py TASK-ID [TASK-ID ...]", file=sys.stderr)
        return 1

    state = _load_state(task_ids)

    for idx, task_id in enumerate(task_ids):
        if idx < state["current_index"]:
            continue  # already completed in a prior run

        status = _task_launch_status(task_id)
        print(f"\n[SEQ] Step {idx + 1}/{len(task_ids)}: {task_id}  [{status}]", flush=True)

        if status in TERMINAL_STATUSES:
            print(f"[SEQ]   Already terminal ({status}), skipping.", flush=True)
            state["steps"][task_id] = {"status": status, "skipped": True}
            state["current_index"] = idx + 1
            _save_state(state)
            continue

        if status not in RUNNING_STATUSES:
            _launch(task_id)
            time.sleep(8)  # give supervisor time to pick up

        final = _wait_for_terminal(task_id)
        print(f"[SEQ]   {task_id} DONE → {final}", flush=True)

        state["steps"][task_id] = {"status": final}
        state["current_index"] = idx + 1
        _save_state(state)

    print(f"\n[SEQ] All {len(task_ids)} tasks complete.", flush=True)
    for tid, info in state["steps"].items():
        print(f"  {tid}: {info['status']}", flush=True)

    STATE_FILE.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
