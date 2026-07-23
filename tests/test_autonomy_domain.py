"""Pure-domain tests for the autonomy proposal foundation (AICC-AUTONOMY-002).

No DB, no I/O — exercises the deterministic core: policy defaults, risk
classification, the proposal state machine, evidence model, and the eligibility
rules including every denial branch and malformed input."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from command_center.runtime import autonomy as A
from command_center.runtime.autonomy import (
    AutonomyPolicy,
    Evidence,
    ProposalKind,
    ProposalState,
    ReasonCode,
    RiskLevel,
    evaluate_eligibility,
)

# A fixed reference clock so staleness checks are deterministic.
NOW = "2026-07-23T12:00:00"


def _ev(**overrides) -> Evidence:
    data = {
        "kind": "task_gap",
        "source": "project_intelligence.compute",
        "summary": "no tests for module X",
        "observed_at": NOW,
        "data": {},
        "is_blocker": False,
    }
    data.update(overrides)
    return Evidence(**data)


def _open_policy(**overrides) -> AutonomyPolicy:
    base = dict(enabled=True, allowed_kinds=frozenset({ProposalKind.TASK_CREATION}))
    base.update(overrides)
    return AutonomyPolicy(**base)


# --------------------------------------------------------------------------
# Policy — conservative by default
# --------------------------------------------------------------------------


def test_default_policy_is_fully_closed():
    p = AutonomyPolicy()
    assert p.enabled is False
    assert p.allowed_kinds == frozenset()
    assert p.auto_approve_max_risk == RiskLevel.NONE
    assert p.allow_execution_dispatch is False
    assert p.may_auto_approve(RiskLevel.LOW) is False


def test_policy_never_auto_approves_critical_even_if_configured():
    # A policy that names CRITICAL is clamped down to HIGH, and CRITICAL is
    # never auto-approvable regardless.
    p = AutonomyPolicy(enabled=True, auto_approve_max_risk=RiskLevel.CRITICAL)
    assert p.auto_approve_max_risk == RiskLevel.HIGH
    assert p.may_auto_approve(RiskLevel.CRITICAL) is False


def test_policy_auto_approve_respects_ceiling():
    p = _open_policy(auto_approve_max_risk=RiskLevel.LOW)
    assert p.may_auto_approve(RiskLevel.LOW) is True
    assert p.may_auto_approve(RiskLevel.MEDIUM) is False
    assert p.may_auto_approve(RiskLevel.HIGH) is False


def test_policy_disabled_never_auto_approves():
    p = AutonomyPolicy(enabled=False, auto_approve_max_risk=RiskLevel.HIGH)
    assert p.may_auto_approve(RiskLevel.LOW) is False


def test_policy_roundtrips_through_json():
    p = _open_policy(auto_approve_max_risk=RiskLevel.MEDIUM, allow_execution_dispatch=True)
    restored = AutonomyPolicy.from_json(p.to_json())
    assert restored.to_dict() == p.to_dict()


def test_policy_from_malformed_json_is_closed_default():
    assert AutonomyPolicy.from_json("not json").to_dict() == AutonomyPolicy().to_dict()
    assert AutonomyPolicy.from_json(None).to_dict() == AutonomyPolicy().to_dict()


def test_policy_unknown_risk_ceiling_falls_back_to_none():
    p = AutonomyPolicy(enabled=True, auto_approve_max_risk="BOGUS")
    assert p.auto_approve_max_risk == RiskLevel.NONE


# --------------------------------------------------------------------------
# Risk classification — deterministic from kind + evidence
# --------------------------------------------------------------------------


def test_risk_floor_by_kind():
    assert A.classify_risk(ProposalKind.TASK_CREATION, [_ev()]) == RiskLevel.LOW
    assert A.classify_risk(ProposalKind.PRIORITY_CHANGE, [_ev()]) == RiskLevel.LOW
    assert A.classify_risk(ProposalKind.TASK_EXECUTION, [_ev()]) == RiskLevel.HIGH
    assert A.classify_risk(ProposalKind.MERGE, [_ev()]) == RiskLevel.CRITICAL


def test_risk_unknown_kind_defaults_high():
    assert A.classify_risk("SOMETHING_NEW", [_ev()]) == RiskLevel.HIGH


def test_risk_escalates_on_blocker_and_signal_and_sensitive():
    assert A.classify_risk(ProposalKind.TASK_CREATION, [_ev(is_blocker=True)]) == RiskLevel.HIGH
    assert (
        A.classify_risk(ProposalKind.PRIORITY_CHANGE, [_ev(data={"risk_signal": "CRITICAL"})])
        == RiskLevel.CRITICAL
    )
    assert (
        A.classify_risk(ProposalKind.TASK_CREATION, [_ev(data={"sensitive": True})]) == RiskLevel.HIGH
    )


def test_risk_never_deescalates_below_floor():
    # A "low" signal cannot pull TASK_EXECUTION below its HIGH floor.
    assert (
        A.classify_risk(ProposalKind.TASK_EXECUTION, [_ev(data={"risk_signal": "LOW"})])
        == RiskLevel.HIGH
    )


def test_risk_at_most_treats_unknown_as_critical():
    assert A.risk_at_most("BOGUS", RiskLevel.HIGH) is False
    assert A.risk_at_most(RiskLevel.LOW, RiskLevel.HIGH) is True


# --------------------------------------------------------------------------
# Evidence model + digest
# --------------------------------------------------------------------------


def test_evidence_requires_source():
    with pytest.raises(ValueError):
        Evidence(kind="x", source="", summary="s")
    with pytest.raises(ValueError):
        Evidence(kind="x", source="   ", summary="s")


def test_evidence_requires_kind():
    with pytest.raises(ValueError):
        Evidence(kind="", source="s", summary="s")


def test_evidence_autofills_observed_at():
    e = Evidence(kind="x", source="s", summary="y")
    assert e.observed_at  # non-empty


def test_evidence_digest_is_order_independent_and_content_sensitive():
    a, b = _ev(kind="a"), _ev(kind="b")
    assert A.evidence_digest([a, b]) == A.evidence_digest([b, a])
    assert A.evidence_digest([a]) != A.evidence_digest([a, b])
    assert A.evidence_digest([_ev(summary="one")]) != A.evidence_digest([_ev(summary="two")])


def test_evidence_age_seconds():
    later = (datetime.fromisoformat(NOW) + timedelta(seconds=90)).isoformat(timespec="seconds")
    assert A.evidence_age_seconds(_ev(observed_at=NOW), now=later) == pytest.approx(90)


def test_evidence_age_unparseable_is_none():
    assert A.evidence_age_seconds(_ev(observed_at="garbage"), now=NOW) is None


# --------------------------------------------------------------------------
# State machine
# --------------------------------------------------------------------------


def test_same_state_transition_always_allowed():
    for state in A.ALL_STATES:
        assert A.is_valid_proposal_transition(state, state) is True


def test_terminal_states_have_no_exit():
    for term in A.TERMINAL_STATES:
        for other in A.ALL_STATES:
            if other != term:
                assert A.is_valid_proposal_transition(term, other) is False


def test_representative_legal_and_illegal_edges():
    assert A.is_valid_proposal_transition(ProposalState.DRAFT, ProposalState.PROPOSED)
    assert A.is_valid_proposal_transition(ProposalState.AWAITING_APPROVAL, ProposalState.APPROVED)
    assert A.is_valid_proposal_transition(ProposalState.APPROVED, ProposalState.DISPATCHED)
    assert A.is_valid_proposal_transition(ProposalState.DISPATCHED, ProposalState.EXECUTED)
    # Illegal: cannot skip the gate, cannot jump blocked straight to approved.
    assert not A.is_valid_proposal_transition(ProposalState.PROPOSED, ProposalState.APPROVED)
    assert not A.is_valid_proposal_transition(ProposalState.BLOCKED, ProposalState.APPROVED)
    assert not A.is_valid_proposal_transition(ProposalState.DRAFT, ProposalState.EXECUTED)


def test_withdraw_reachable_from_every_nonterminal_state_except_dispatched():
    # DISPATCHED is the deliberate exception: once the execution boundary has
    # been crossed, a proposal is confirmed (EXECUTED) or failed back (BLOCKED),
    # never silently withdrawn.
    withdrawable = A.ALL_STATES - A.TERMINAL_STATES - {ProposalState.DISPATCHED}
    for state in withdrawable:
        assert A.is_valid_proposal_transition(state, ProposalState.WITHDRAWN)
    assert not A.is_valid_proposal_transition(ProposalState.DISPATCHED, ProposalState.WITHDRAWN)


def test_unknown_state_permits_no_transition():
    assert A.is_valid_proposal_transition("NONSENSE", ProposalState.APPROVED) is False


# --------------------------------------------------------------------------
# Eligibility — every branch, denials default to blocked
# --------------------------------------------------------------------------


def test_eligibility_blocked_when_policy_disabled():
    v = evaluate_eligibility(kind=ProposalKind.TASK_CREATION, evidence=[_ev()],
                             policy=AutonomyPolicy(), now=NOW)
    assert v.decision == A.DECISION_BLOCKED
    assert v.primary_reason == ReasonCode.POLICY_DISABLED
    assert v.next_state == ProposalState.BLOCKED
    assert v.requires_human is True


def test_eligibility_blocked_when_kind_not_allowed():
    pol = AutonomyPolicy(enabled=True, allowed_kinds=frozenset({ProposalKind.PRIORITY_CHANGE}))
    v = evaluate_eligibility(kind=ProposalKind.TASK_CREATION, evidence=[_ev()], policy=pol, now=NOW)
    assert v.primary_reason == ReasonCode.KIND_NOT_ALLOWED
    assert v.decision == A.DECISION_BLOCKED


def test_eligibility_blocked_when_no_evidence():
    v = evaluate_eligibility(kind=ProposalKind.TASK_CREATION, evidence=[], policy=_open_policy(), now=NOW)
    assert v.primary_reason == ReasonCode.EVIDENCE_MISSING


def test_eligibility_blocked_when_evidence_stale():
    old = (datetime.fromisoformat(NOW) - timedelta(seconds=10_000)).isoformat(timespec="seconds")
    v = evaluate_eligibility(kind=ProposalKind.TASK_CREATION, evidence=[_ev(observed_at=old)],
                             policy=_open_policy(max_evidence_age_seconds=3600), now=NOW)
    assert v.primary_reason == ReasonCode.EVIDENCE_STALE
    assert v.decision == A.DECISION_BLOCKED


def test_eligibility_blocked_on_blocker_evidence():
    v = evaluate_eligibility(kind=ProposalKind.TASK_CREATION, evidence=[_ev(is_blocker=True)],
                             policy=_open_policy(), now=NOW)
    assert v.primary_reason == ReasonCode.EVIDENCE_BLOCKER
    assert v.decision == A.DECISION_BLOCKED


def test_eligibility_human_gate_when_risk_exceeds_ceiling():
    # TASK_EXECUTION is HIGH; policy only auto-approves up to LOW.
    pol = AutonomyPolicy(enabled=True, allowed_kinds=frozenset({ProposalKind.TASK_EXECUTION}),
                         auto_approve_max_risk=RiskLevel.LOW)
    v = evaluate_eligibility(kind=ProposalKind.TASK_EXECUTION, evidence=[_ev()], policy=pol, now=NOW)
    assert v.decision == A.DECISION_ELIGIBLE
    assert v.next_state == ProposalState.AWAITING_APPROVAL
    assert v.requires_human is True
    assert ReasonCode.RISK_EXCEEDS_POLICY in v.reasons


def test_eligibility_auto_approves_within_ceiling():
    pol = _open_policy(auto_approve_max_risk=RiskLevel.LOW)
    v = evaluate_eligibility(kind=ProposalKind.TASK_CREATION, evidence=[_ev()], policy=pol, now=NOW)
    assert v.decision == A.DECISION_ELIGIBLE
    assert v.next_state == ProposalState.APPROVED
    assert v.requires_human is False
    assert v.primary_reason == ReasonCode.AUTO_APPROVED


def test_eligibility_critical_never_auto_even_when_allowed():
    pol = AutonomyPolicy(enabled=True, allowed_kinds=A.ALL_KINDS,
                         auto_approve_max_risk=RiskLevel.HIGH)
    v = evaluate_eligibility(kind=ProposalKind.MERGE, evidence=[_ev()], policy=pol, now=NOW)
    assert v.risk == RiskLevel.CRITICAL
    assert v.next_state == ProposalState.AWAITING_APPROVAL
    assert v.requires_human is True
    assert ReasonCode.RISK_CRITICAL_NO_AUTO in v.reasons


def test_eligibility_is_reproducible():
    pol = _open_policy(auto_approve_max_risk=RiskLevel.LOW)
    args = dict(kind=ProposalKind.TASK_CREATION, evidence=[_ev()], policy=pol, now=NOW)
    assert evaluate_eligibility(**args).to_dict() == evaluate_eligibility(**args).to_dict()


# --------------------------------------------------------------------------
# Dry-run planning — pure, side-effect-free, never launches
# --------------------------------------------------------------------------


def test_plan_for_task_execution_routes_through_start_run():
    plan = A.build_execution_plan(kind=ProposalKind.TASK_EXECUTION, title="run it",
                                  rationale="ready", parameters={"task_id": "t1"})
    assert plan.dispatch_route == "ExecutionCenterAPI.start_run"
    assert plan.requires_confirmation is True
    assert any("start_run" in step for step in plan.steps)


def test_plan_for_merge_is_advisory_only():
    plan = A.build_execution_plan(kind=ProposalKind.MERGE, title="merge", rationale="done")
    assert "never performed by autonomy" in " ".join(plan.steps).lower()
