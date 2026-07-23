"""End-to-end lifecycle tests for the autonomy orchestration engine.

Exercises the full recommendation -> approval -> execution boundary: the
blocked path, the human-approval path, policy auto-approval, the dispatch
refusal gate, dispatch + confirm, rejection/withdrawal/override, the
critical-never-auto rule, audit-trail completeness, reproducibility, and
malformed input. The load-bearing safety assertion is that the engine never
executes anything on its own."""

from __future__ import annotations

import pytest

from command_center.runtime import autonomy as A
from command_center.runtime import db as runtime_db
from command_center.runtime.autonomy import AutonomyPolicy, ProposalKind, ProposalState, RiskLevel
from command_center.runtime.autonomy_service import (
    AutonomyEngine,
    DispatchNotPermittedError,
    IllegalProposalActionError,
    UnknownProposalError,
    observe,
)


@pytest.fixture
def engine():
    return AutonomyEngine(runtime_db.resolve_db_path())


def _evidence():
    return [observe("task_gap", "project_intelligence.compute", "no tests for module X",
                    data={"module": "X"})]


def _closed_policy():
    return AutonomyPolicy()  # default: everything blocked


def _human_gate_policy():
    return AutonomyPolicy(enabled=True, allowed_kinds=frozenset({ProposalKind.TASK_CREATION}))


def _auto_policy():
    return AutonomyPolicy(enabled=True, allowed_kinds=frozenset({ProposalKind.TASK_CREATION}),
                          auto_approve_max_risk=RiskLevel.LOW)


def _dispatch_policy():
    return AutonomyPolicy(enabled=True, allowed_kinds=frozenset({ProposalKind.TASK_CREATION}),
                          allow_execution_dispatch=True)


def _create(engine, policy, **overrides):
    kwargs = dict(kind=ProposalKind.TASK_CREATION, project="AICC", title="Add tests",
                  rationale="Coverage gap detected in module X", evidence=_evidence(), policy=policy)
    kwargs.update(overrides)
    return engine.create_proposal(**kwargs)


# --------------------------------------------------------------------------
# Create
# --------------------------------------------------------------------------


def test_create_records_rationale_evidence_and_digest(engine):
    p = _create(engine, _human_gate_policy())
    assert p["state"] == ProposalState.DRAFT
    assert p["rationale"].startswith("Coverage gap")
    assert p["evidence_digest"]
    ev = engine.evidence(p["id"])
    assert len(ev) == 1 and ev[0]["source"] == "project_intelligence.compute"
    # The very first audit event explains why the proposal exists.
    events = engine.events(p["id"])
    assert events[0]["event_type"] == A.EventType.CREATED
    assert events[0]["message"] == p["rationale"]
    assert p["parameters_json"] == (
        '{"project":"AICC","task_type":"implementation","title":"Add tests"}'
    )


def test_create_rejects_unknown_kind(engine):
    with pytest.raises(ValueError):
        engine.create_proposal(kind="BOGUS", project="AICC", title="x", rationale="y")


def test_create_rejects_blank_rationale(engine):
    with pytest.raises(ValueError):
        engine.create_proposal(kind=ProposalKind.TASK_CREATION, project="AICC", title="x", rationale="")


# --------------------------------------------------------------------------
# Assess — the deterministic routing
# --------------------------------------------------------------------------


def test_assess_blocks_when_autonomy_disabled(engine):
    p = engine.assess(_create(engine, _closed_policy())["id"])
    assert p["state"] == ProposalState.BLOCKED
    assert p["last_reason_code"] == A.ReasonCode.POLICY_DISABLED
    assert p["eligibility_json"]  # verdict stored for reproducibility


def test_assess_routes_to_human_gate(engine):
    p = engine.assess(_create(engine, _human_gate_policy())["id"])
    assert p["state"] == ProposalState.AWAITING_APPROVAL
    assert p["requires_human"] == 1


def test_assess_auto_approves_within_policy(engine):
    p = engine.assess(_create(engine, _auto_policy())["id"])
    assert p["state"] == ProposalState.APPROVED
    assert p["decided_by"] == "policy:auto"
    assert p["requires_human"] == 0


def test_assess_blocks_on_blocker_evidence(engine):
    p = engine.create_proposal(
        kind=ProposalKind.TASK_CREATION, project="AICC", title="x",
        rationale="dirty tree", policy=_auto_policy(),
        evidence=[observe("git_status", "git_info.get_status", "working tree dirty", is_blocker=True)],
    )
    p = engine.assess(p["id"])
    assert p["state"] == ProposalState.BLOCKED
    assert p["last_reason_code"] == A.ReasonCode.EVIDENCE_BLOCKER


