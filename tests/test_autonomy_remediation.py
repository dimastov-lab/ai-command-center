"""Regression tests for Founder Gate remediation AICC-…-MINOR-REMEDIATION-001.

F1 — persisted policy is authoritative; a caller-supplied runtime policy may only
     further restrict (conservative intersection), never widen.
F2 — proposal creation and assessment are atomic: either the whole multi-row
     sequence commits or none of it does, and there is exactly one CREATED /
     ASSESSED event per committed operation. Includes deterministic
     failure-injection (real transactions, no sleeps) and CAS concurrency.

The primary F1 test (`test_F1_stored_forbids_caller_permits_is_refused`) fails on
da1de1c — there, a caller-supplied permissive policy overrode the stored gate and
dispatch succeeded."""

from __future__ import annotations

import json

import pytest

from command_center.runtime import autonomy as A
from command_center.runtime import db as runtime_db
from command_center.runtime.autonomy import AutonomyPolicy, ProposalKind, ProposalState, RiskLevel
from command_center.runtime.autonomy_service import (
    AutonomyEngine,
    DispatchNotPermittedError,
    observe,
)


@pytest.fixture
def engine():
    return AutonomyEngine(runtime_db.resolve_db_path())


@pytest.fixture
def db_path():
    path = runtime_db.resolve_db_path()
    runtime_db.migrate(path)
    return path


def _ev():
    return [observe("task_gap", "project_intelligence.compute", "no tests", data={"m": "X"})]


def _stored_no_dispatch():
    return AutonomyPolicy(enabled=True, allowed_kinds=frozenset({ProposalKind.TASK_CREATION}))


def _stored_dispatch():
    return AutonomyPolicy(enabled=True, allowed_kinds=frozenset({ProposalKind.TASK_CREATION}),
                          allow_execution_dispatch=True)


def _permissive():
    return AutonomyPolicy(enabled=True, allowed_kinds=A.ALL_KINDS,
                          auto_approve_max_risk=RiskLevel.HIGH, allow_execution_dispatch=True)


class _Boom(Exception):
    pass


def _fail_on(orig, n):
    state = {"calls": 0}

    def wrapper(*args, **kwargs):
        state["calls"] += 1
        if state["calls"] == n:
            raise _Boom("injected failure")
        return orig(*args, **kwargs)

    return wrapper


# ==========================================================================
# F1 — persisted policy authoritative; caller may only restrict
# ==========================================================================


def test_F1_intersect_unit_semantics():
    a = AutonomyPolicy(enabled=True, allowed_kinds=frozenset({"A", "B"}),
                       auto_approve_max_risk=RiskLevel.LOW, allow_execution_dispatch=False,
                       max_evidence_age_seconds=1000)
    b = AutonomyPolicy(enabled=True, allowed_kinds=frozenset({"B", "C"}),
                       auto_approve_max_risk=RiskLevel.HIGH, allow_execution_dispatch=True,
                       max_evidence_age_seconds=500)
    eff = a.intersect(b)
    assert eff.enabled is True
    assert eff.allowed_kinds == frozenset({"B"})               # intersection
    assert eff.auto_approve_max_risk == RiskLevel.LOW          # lower ceiling
    assert eff.allow_execution_dispatch is False               # AND
    assert eff.max_evidence_age_seconds == 500                 # stricter window
    # AND on enabled
    assert AutonomyPolicy(enabled=False).intersect(b).enabled is False


def test_F1_intersect_none_returns_self():
    a = _stored_dispatch()
    assert a.intersect(None).to_dict() == a.to_dict()


def test_F1_intersect_is_deterministic():
    a, b = _stored_no_dispatch(), _permissive()
    assert a.intersect(b).to_dict() == a.intersect(b).to_dict()


