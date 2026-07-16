#!/usr/bin/env python3
"""Minimal internal/debug CLI for the v2 Execution Center backend.

Exists only to validate `command_center.runtime` end to end without
redesigning the Streamlit UI (out of scope for Sprint 1 — see the brief).

**This is not a persistent multi-command daemon.** Every invocation of this
script is a separate OS process with its own, empty `Supervisor._active`
registry — it never shares in-memory state (a `Popen` handle, a stdout pipe,
a waitable-child relationship) with any other invocation, past or future.
That is exactly why `launch` below is a *foreground, blocking* command
rather than "start and return a run id": a Claude Code process launched by
one invocation of this CLI cannot be safely or truthfully controlled
(streamed, cancelled) by a *different* invocation, because the second
process has no handle to it at all — only the OS pid, which is not enough
(see `identity.py`/`Supervisor.reconcile`). An earlier version of this
script had a separate `cancel RUN_ID` subcommand; it has been removed
because it could only ever raise (there is nothing for a fresh process to
cancel) or, worse, silently do nothing truthful — see the Sprint 1
remediation notes (finding F1) for the full incident this fixes: a `launch`
that returned immediately left the underlying `claude` process orphaned,
with stream capture permanently stopped and no way to cancel it from a
later invocation.

The read-only inspection subcommands (`list-sessions`, `list-runs`,
`run-status`, `events`, `reconcile`) have no such problem — they only read
(or, for `reconcile`, conservatively reclassify) rows in the shared SQLite
db, which genuinely is persistent cross-process state — and remain safe to
call from any number of separate invocations at any time.

Usage:
    python scripts/execution_center_debug.py list-sessions [--task-id ID]
    python scripts/execution_center_debug.py list-runs [--session-id ID] [--task-id ID] [--state STATE]
    python scripts/execution_center_debug.py run-status RUN_ID
    python scripts/execution_center_debug.py events RUN_ID [--after-seq N]
    python scripts/execution_center_debug.py reconcile
    python scripts/execution_center_debug.py launch PROJECT REPO_PATH TASK_TYPE INSTRUCTION --confirm
        Blocks in the foreground, printing each new event as it is
        persisted, until the run reaches a terminal state. Ctrl+C requests
        confirmed cancellation of the run and waits for cleanup before
        exiting — it does not just kill this CLI process. Exit code: 0 for
        COMPLETED, non-zero (1-4) for FAILED/CANCELLED/INTERRUPTED/UNKNOWN.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from command_center.runtime import db  # noqa: E402
from command_center.runtime.api import ExecutionCenterAPI  # noqa: E402

_POLL_INTERVAL_SECONDS = 0.2
_DEFAULT_CANCEL_GRACE_SECONDS = 10.0

_EXIT_CODE_BY_STATE = {
    "COMPLETED": 0,
    "FAILED": 1,
    "CANCELLED": 2,
    "INTERRUPTED": 3,
    "UNKNOWN": 4,
}


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _print_event(event: dict) -> None:
    print(f"[{event['seq']:>5}] {event['event_type']}: {json.dumps(event['payload'], ensure_ascii=False)[:300]}")


def _run_foreground(api: ExecutionCenterAPI, run: dict) -> int:
    """Block until `run` reaches a terminal state, printing new events as
    they're persisted. Ctrl+C triggers confirmed cancellation and waits for
    it to finish before this function (and the process) exits — it never
    just kills this CLI process and abandons the child."""
    run_id = run["id"]
    after_seq = 0

    try:
        while True:
            current = api.get_run(run_id)
            for event in api.get_events(run_id, after_seq=after_seq):
                _print_event(event)
                after_seq = event["seq"]
            if current["state"] in db.TERMINAL_STATES:
                _print(current)
                return _EXIT_CODE_BY_STATE.get(current["state"], 1)
            time.sleep(_POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nCtrl+C received — requesting confirmed cancellation and waiting for cleanup...", file=sys.stderr)
        final = api.request_cancel(run_id, confirmed=True, grace_seconds=_DEFAULT_CANCEL_GRACE_SECONDS)
        for event in api.get_events(run_id, after_seq=after_seq):
            _print_event(event)
        _print(final)
        return _EXIT_CODE_BY_STATE.get(final["state"], 1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list-sessions")
    p.add_argument("--task-id")

    p = sub.add_parser("list-runs")
    p.add_argument("--session-id")
    p.add_argument("--task-id")
    p.add_argument("--state")

    p = sub.add_parser("run-status")
    p.add_argument("run_id")

    p = sub.add_parser("events")
    p.add_argument("run_id")
    p.add_argument("--after-seq", type=int, default=0)

    p = sub.add_parser("launch", help="Foreground/blocking — see module docstring.")
    p.add_argument("project")
    p.add_argument("repository_path")
    p.add_argument("task_type")
    p.add_argument("instruction")
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--model")
    p.add_argument("--timeout-seconds", type=int, default=None)
    p.add_argument(
        "--confirmed-item", action="append", default=[], dest="confirmed_items",
        help="Repeatable. A candidate_content key to confirm for a sensitive project (BANK/LEGAL).",
    )

    sub.add_parser("reconcile")

    args = parser.parse_args()
    api = ExecutionCenterAPI()

    if args.command == "list-sessions":
        _print(api.list_sessions(task_id=args.task_id))
    elif args.command == "list-runs":
        _print(api.list_runs(session_id=args.session_id, task_id=args.task_id, state=args.state))
    elif args.command == "run-status":
        _print(api.get_run(args.run_id))
    elif args.command == "events":
        _print(api.get_events(args.run_id, after_seq=args.after_seq))
    elif args.command == "launch":
        run = api.start_run(
            project=args.project,
            repository_path=args.repository_path,
            task_type=args.task_type,
            instruction=args.instruction,
            confirmed=args.confirm,
            confirmed_items=args.confirmed_items or None,
            model=args.model,
            timeout_seconds=args.timeout_seconds,
        )
        if run["state"] in db.TERMINAL_STATES:
            # Popen itself failed (e.g. `claude` binary missing) — nothing
            # to wait on, no process was ever left running.
            _print(run)
            return _EXIT_CODE_BY_STATE.get(run["state"], 1)
        return _run_foreground(api, run)
    elif args.command == "reconcile":
        _print(api.reconcile())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