def test_assess_blocks_when_no_evidence(engine):
    p = engine.create_proposal(kind=ProposalKind.TASK_CREATION, project="AICC", title="x",
                               rationale="no evidence attached", policy=_human_gate_policy(), evidence=[])
    p = engine.assess(p["id"])
    assert p["state"] == ProposalState.BLOCKED
    assert p["last_reason_code"] == A.ReasonCode.EVIDENCE_MISSING


def test_assess_is_reproducible_from_stored_evidence(engine):
    p = _create(engine, _human_gate_policy())
    first = engine.assess(p["id"])
    # Re-assessing a settled proposal is a no-op; the stored verdict stands and
    # re-running the pure evaluator on the frozen evidence reproduces it.
    again = engine.assess(p["id"])
    assert again["eligibility_json"] == first["eligibility_json"]
    import json
    verdict = json.loads(first["eligibility_json"])
    assert verdict["decision"] == A.DECISION_ELIGIBLE
    assert verdict["risk"] == RiskLevel.LOW


def test_merge_is_critical_and_never_auto(engine):
    permissive = AutonomyPolicy(enabled=True, allowed_kinds=A.ALL_KINDS,
                                auto_approve_max_risk=RiskLevel.HIGH, allow_execution_dispatch=True)
    p = engine.create_proposal(kind=ProposalKind.MERGE, project="AICC", title="merge",
                               rationale="pr looks ready", policy=permissive,
                               evidence=[observe("pr_state", "github.get_pr", "pr open, checks green")])
    p = engine.assess(p["id"])
    assert p["risk_level"] == RiskLevel.CRITICAL
    assert p["state"] == ProposalState.AWAITING_APPROVAL


# --------------------------------------------------------------------------
# Human decision gates
# --------------------------------------------------------------------------


def test_approve_requires_actor(engine):
    p = engine.assess(_create(engine, _human_gate_policy())["id"])
    with pytest.raises(ValueError):
        engine.approve(p["id"], actor="")


def test_approve_moves_to_approved_and_records_decider(engine):
    p = engine.assess(_create(engine, _human_gate_policy())["id"])
    p = engine.approve(p["id"], actor="dima@me.com", reason="looks good")
    assert p["state"] == ProposalState.APPROVED
    assert p["decided_by"] == "dima@me.com"
    assert p["decision_reason"] == "looks good"
    assert p["requires_human"] == 0


def test_reject_is_terminal(engine):
    p = engine.assess(_create(engine, _human_gate_policy())["id"])
    p = engine.reject(p["id"], actor="dima@me.com", reason="not now")
    assert p["state"] == ProposalState.REJECTED
    with pytest.raises(IllegalProposalActionError):
        engine.approve(p["id"], actor="dima@me.com")


def test_reject_requires_reason(engine):
    p = engine.assess(_create(engine, _human_gate_policy())["id"])
    with pytest.raises(ValueError):
        engine.reject(p["id"], actor="dima@me.com", reason="")


def test_blocked_proposal_can_be_overridden_to_approval(engine):
    p = engine.assess(_create(engine, _closed_policy())["id"])
    assert p["state"] == ProposalState.BLOCKED
    p = engine.approve(p["id"], actor="dima@me.com", reason="override: I accept the risk")
    assert p["state"] == ProposalState.APPROVED
    types = [e["event_type"] for e in engine.events(p["id"])]
    assert A.EventType.OVERRIDE in types  # the override hop is audited


def test_withdraw_is_terminal(engine):
    p = engine.assess(_create(engine, _human_gate_policy())["id"])
    p = engine.withdraw(p["id"], actor="engine", reason="superseded")
    assert p["state"] == ProposalState.WITHDRAWN


# --------------------------------------------------------------------------
# Execution boundary — the core safety property
# --------------------------------------------------------------------------


def test_dispatch_refused_without_explicit_policy(engine):
    p = engine.assess(_create(engine, _human_gate_policy())["id"])
    p = engine.approve(p["id"], actor="dima@me.com")
    with pytest.raises(DispatchNotPermittedError):
        engine.dispatch(p["id"], actor="dima@me.com")
    # The proposal is untouched — nothing crossed the boundary.
    assert engine.get(p["id"])["state"] == ProposalState.APPROVED
    # The refusal is recorded in the audit trail.
    reasons = [e["reason_code"] for e in engine.events(p["id"])]
    assert A.ReasonCode.DISPATCH_DISABLED in reasons


def test_dispatch_only_from_approved(engine):
    p = engine.assess(_create(engine, _dispatch_policy())["id"])  # AWAITING_APPROVAL
    with pytest.raises(IllegalProposalActionError):
        engine.dispatch(p["id"], actor="dima@me.com")