def test_F1_intersect_is_never_more_permissive_than_either():
    a, b = _stored_no_dispatch(), _permissive()
    eff = a.intersect(b)
    # capability only if BOTH grant it
    assert eff.allow_execution_dispatch == (a.allow_execution_dispatch and b.allow_execution_dispatch)
    assert eff.allowed_kinds == (a.allowed_kinds & b.allowed_kinds)
    assert eff.enabled == (a.enabled and b.enabled)


def test_F1_stored_forbids_caller_permits_is_refused(engine):
    """PRIMARY F1 TEST — fails on da1de1c (dispatch succeeded there)."""
    p = engine.create_proposal(kind=ProposalKind.TASK_CREATION, project="AICC", title="t",
                               rationale="r", policy=_stored_no_dispatch(), evidence=_ev())
    p = engine.assess(p["id"])
    p = engine.approve(p["id"], actor="dima@me.com")
    with pytest.raises(DispatchNotPermittedError):
        engine.dispatch(p["id"], actor="dima@me.com", policy=_permissive())
    # Proposal untouched, refusal audited with fingerprints + result.
    assert engine.get(p["id"])["state"] == ProposalState.APPROVED
    refusals = [e for e in engine.events(p["id"]) if e["reason_code"] == A.ReasonCode.DISPATCH_DISABLED]
    assert len(refusals) == 1
    md = refusals[0]["metadata"]
    assert md["dispatch_allowed"] is False
    assert md["persisted_policy_fingerprint"] and md["effective_policy_fingerprint"]
    assert md["runtime_policy_fingerprint"]  # a runtime policy was supplied


def test_F1_stored_permits_caller_forbids_is_refused(engine):
    p = engine.create_proposal(kind=ProposalKind.TASK_CREATION, project="AICC", title="t",
                               rationale="r", policy=_stored_dispatch(), evidence=_ev())
    p = engine.assess(p["id"])
    p = engine.approve(p["id"], actor="dima@me.com")
    caller_forbid = AutonomyPolicy(enabled=True, allowed_kinds=frozenset({ProposalKind.TASK_CREATION}))
    with pytest.raises(DispatchNotPermittedError):
        engine.dispatch(p["id"], actor="dima@me.com", policy=caller_forbid)
    assert engine.get(p["id"])["state"] == ProposalState.APPROVED


def test_F1_both_permit_dispatches_with_no_side_effects(engine):
    p = engine.create_proposal(kind=ProposalKind.TASK_CREATION, project="AICC", title="t",
                               rationale="r", policy=_stored_dispatch(), evidence=_ev())
    p = engine.assess(p["id"])
    p = engine.approve(p["id"], actor="dima@me.com")
    result = engine.dispatch(p["id"], actor="dima@me.com")  # no caller policy -> stored authoritative
    assert result["proposal"]["state"] == ProposalState.DISPATCHED
    # zero execution side effects
    assert runtime_db.list_tasks(engine.db_path) == []
    assert runtime_db.list_runs(engine.db_path) == []
    dispatched = [e for e in engine.events(p["id"]) if e["reason_code"] == A.ReasonCode.DISPATCHED]
    assert dispatched[0]["metadata"]["dispatch_allowed"] is True


def test_F1_caller_cannot_raise_auto_approval(engine):
    # Stored policy auto-approves nothing (NONE); a permissive caller at assess
    # time must NOT be able to turn a low-risk proposal into an auto-approval.
    p = engine.create_proposal(kind=ProposalKind.TASK_CREATION, project="AICC", title="t",
                               rationale="r", policy=_stored_no_dispatch(), evidence=_ev())
    p = engine.assess(p["id"], policy=AutonomyPolicy(enabled=True, allowed_kinds=A.ALL_KINDS,
                                                     auto_approve_max_risk=RiskLevel.HIGH))
    assert p["state"] == ProposalState.AWAITING_APPROVAL   # human gate, not auto-approved
    assert p["decided_by"] is None


