"""VOYN-W0-AICC-AUDIT-ROLLBACK-CLASS — a refusal must outlive the failure it causes.

The defect this suite pins is not an episode but a shape. A gate decides that
an actor may not do something; the record of that decision is the *signal*
(a campaign that no longer owns its publication lease is, from the store's point
of view, indistinguishable from a stolen one); and then the gate expresses the
refusal by raising, so the exception unwinds through the very transaction that
was carrying the record. The signal deletes itself, and the louder the refusal
the less trace it leaves.

Measured before the fix, with two probes differing *only* in how the refusal
surfaced: ``advance()`` (raise) left **0** rows; ``advance_safely()`` (return)
left **1**. Those two numbers are now asserted directly, in
:func:`test_raise_and_return_flavours_of_the_same_denial_leave_the_same_record`.

The acceptance has two halves and so does this file:

* *No denial path raises before the audit is committed* — the fence evaluates,
  commits its denial record, and only then raises, so every call site inherits
  the guarantee whether or not it catches.
* *A mandatory survives-the-rollback test in every layer where the record is
  written* — one section below per layer:

  1. persistence (``runtime.db`` completion/proposal event journals),
  2. publication fence (``runtime.completion_service``),
  3. autonomy dispatch/execution boundary (``runtime.autonomy_service``),
  4. scheduler lease (``daily_audit.DailyAuditStore``).

The structural half — no *new* gate may reintroduce the shape — lives in
``tests/architecture/test_denial_audit_fitness.py``.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from command_center.daily_audit import CampaignResult, DailyAuditStore
from command_center.runtime import autonomy as A
from command_center.runtime import db as runtime_db
from command_center.runtime.autonomy import AutonomyPolicy, ProposalKind
from command_center.runtime.autonomy_service import (
    AutonomyEngine,
    DispatchNotPermittedError,
    IllegalProposalActionError,
    observe,
)
from command_center.runtime.completion import CompletionState, ReasonCode
from command_center.runtime.completion_service import (
    EV_PUBLICATION_FENCE_DENIED,
    CompletionFenceLostError,
    CompletionOrchestrator,
    ManualMergeError,
)
from command_center.runtime.github import FakeGitHubClient
from tests.completion_helpers import build_repo, make_task_branch, seed_completed_run

NOW = datetime(2026, 8, 20, 9, 0, 0, tzinfo=timezone.utc)


# ==========================================================================
# Fixtures / helpers
# ==========================================================================


@pytest.fixture
def db_path():
    path = runtime_db.resolve_db_path()
    runtime_db.migrate(path)
    return path


def _make_run(db_path, *, repository_path="/tmp/x"):
    task = runtime_db.create_task(db_path, project="AICC", title="t", task_type="implementation")
    session = runtime_db.create_session(
        db_path, task_id=task["id"], project="AICC", repository_path=repository_path
    )
    return runtime_db.create_run(
        db_path, session_id=session["id"], task_id=task["id"], project="AICC",
        task_type="implementation", repository_path=repository_path, prompt="p", is_resume=False,
    )


def _fence_denials(db_path, run_id) -> list[dict]:
    return [
        event
        for event in runtime_db.list_completion_events(db_path, run_id)
        if event["event_type"] == EV_PUBLICATION_FENCE_DENIED
    ]


def _stolen_lease_orchestrator(tmp_path, branch_name):
    """A completion fenced to a campaign whose lease another owner has taken.

    Returns ``(db_path, orchestrator, run, now)``. The fence is closed at
    ``now``: the campaign's one-time publication ticket is void, and every
    privileged side effect must refuse.
    """
    _remote, work = build_repo(tmp_path)
    db_path = runtime_db.resolve_db_path()
    runtime_db.migrate(db_path)

    store = DailyAuditStore(db_path)
    campaign = store.acquire_due(now=NOW, owner="owner-a", lease_duration=timedelta(minutes=1))
    assert campaign

    branch = f"task/{branch_name}"
    make_task_branch(work, branch)
    run = seed_completed_run(db_path, repository_path=str(work), branch=branch)

    clock = [NOW]
    orch = CompletionOrchestrator(
        db_path, github=FakeGitHubClient(), publication_fence_clock=lambda: clock[0]
    )
    orch.begin_completion(
        run,
        project_cfg={"default_branch": "main", "validation_required": False},
        policy_overrides={
            "publication_fence_campaign_id": campaign,
            "publication_fence_owner": "owner-a",
        },
    )

    # Another owner takes the lease. Nothing about the completion row changes —
    # only its authority to publish, which is precisely what the fence checks.
    clock[0] = NOW + timedelta(minutes=6)
    stolen = store.acquire_due(now=clock[0], owner="owner-b", lease_duration=timedelta(hours=1))
    assert stolen and stolen != campaign
    return db_path, orch, run, clock[0]


# ==========================================================================
# Layer 1 — persistence: the journals the denial records are written to
# ==========================================================================


def test_completion_denial_event_is_committed_before_the_writer_returns(db_path):
    """`append_completion_event` commits on its own connection, not the caller's.

    Read back through a connection that was never party to the write: if the
    row were still sitting in an uncommitted transaction, this read would not
    see it, and any later rollback could take it away.
    """
    run = _make_run(db_path)
    runtime_db.append_completion_event(
        db_path, run["id"], EV_PUBLICATION_FENCE_DENIED,
        reason_code=ReasonCode.PUBLICATION_FENCE_LOST,
        message="Publication refused: lease lost.",
    )
    with sqlite3.connect(str(db_path)) as fresh:
        rows = fresh.execute(
            "SELECT COUNT(*) FROM completion_event WHERE run_id = ? AND event_type = ?",
            (run["id"], EV_PUBLICATION_FENCE_DENIED),
        ).fetchone()[0]
    assert rows == 1


def test_a_rollback_after_the_denial_write_cannot_erase_it(db_path):
    """A later transaction rolling back leaves the committed denial untouched.

    This is the literal acceptance sentence at the persistence layer: work that
    is abandoned takes its own writes with it and nothing else.
    """
    run = _make_run(db_path)
    runtime_db.append_completion_event(
        db_path, run["id"], EV_PUBLICATION_FENCE_DENIED,
        reason_code=ReasonCode.PUBLICATION_FENCE_LOST, message="Publication refused.",
    )
    with runtime_db.connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """INSERT INTO completion_event (run_id, seq, event_type, created_at)
               VALUES (?, ?, 'ABANDONED_WORK', ?)""",
            (run["id"], 99, runtime_db.iso_now()),
        )
        conn.execute("ROLLBACK")

    kinds = [e["event_type"] for e in runtime_db.list_completion_events(db_path, run["id"])]
    assert "ABANDONED_WORK" not in kinds  # the rollback did its job ...
    assert kinds.count(EV_PUBLICATION_FENCE_DENIED) == 1  # ... and only its job


def test_proposal_denial_event_is_committed_before_the_writer_returns(db_path):
    """The same guarantee for the proposal journal the autonomy engine uses."""
    engine = AutonomyEngine(db_path)
    proposal = engine.create_proposal(
        kind=ProposalKind.TASK_CREATION, project="AICC", title="Add tests",
        rationale="Coverage gap", evidence=[observe("task_gap", "src", "gap")],
        policy=AutonomyPolicy(enabled=True, allowed_kinds=frozenset({ProposalKind.TASK_CREATION})),
    )
    runtime_db.append_proposal_event(
        db_path, proposal["id"], A.EventType.DISPATCH,
        from_state=proposal["state"], to_state=proposal["state"], actor="probe",
        reason_code=A.ReasonCode.DISPATCH_DISABLED, message="dispatch refused",
    )
    with sqlite3.connect(str(db_path)) as fresh:
        rows = fresh.execute(
            "SELECT COUNT(*) FROM proposal_event WHERE proposal_id = ? AND reason_code = ?",
            (proposal["id"], A.ReasonCode.DISPATCH_DISABLED),
        ).fetchone()[0]
    assert rows == 1


# ==========================================================================
# Layer 2 — publication fence (runtime.completion_service)
# ==========================================================================


def test_raise_and_return_flavours_of_the_same_denial_leave_the_same_record(tmp_path):
    """The two probes from the ticket, side by side, as one assertion.

    Before the fix: raise → 0 records, return → 1. The manner of refusing is an
    implementation detail of the call site; it must not decide whether the
    refusal happened as far as the audit trail is concerned.
    """
    raise_db, raise_orch, raise_run, now = _stolen_lease_orchestrator(
        tmp_path / "raise", "denial-raise"
    )
    with pytest.raises(CompletionFenceLostError):
        raise_orch.advance(raise_run["id"], now=now)
    raised = _fence_denials(raise_db, raise_run["id"])

    return_db, return_orch, return_run, now = _stolen_lease_orchestrator(
        tmp_path / "return", "denial-return"
    )
    assert return_orch.advance_safely(return_run["id"], now=now) is None
    returned = _fence_denials(return_db, return_run["id"])

    assert len(raised) == 1, "the raising flavour lost its denial record"
    assert len(returned) == 1, "the returning flavour lost its denial record"
    assert raised[0]["reason_code"] == returned[0]["reason_code"] == (
        ReasonCode.PUBLICATION_FENCE_LOST
    )


def test_fence_denial_outlives_the_work_the_raise_abandons(tmp_path):
    """The aborted advance leaves no state change — and the denial remains.

    Both halves matter. The first shows the exception really did unwind the
    work; the second shows the record was not part of that work.
    """
    db_path, orch, run, now = _stolen_lease_orchestrator(tmp_path, "outlives")
    before = runtime_db.get_completion(db_path, run["id"])

    with pytest.raises(CompletionFenceLostError):
        orch.advance(run["id"], now=now)

    after = runtime_db.get_completion(db_path, run["id"])
    assert after["version"] == before["version"]
    assert after["completion_state"] == before["completion_state"]

    denials = _fence_denials(db_path, run["id"])
    assert len(denials) == 1
    assert "no longer owns a live lease" in denials[0]["message"]


def test_fence_denial_is_durable_not_merely_visible(tmp_path):
    """Read the record through a connection that never saw the failing work."""
    db_path, orch, run, now = _stolen_lease_orchestrator(tmp_path, "durable")
    with pytest.raises(CompletionFenceLostError):
        orch.advance(run["id"], now=now)
    with sqlite3.connect(str(db_path)) as fresh:
        rows = fresh.execute(
            "SELECT COUNT(*) FROM completion_event WHERE run_id = ? AND event_type = ?",
            (run["id"], EV_PUBLICATION_FENCE_DENIED),
        ).fetchone()[0]
    assert rows == 1


def test_operator_initiated_merge_records_the_fence_refusal_it_raises(tmp_path):
    """`request_manual_merge` never caught the fence error — so it left no trace.

    An operator pressing "merge" on a campaign whose lease was taken is the
    single most interesting refusal in this layer, and it was the quietest.
    """
    db_path, orch, run, now = _stolen_lease_orchestrator(tmp_path, "manual-merge")
    completion = runtime_db.get_completion(db_path, run["id"])
    runtime_db.update_completion(
        db_path, run["id"], expected_version=completion["version"],
        fields={
            "completion_state": CompletionState.AWAITING_MERGE,
            "last_reason_code": ReasonCode.AWAITING_MANUAL_MERGE,
            "policy_json": completion["policy_json"].replace(
                '"merge_mode":"auto_after_checks"', '"merge_mode":"manual"'
            ),
        },
    )

    with pytest.raises((CompletionFenceLostError, ManualMergeError)):
        orch.request_manual_merge(run["id"], confirmed=True, now=now)

    assert len(_fence_denials(db_path, run["id"])) >= 1


def test_an_incomplete_fence_policy_is_audited_too(db_path, tmp_path):
    """Half a fence is a refusal like any other, and was equally silent.

    A policy carrying an owner but no campaign (or the reverse) is unverifiable,
    so the fence fails closed — and now says so on the record.
    """
    _remote, work = build_repo(tmp_path)
    branch = "task/half-fence"
    make_task_branch(work, branch)
    run = seed_completed_run(db_path, repository_path=str(work), branch=branch)
    orch = CompletionOrchestrator(db_path, github=FakeGitHubClient())
    orch.begin_completion(
        run,
        project_cfg={"default_branch": "main", "validation_required": False},
        policy_overrides={"publication_fence_owner": "owner-a"},  # no campaign id
    )
    row = runtime_db.get_completion(db_path, run["id"])

    with pytest.raises(CompletionFenceLostError):
        orch._assert_publication_fence(row)

    denials = _fence_denials(db_path, run["id"])
    assert len(denials) == 1
    assert "policy is incomplete" in denials[0]["message"]


def test_an_open_fence_writes_no_denial(tmp_path, db_path):
    """The counterweight: a permitted action must not manufacture a refusal."""
    _remote, work = build_repo(tmp_path)
    branch = "task/open-fence"
    make_task_branch(work, branch)
    run = seed_completed_run(db_path, repository_path=str(work), branch=branch)

    store = DailyAuditStore(db_path)
    campaign = store.acquire_due(now=NOW, owner="owner-a", lease_duration=timedelta(hours=1))
    orch = CompletionOrchestrator(
        db_path, github=FakeGitHubClient(), publication_fence_clock=lambda: NOW
    )
    orch.begin_completion(
        run,
        project_cfg={"default_branch": "main", "validation_required": False},
        policy_overrides={
            "publication_fence_campaign_id": campaign,
            "publication_fence_owner": "owner-a",
        },
    )
    row = runtime_db.get_completion(db_path, run["id"])
    orch._assert_publication_fence(row, renew_for_action=True)

    assert _fence_denials(db_path, run["id"]) == []


def test_a_failed_denial_write_never_swallows_the_refusal(tmp_path, monkeypatch, caplog):
    """Fail closed twice over: an unrecordable refusal still refuses, loudly.

    The one thing worse than a refusal with no record is a refusal that stops
    being a refusal because its record could not be written.
    """
    db_path, orch, run, now = _stolen_lease_orchestrator(tmp_path, "audit-write-fails")

    def _explode(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(runtime_db, "append_completion_event", _explode)
    row = runtime_db.get_completion(db_path, run["id"])
    with caplog.at_level("ERROR"):
        with pytest.raises(CompletionFenceLostError):
            orch._assert_publication_fence(row)
    assert "could not be recorded" in caplog.text


# ==========================================================================
# Layer 3 — autonomy dispatch/execution boundary (runtime.autonomy_service)
# ==========================================================================


def _approved_proposal(engine, *, allow_dispatch: bool):
    policy = AutonomyPolicy(
        enabled=True,
        allowed_kinds=frozenset({ProposalKind.TASK_CREATION}),
        allow_execution_dispatch=allow_dispatch,
    )
    proposal = engine.create_proposal(
        kind=ProposalKind.TASK_CREATION, project="AICC", title="Add tests",
        rationale="Coverage gap detected in module X",
        evidence=[observe("task_gap", "project_intelligence.compute", "no tests")],
        policy=policy,
    )
    engine.assess(proposal["id"], policy=policy)
    return engine.approve(proposal["id"], actor="operator", reason="ok")


def test_dispatch_refusal_survives_the_exception_that_reports_it(db_path):
    """The refusal is recorded, the proposal is untouched, and the caller raises."""
    engine = AutonomyEngine(db_path)
    proposal = _approved_proposal(engine, allow_dispatch=False)
    before = engine.get(proposal["id"])

    with pytest.raises(DispatchNotPermittedError):
        engine.dispatch(proposal["id"], actor="operator")

    after = engine.get(proposal["id"])
    assert after["state"] == before["state"]
    assert after["version"] == before["version"]

    refusals = [
        e for e in engine.events(proposal["id"])
        if e["event_type"] == A.EventType.DISPATCH
        and (e.get("metadata") or {}).get("dispatch_allowed") is False
    ]
    assert len(refusals) == 1
    assert refusals[0]["reason_code"] == A.ReasonCode.DISPATCH_DISABLED


def test_execution_mismatch_refusal_survives_the_exception_that_reports_it(db_path):
    """A confirmation that does not match the authorised payload leaves a record.

    This is the theft-shaped case in this layer: someone reports having executed
    something other than what was approved.
    """
    engine = AutonomyEngine(db_path)
    proposal = _approved_proposal(engine, allow_dispatch=True)
    engine.dispatch(proposal["id"], actor="operator")

    with pytest.raises(IllegalProposalActionError):
        engine.confirm_execution(proposal["id"], actor="operator", task_id="not-a-real-task")

    mismatches = [
        e for e in engine.events(proposal["id"])
        if e["reason_code"] == A.ReasonCode.EXECUTION_MISMATCH
    ]
    assert len(mismatches) == 1
    assert engine.get(proposal["id"])["state"] == A.ProposalState.DISPATCHED


# ==========================================================================
# Layer 4 — scheduler lease (daily_audit.DailyAuditStore)
# ==========================================================================


def test_finish_after_losing_the_lease_commits_the_interruption_record(db_path):
    """A campaign that finishes without its lease is recorded as interrupted.

    `finish` refuses by returning False. The record must already be durable at
    that point — a caller that then crashes must not take it down.
    """
    store = DailyAuditStore(db_path)
    campaign = store.acquire_due(now=NOW, owner="owner-a", lease_duration=timedelta(minutes=1))
    later = NOW + timedelta(minutes=5)
    assert store.acquire_due(now=later, owner="owner-b", lease_duration=timedelta(hours=1))

    accepted = store.finish(
        campaign,
        CampaignResult(status="succeeded", summary="done", target_verified=True),
        owner="owner-a", now=later, interval=timedelta(days=1),
    )
    assert accepted is False

    with sqlite3.connect(str(db_path)) as fresh:
        fresh.row_factory = sqlite3.Row
        row = fresh.execute(
            "SELECT status, summary FROM daily_audit_campaign WHERE id = ?", (campaign,)
        ).fetchone()
    assert row["status"] == "interrupted"
    assert "losing its scheduler lease" in row["summary"]


def test_fail_after_losing_the_lease_commits_the_interruption_record(db_path):
    """Same guarantee on the failure path, where the record matters most."""
    store = DailyAuditStore(db_path)
    campaign = store.acquire_due(now=NOW, owner="owner-a", lease_duration=timedelta(minutes=1))
    later = NOW + timedelta(minutes=5)
    assert store.acquire_due(now=later, owner="owner-b", lease_duration=timedelta(hours=1))

    assert store.fail(campaign, "boom", owner="owner-a", now=later) is None

    with sqlite3.connect(str(db_path)) as fresh:
        fresh.row_factory = sqlite3.Row
        row = fresh.execute(
            "SELECT status, summary FROM daily_audit_campaign WHERE id = ?", (campaign,)
        ).fetchone()
    assert row["status"] == "interrupted"
    assert "lost its scheduler lease" in row["summary"]