def test_dispatch_returns_plan_but_executes_nothing(engine):
    p = engine.assess(_create(engine, _dispatch_policy())["id"])
    p = engine.approve(p["id"], actor="dima@me.com")
    result = engine.dispatch(p["id"], actor="dima@me.com")
    assert result["proposal"]["state"] == ProposalState.DISPATCHED
    assert result["plan"]["dispatch_route"] == "db.create_task"
    assert result["plan"]["requires_confirmation"] is True
    assert result["plan"]["executable"] is True
    assert len(result["plan"]["action_digest"]) == 64
    # No task/run has been created as a side effect of dispatch.
    assert runtime_db.list_tasks(engine.db_path) == []


def test_confirm_execution_links_real_work(engine):
    p = engine.assess(_create(engine, _dispatch_policy())["id"])
    p = engine.approve(p["id"], actor="dima@me.com")
    engine.dispatch(p["id"], actor="dima@me.com")
    # The caller — not the engine — performs the action, then reports it back.
    task = runtime_db.create_task(engine.db_path, project="AICC", title="Add tests",
                                  task_type="implementation")
    p = engine.confirm_execution(p["id"], actor="executor", task_id=task["id"])
    assert p["state"] == ProposalState.EXECUTED
    assert p["dispatched_task_id"] == task["id"]


def test_fail_dispatch_routes_back_to_blocked(engine):
    p = engine.assess(_create(engine, _dispatch_policy())["id"])
    p = engine.approve(p["id"], actor="dima@me.com")
    engine.dispatch(p["id"], actor="dima@me.com")
    p = engine.fail_dispatch(p["id"], actor="executor", reason="repository unavailable")
    assert p["state"] == ProposalState.BLOCKED
    assert p["last_reason_code"] == A.ReasonCode.DISPATCH_FAILED


def test_full_audit_trail_for_happy_path(engine):
    p = engine.assess(_create(engine, _dispatch_policy())["id"])
    p = engine.approve(p["id"], actor="dima@me.com")
    engine.dispatch(p["id"], actor="dima@me.com")
    task = runtime_db.create_task(
        engine.db_path,
        project="AICC",
        title="Add tests",
        task_type="implementation",
    )
    p = engine.confirm_execution(p["id"], actor="executor", task_id=task["id"])
    states = [e["to_state"] for e in engine.events(p["id"])]
    # Every lifecycle step left a durable, ordered record.
    assert states == [
        ProposalState.DRAFT,       # created
        ProposalState.DRAFT,       # assessed (verdict recorded in place)
        ProposalState.PROPOSED,
        ProposalState.ELIGIBLE,
        ProposalState.AWAITING_APPROVAL,
        ProposalState.APPROVED,
        ProposalState.DISPATCHED,
        ProposalState.EXECUTED,
    ]


def test_confirm_execution_rejects_missing_or_foreign_result(engine):
    p = engine.assess(_create(engine, _dispatch_policy())["id"])
    p = engine.approve(p["id"], actor="dima@me.com")
    engine.dispatch(p["id"], actor="dima@me.com")

    with pytest.raises(ValueError, match="run_id or task_id"):
        engine.confirm_execution(p["id"], actor="executor")

    foreign = runtime_db.create_task(
        engine.db_path,
        project="OTHER",
        title="Different action",
        task_type="documentation",
    )
    with pytest.raises(IllegalProposalActionError, match="does not match"):
        engine.confirm_execution(p["id"], actor="executor", task_id=foreign["id"])
    assert engine.get(p["id"])["state"] == ProposalState.DISPATCHED
    assert engine.events(p["id"])[-1]["reason_code"] == A.ReasonCode.EXECUTION_MISMATCH


def test_confirm_execution_rejects_matching_result_created_before_dispatch(engine):
    existing = runtime_db.create_task(
        engine.db_path,
        project="AICC",
        title="Add tests",
        task_type="implementation",
    )
    with runtime_db.connect(engine.db_path) as conn:
        with runtime_db.transaction(conn):
            conn.execute(
                "UPDATE task SET created_at = ? WHERE id = ?",
                ("2000-01-01T00:00:00", existing["id"]),
            )

    p = engine.assess(_create(engine, _dispatch_policy())["id"])
    p = engine.approve(p["id"], actor="dima@me.com")
    engine.dispatch(p["id"], actor="dima@me.com")
    with pytest.raises(IllegalProposalActionError, match="predates"):
        engine.confirm_execution(p["id"], actor="executor", task_id=existing["id"])
    assert engine.get(p["id"])["state"] == ProposalState.DISPATCHED


def test_advisory_metadata_plan_cannot_dispatch(engine):
    task = runtime_db.create_task(
        engine.db_path,
        project="AICC",
        title="Prioritise",
        task_type="planning",
    )
    policy = AutonomyPolicy(
        enabled=True,
        allowed_kinds={ProposalKind.PRIORITY_CHANGE},
        allow_execution_dispatch=True,
    )
    p = engine.create_proposal(
        kind=ProposalKind.PRIORITY_CHANGE,
        project="AICC",
        title="Raise priority",
        rationale="critical path",
        task_id=task["id"],
        parameters={"priority": "High"},
        policy=policy,
        evidence=_evidence(),
    )
    p = engine.assess(p["id"])
    p = engine.approve(p["id"], actor="dima@me.com")
    with pytest.raises(DispatchNotPermittedError, match="action payload"):
        engine.dispatch(p["id"], actor="dima@me.com")
    assert engine.get(p["id"])["state"] == ProposalState.APPROVED