def test_F1_missing_stored_policy_fails_closed(engine):
    p = engine.create_proposal(kind=ProposalKind.TASK_CREATION, project="AICC", title="t",
                               rationale="r", evidence=_ev())  # no policy stored
    p = engine.assess(p["id"], policy=_permissive())  # caller cannot open a closed stored policy
    assert p["state"] == ProposalState.BLOCKED
    assert p["last_reason_code"] == A.ReasonCode.POLICY_DISABLED


def test_F1_malformed_stored_policy_fails_closed(engine, db_path):
    p = engine.create_proposal(kind=ProposalKind.TASK_CREATION, project="AICC", title="t",
                               rationale="r", policy=_stored_dispatch(), evidence=_ev())
    # Corrupt the persisted policy JSON directly; it must resolve to deny-by-default.
    runtime_db.update_proposal(db_path, p["id"], expected_version=engine.get(p["id"])["version"],
                               fields={"policy_json": "not-json{{"})
    p = engine.assess(p["id"])
    assert p["state"] == ProposalState.BLOCKED


def test_F1_assessment_persists_effective_policy(engine):
    p = engine.create_proposal(kind=ProposalKind.TASK_CREATION, project="AICC", title="t",
                               rationale="r", policy=_stored_dispatch(), evidence=_ev())
    # Assess with a runtime policy that forbids dispatch -> effective forbids it,
    # and that effective policy is what gets persisted, so a later dispatch (no
    # caller policy) is refused.
    p = engine.assess(p["id"], policy=AutonomyPolicy(enabled=True,
                                                     allowed_kinds=frozenset({ProposalKind.TASK_CREATION})))
    stored = AutonomyPolicy.from_json(engine.get(p["id"])["policy_json"])
    assert stored.allow_execution_dispatch is False
    p = engine.approve(p["id"], actor="h")
    with pytest.raises(DispatchNotPermittedError):
        engine.dispatch(p["id"], actor="h")


# ==========================================================================
# F2 — atomic creation
# ==========================================================================


def test_F2_creation_is_atomic_and_complete(engine):
    p = engine.create_proposal(kind=ProposalKind.TASK_CREATION, project="AICC", title="t",
                               rationale="explain", policy=_stored_no_dispatch(), evidence=_ev())
    assert engine.get(p["id"])["evidence_digest"]
    assert len(engine.evidence(p["id"])) == 1
    created = [e for e in engine.events(p["id"]) if e["event_type"] == A.EventType.CREATED]
    assert len(created) == 1 and created[0]["message"] == "explain"


def test_F2_creation_rollback_when_evidence_insert_fails(engine, monkeypatch):
    # Fail AFTER proposal insert but BEFORE evidence -> whole txn rolls back.
    monkeypatch.setattr(runtime_db, "_proposal_evidence_insert",
                        _fail_on(runtime_db._proposal_evidence_insert, 1))
    with pytest.raises(_Boom):
        engine.create_proposal(kind=ProposalKind.TASK_CREATION, project="AICC", title="t",
                               rationale="r", policy=_stored_no_dispatch(), evidence=_ev())
    assert runtime_db.list_proposals(engine.db_path) == []          # nothing persisted
    monkeypatch.undo()
    # Retry succeeds with exactly one proposal + one CREATED event.
    p = engine.create_proposal(kind=ProposalKind.TASK_CREATION, project="AICC", title="t",
                               rationale="r", policy=_stored_no_dispatch(), evidence=_ev())
    assert len(runtime_db.list_proposals(engine.db_path)) == 1
    assert len([e for e in engine.events(p["id"]) if e["event_type"] == A.EventType.CREATED]) == 1
    assert len(engine.evidence(p["id"])) == 1


def test_F2_creation_rollback_when_event_insert_fails(engine, monkeypatch):
    # Fail AFTER evidence but BEFORE the CREATED event -> full rollback.
    monkeypatch.setattr(runtime_db, "_proposal_event_from_spec",
                        _fail_on(runtime_db._proposal_event_from_spec, 1))
    with pytest.raises(_Boom):
        engine.create_proposal(kind=ProposalKind.TASK_CREATION, project="AICC", title="t",
                               rationale="r", policy=_stored_no_dispatch(), evidence=_ev())
    assert runtime_db.list_proposals(engine.db_path) == []
    monkeypatch.undo()
    engine.create_proposal(kind=ProposalKind.TASK_CREATION, project="AICC", title="t",
                           rationale="r", policy=_stored_no_dispatch(), evidence=_ev())
    assert len(runtime_db.list_proposals(engine.db_path)) == 1


