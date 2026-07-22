#!/usr/bin/env python3
"""Deterministic demonstration of the autonomous completion pipeline
(AICC-AUTONOMY-001).

Runs the three specified scenarios against a **real** throwaway git topology
(a bare remote plus a working clone) with a **fake** GitHub client, so the
output is reproducible and touches neither real GitHub nor a real project:

  A — normal completion:  validate → PR → merge → verify target → COMPLETED
  B — closed PR recovery: closed-unmerged PR → recreate branch → replacement PR
  C — validation failure: tests fail → VALIDATION_FAILED (no PR opened)

Usage:  python3 scripts/demo_completion_pipeline.py
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path

# Make the repo importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from command_center.runtime import db as runtime_db  # noqa: E402
from command_center.runtime.completion_service import CompletionOrchestrator  # noqa: E402
from command_center.runtime.github import STATE_CLOSED, FakeGitHubClient, PullRequestState  # noqa: E402
from tests.completion_helpers import (  # noqa: E402
    build_repo,
    make_task_branch,
    merge_into_main,
    seed_completed_run,
)

NOW = datetime(2026, 7, 22, 12, 0, 0)


def _fresh_db(tmp: Path) -> Path:
    import os

    os.environ["AICC_DATA_DIR"] = str(tmp / "data")
    # Force db module to re-resolve the path under the new data dir.
    path = (tmp / "data" / "runtime.db")
    path.parent.mkdir(parents=True, exist_ok=True)
    runtime_db.migrate(path)
    return path


def _print_events(db_path: Path, run_id: str) -> None:
    for event in runtime_db.list_completion_events(db_path, run_id):
        msg = (event["message"] or "").strip()
        print(f"    · {event['event_type']:<24} {msg}")


def _print_state(db_path: Path, run_id: str) -> None:
    row = runtime_db.get_completion(db_path, run_id)
    print(f"  FINAL completion_state = {row['completion_state']}")
    print(f"        reason           = {row['last_reason_code']}")
    print(f"        pull_request     = #{row['pull_request_number']} ({row['pull_request_state']})")
    if row["replaced_pull_request_number"]:
        print(f"        replaced PR      = #{row['replaced_pull_request_number']}")
    print(f"        merge_commit     = {row['merge_commit']}")
    print(f"        requires_human   = {bool(row['requires_human'])}")
    print(f"        recommended      = {row['recommended_action']}")


def scenario_a(tmp: Path) -> None:
    print("\n=== Scenario A — normal completion (auto-merge) ===")
    db = _fresh_db(tmp / "A")
    remote, work = build_repo(tmp / "A" / "git")
    make_task_branch(work, "task/demo-a")
    run = seed_completed_run(db, repository_path=str(work), branch="task/demo-a")
    gh = FakeGitHubClient(on_merge=lambda pr: merge_into_main(remote, tmp / "A" / "git", pr.head_ref))
    orch = CompletionOrchestrator(db, github=gh)
    orch.begin_completion(run, project_cfg={"default_branch": "main", "merge_mode": "auto_after_checks"})
    orch.advance(run["id"], now=NOW)
    _print_events(db, run["id"])
    _print_state(db, run["id"])


def scenario_b(tmp: Path) -> None:
    print("\n=== Scenario B — closed PR without merge → recovery ===")
    db = _fresh_db(tmp / "B")
    remote, work = build_repo(tmp / "B" / "git")
    make_task_branch(work, "task/demo-b")
    run = seed_completed_run(db, repository_path=str(work), branch="task/demo-b")
    gh = FakeGitHubClient()
    gh.seed(PullRequestState(number=41, url="https://github.com/x/y/pull/41", state=STATE_CLOSED, head_ref="task/demo-b"))
    orch = CompletionOrchestrator(db, github=gh)
    orch.begin_completion(run, project_cfg={"default_branch": "main", "allow_pr_recovery": True})
    orch.advance(run["id"], now=NOW)
    # Second pass proves recovery is idempotent (no duplicate PR).
    orch.advance(run["id"], now=NOW)
    _print_events(db, run["id"])
    _print_state(db, run["id"])
    print(f"  replacement PRs created = {len(gh.created)} (idempotent recovery)")


def scenario_c(tmp: Path) -> None:
    print("\n=== Scenario C — validation failure ===")
    db = _fresh_db(tmp / "C")
    remote, work = build_repo(tmp / "C" / "git")
    make_task_branch(work, "task/demo-c", filename="broken.py", content="def (:\n")
    run = seed_completed_run(db, repository_path=str(work), branch="task/demo-c")
    gh = FakeGitHubClient()
    orch = CompletionOrchestrator(db, github=gh)
    orch.begin_completion(run, project_cfg={"default_branch": "main", "merge_mode": "auto_after_checks"})
    orch.advance(run["id"], now=NOW)
    _print_events(db, run["id"])
    _print_state(db, run["id"])
    print(f"  pull requests opened = {len(gh.created)} (none, as expected)")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="aicc-completion-demo-") as tmpdir:
        tmp = Path(tmpdir)
        scenario_a(tmp)
        scenario_b(tmp)
        scenario_c(tmp)
    print("\nAll three scenarios completed deterministically.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
