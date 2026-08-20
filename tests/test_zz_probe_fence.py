"""Two probes that differ ONLY in how the publication-fence denial surfaces."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import pytest

from command_center.daily_audit import DailyAuditStore
from command_center.runtime import db as runtime_db
from command_center.runtime.completion import CompletionState, ReasonCode
from command_center.runtime.completion_service import (
    CompletionOrchestrator, CompletionFenceLostError,
)
from command_center.runtime.github import FakeGitHubClient
from tests.completion_helpers import build_repo, make_task_branch, seed_completed_run

NOW = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)


def _fenced(tmp_path, name):
    remote, work = build_repo(tmp_path)
    db_path = runtime_db.resolve_db_path()
    runtime_db.migrate(db_path)
    store = DailyAuditStore(db_path)
    campaign = store.acquire_due(now=NOW, owner="owner-a", lease_duration=timedelta(minutes=1))
    branch = f"task/{name}"
    make_task_branch(work, branch)
    run = seed_completed_run(db_path, repository_path=str(work), branch=branch)
    clock = [NOW]
    orch = CompletionOrchestrator(db_path, github=FakeGitHubClient(), publication_fence_clock=lambda: clock[0])
    orch.begin_completion(run, project_cfg={"default_branch": "main", "validation_required": False},
                          policy_overrides={"publication_fence_campaign_id": campaign,
                                            "publication_fence_owner": "owner-a"})
    # Steal the lease: the campaign's one-time publication ticket is now void.
    clock[0] = NOW + timedelta(minutes=6)
    stolen = store.acquire_due(now=clock[0], owner="owner-b", lease_duration=timedelta(hours=1))
    assert stolen and stolen != campaign
    return db_path, orch, run, clock[0]


def _audit_rows(db_path, run_id):
    return [
        e for e in runtime_db.list_completion_events(db_path, run_id)
        if (e.get("reason_code") == ReasonCode.PUBLICATION_FENCE_LOST)
        or "fence" in (e.get("message") or "").lower()
    ]


def test_probe_raise(tmp_path):
    db_path, orch, run, now = _fenced(tmp_path, "raise")
    with pytest.raises(CompletionFenceLostError):
        orch.advance(run["id"], now=now)
    rows = _audit_rows(db_path, run["id"])
    print(f"\nPROBE raise  -> audit rows = {len(rows)} {[r['event_type'] for r in rows]}")


def test_probe_return(tmp_path):
    db_path, orch, run, now = _fenced(tmp_path, "return")
    assert orch.advance_safely(run["id"], now=now) is None
    rows = _audit_rows(db_path, run["id"])
    print(f"\nPROBE return -> audit rows = {len(rows)} {[r['event_type'] for r in rows]}")