def test_F2_creation_duplicate_id_rejected(db_path):
    import sqlite3
    kw = dict(kind="TASK_CREATION", project="AICC", title="t", rationale="r",
              state="DRAFT", risk_level="LOW", proposal_id="fixed-id")
    runtime_db.create_proposal_atomic(db_path, **kw)
    with pytest.raises(sqlite3.IntegrityError):
        runtime_db.create_proposal_atomic(db_path, **kw)


# ==========================================================================
# F2 — atomic / idempotent assessment
# ==========================================================================


def test_F2_assessment_single_assessed_event_even_on_reassess(engine):
    p = engine.create_proposal(kind=ProposalKind.TASK_CREATION, project="AICC", title="t",
                               rationale="r", policy=_stored_no_dispatch(), evidence=_ev())
    engine.assess(p["id"])
    engine.assess(p["id"])  # no-op: already assessed
    assessed = [e for e in engine.events(p["id"]) if e["event_type"] == A.EventType.ASSESSED]
    assert len(assessed) == 1


def test_F2_assessment_rollback_before_persistence(engine, monkeypatch):
    p = engine.create_proposal(kind=ProposalKind.TASK_CREATION, project="AICC", title="t",
                               rationale="r", policy=_stored_no_dispatch(), evidence=_ev())
    # Fail on the verdict UPDATE (first _proposal_update) -> nothing persists.
    monkeypatch.setattr(runtime_db, "_proposal_update", _fail_on(runtime_db._proposal_update, 1))
    with pytest.raises(_Boom):
        engine.assess(p["id"])
    row = engine.get(p["id"])
    assert row["state"] == ProposalState.DRAFT and row["version"] == 0
    assert row["eligibility_json"] is None
    assert [e for e in engine.events(p["id"]) if e["event_type"] == A.EventType.ASSESSED] == []
    monkeypatch.undo()
    p = engine.assess(p["id"])   # retry
    assert p["state"] == ProposalState.AWAITING_APPROVAL
    assert len([e for e in engine.events(p["id"]) if e["event_type"] == A.EventType.ASSESSED]) == 1


def test_F2_assessment_rollback_after_verdict_before_event(engine, monkeypatch):
    p = engine.create_proposal(kind=ProposalKind.TASK_CREATION, project="AICC", title="t",
                               rationale="r", policy=_stored_no_dispatch(), evidence=_ev())
    # Fail on the ASSESSED event (first event in the atomic assessment) -> the
    # already-applied verdict UPDATE rolls back too.
    monkeypatch.setattr(runtime_db, "_proposal_event_from_spec",
                        _fail_on(runtime_db._proposal_event_from_spec, 1))
    with pytest.raises(_Boom):
        engine.assess(p["id"])
    row = engine.get(p["id"])
    assert row["state"] == ProposalState.DRAFT and row["eligibility_json"] is None
    monkeypatch.undo()
    p = engine.assess(p["id"])
    assert len([e for e in engine.events(p["id"]) if e["event_type"] == A.EventType.ASSESSED]) == 1