def test_task_execution_confirmation_is_bound_to_authorised_run(engine, tmp_path):
    task = runtime_db.create_task(
        engine.db_path,
        project="AICC",
        title="Run implementation",
        task_type="implementation",
    )
    policy = AutonomyPolicy(
        enabled=True,
        allowed_kinds={ProposalKind.TASK_EXECUTION},
        allow_execution_dispatch=True,
    )
    action = {
        "repository_path": str(tmp_path),
        "expected_branch": "feature/approved",
        "task_type": "implementation",
        "instruction": "Implement the approved change",
    }
    p = engine.create_proposal(
        kind=ProposalKind.TASK_EXECUTION,
        project="AICC",
        title="Execute task",
        rationale="task is ready",
        task_id=task["id"],
        parameters=action,
        policy=policy,
        evidence=_evidence(),
    )
    p = engine.assess(p["id"])
    p = engine.approve(p["id"], actor="dima@me.com")
    dispatched = engine.dispatch(p["id"], actor="dima@me.com")
    assert dispatched["plan"]["parameters"]["instruction"] == action["instruction"]

    session = runtime_db.create_session(
        engine.db_path,
        task_id=task["id"],
        project="AICC",
        repository_path=str(tmp_path),
    )
    run = runtime_db.create_run(
        engine.db_path,
        session_id=session["id"],
        task_id=task["id"],
        project="AICC",
        task_type="implementation",
        repository_path=str(tmp_path),
        prompt="Implement the approved change",
        is_resume=False,
        expected_branch="feature/approved",
    )
    p = engine.confirm_execution(p["id"], actor="executor", run_id=run["id"])
    assert p["state"] == ProposalState.EXECUTED
    assert p["dispatched_run_id"] == run["id"]


def test_dispatch_rechecks_evidence_freshness(engine, monkeypatch):
    observed = "2026-07-23T12:00:00"
    policy = AutonomyPolicy(
        enabled=True,
        allowed_kinds={ProposalKind.TASK_CREATION},
        allow_execution_dispatch=True,
        max_evidence_age_seconds=60,
    )
    p = engine.create_proposal(
        kind=ProposalKind.TASK_CREATION,
        project="AICC",
        title="Add tests",
        rationale="gap",
        policy=policy,
        evidence=[
            A.Evidence(
                kind="task_gap",
                source="project_intelligence",
                summary="gap",
                observed_at=observed,
            )
        ],
    )
    p = engine.assess(p["id"], now=observed)
    p = engine.approve(p["id"], actor="dima@me.com")
    monkeypatch.setattr(
        "command_center.runtime.autonomy_service.iso_now",
        lambda: "2026-07-23T12:02:00",
    )

    with pytest.raises(DispatchNotPermittedError, match="EVIDENCE_STALE"):
        engine.dispatch(p["id"], actor="dima@me.com")
    assert engine.get(p["id"])["state"] == ProposalState.APPROVED
    assert engine.events(p["id"])[-1]["reason_code"] == A.ReasonCode.EVIDENCE_STALE


def test_malformed_policy_cannot_be_overridden_into_dispatch(engine):
    malformed = AutonomyPolicy.from_dict(
        {
            "enabled": "false",
            "allowed_kinds": [ProposalKind.TASK_CREATION],
            "allow_execution_dispatch": "true",
        }
    )
    p = engine.assess(_create(engine, malformed)["id"])
    p = engine.approve(p["id"], actor="dima@me.com", reason="human override")
    with pytest.raises(DispatchNotPermittedError, match="disabled"):
        engine.dispatch(p["id"], actor="dima@me.com")
    assert engine.get(p["id"])["state"] == ProposalState.APPROVED


# --------------------------------------------------------------------------
# Misc / malformed input
# --------------------------------------------------------------------------


def test_unknown_proposal_raises(engine):
    with pytest.raises(UnknownProposalError):
        engine.assess("does-not-exist")
    with pytest.raises(UnknownProposalError):
        engine.approve("does-not-exist", actor="x")


def test_plan_is_pure_readonly(engine):
    p = _create(engine, _human_gate_policy())
    plan = engine.plan(p["id"])
    assert plan["kind"] == ProposalKind.TASK_CREATION
    # Planning a not-yet-assessed proposal does not change its state.
    assert engine.get(p["id"])["state"] == ProposalState.DRAFT