def test_F2_assessment_rollback_after_event_during_transition(engine, monkeypatch):
    p = engine.create_proposal(kind=ProposalKind.TASK_CREATION, project="AICC", title="t",
                               rationale="r", policy=_stored_no_dispatch(), evidence=_ev())
    # Fail on the 2nd event (a transition event, after the ASSESSED event) ->
    # the entire assessment (verdict + ASSESSED + prior transitions) rolls back.
    monkeypatch.setattr(runtime_db, "_proposal_event_from_spec",
                        _fail_on(runtime_db._proposal_event_from_spec, 2))
    with pytest.raises(_Boom):
        engine.assess(p["id"])
    row = engine.get(p["id"])
    assert row["state"] == ProposalState.DRAFT and row["version"] == 0
    assert [e for e in engine.events(p["id"]) if e["event_type"] == A.EventType.ASSESSED] == []
    monkeypatch.undo()
    p = engine.assess(p["id"])
    assert p["state"] == ProposalState.AWAITING_APPROVAL
    assert len([e for e in engine.events(p["id"]) if e["event_type"] == A.EventType.ASSESSED]) == 1


# ==========================================================================
# F2 — CAS concurrency (deterministic, real transactions, no sleeps)
# ==========================================================================


def _draft(db_path):
    return runtime_db.create_proposal_atomic(
        db_path, kind="TASK_CREATION", project="AICC", title="t", rationale="r",
        state="DRAFT", risk_level="LOW", evidence=[{"kind": "g", "source": "s", "summary": "x",
                                                    "observed_at": "2026-07-23T12:00:00"}],
        created_event={"event_type": "created", "to_state": "DRAFT"},
    )


def test_F2_concurrent_assessment_one_wins(db_path):
    p = _draft(db_path)
    vf = {"eligibility_json": json.dumps({"decision": "ELIGIBLE"}), "risk_level": "LOW"}
    ae = {"from_state": "DRAFT", "to_state": "DRAFT"}
    tr = [{"new_state": "PROPOSED",
           "event": {"event_type": "transition", "from_state": "DRAFT", "to_state": "PROPOSED"}}]
    # Both "assessors" hold expected_version=0.
    r1 = runtime_db.apply_assessment_atomic(db_path, p["id"], expected_version=0,
                                            verdict_fields=vf, assessed_event=ae, transitions=tr)
    assert r1["state"] == "PROPOSED"
    with pytest.raises(runtime_db.LostUpdateError):
        runtime_db.apply_assessment_atomic(db_path, p["id"], expected_version=0,
                                           verdict_fields=vf, assessed_event=ae, transitions=tr)
    # Loser wrote nothing: exactly one ASSESSED event, monotonic seq.
    events = runtime_db.list_proposal_events(db_path, p["id"])
    assert len([e for e in events if e["event_type"] == "assessed"]) == 1
    seqs = [e["seq"] for e in events]
    assert seqs == list(range(1, len(seqs) + 1))


def test_F2_concurrent_transition_one_wins(db_path):
    p = _draft(db_path)
    ev = {"event_type": "transition", "from_state": "DRAFT", "to_state": "PROPOSED"}
    runtime_db.transition_proposal_atomic(db_path, p["id"], expected_version=0,
                                          new_state="PROPOSED", event=ev)
    with pytest.raises(runtime_db.LostUpdateError):
        runtime_db.transition_proposal_atomic(db_path, p["id"], expected_version=0,
                                              new_state="PROPOSED", event=ev)
    transitions = [e for e in runtime_db.list_proposal_events(db_path, p["id"])
                   if e["event_type"] == "transition"]
    assert len(transitions) == 1


# ==========================================================================
# F4 — future-dated evidence is stale (adjacent nit)
# ==========================================================================


def test_F4_future_dated_evidence_is_blocked(engine):
    pol = AutonomyPolicy(enabled=True, allowed_kinds=frozenset({ProposalKind.TASK_CREATION}),
                         auto_approve_max_risk=RiskLevel.LOW)
    p = engine.create_proposal(kind=ProposalKind.TASK_CREATION, project="AICC", title="t",
                               rationale="r", policy=pol,
                               evidence=[observe("g", "s", "x", observed_at="2099-01-01T00:00:00")])
    p = engine.assess(p["id"])
    assert p["state"] == ProposalState.BLOCKED
    assert p["last_reason_code"] == A.ReasonCode.EVIDENCE_STALE
