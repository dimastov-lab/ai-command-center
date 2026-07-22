"""The `ExecutionCenterAPI` autonomy-proposal facade wires through to the engine.

Thin passthrough coverage: the API is the application-facing surface, so this
checks the create -> assess -> approve -> dispatch -> confirm path plus the
read projections resolve against the same DB the rest of the API uses."""

from __future__ import annotations

import pytest

from command_center.runtime import db as runtime_db
from command_center.runtime.api import ExecutionCenterAPI
from command_center.runtime.autonomy import AutonomyPolicy, ProposalKind, ProposalState, RiskLevel
from command_center.runtime.autonomy_service import DispatchNotPermittedError, observe


@pytest.fixture
def api():
    return ExecutionCenterAPI(runtime_db.resolve_db_path())


def _dispatch_policy():
    return AutonomyPolicy(enabled=True, allowed_kinds=frozenset({ProposalKind.TASK_CREATION}),
                          allow_execution_dispatch=True)


def test_api_end_to_end_proposal_flow(api):
    p = api.create_proposal(
        kind=ProposalKind.TASK_CREATION, project="AICC", title="Add tests",
        rationale="Coverage gap in module X", policy=_dispatch_policy(),
        evidence=[observe("task_gap", "project_intelligence", "no tests", data={"module": "X"})],
    )
    p = api.assess_proposal(p["id"])
    assert p["state"] == ProposalState.AWAITING_APPROVAL

    plan = api.plan_proposal(p["id"])
    assert plan["dispatch_route"] == "db.create_task"

    p = api.approve_proposal(p["id"], actor="dima@me.com", reason="ok")
    assert p["state"] == ProposalState.APPROVED

    result = api.dispatch_proposal(p["id"], actor="dima@me.com")
    assert result["proposal"]["state"] == ProposalState.DISPATCHED

    task = runtime_db.create_task(api.db_path, project="AICC", title="Add tests",
                                  task_type="implementation")
    p = api.confirm_proposal_execution(p["id"], actor="executor", task_id=task["id"])
    assert p["state"] == ProposalState.EXECUTED

    assert [r["id"] for r in api.list_proposals(project="AICC")] == [p["id"]]
    assert len(api.get_proposal_evidence(p["id"])) == 1
    assert api.get_proposal_events(p["id"])[0]["event_type"] == "created"


def test_api_dispatch_gate_enforced(api):
    # Default policy: no execution dispatch even once approved.
    p = api.create_proposal(
        kind=ProposalKind.TASK_CREATION, project="AICC", title="x", rationale="gap",
        policy=AutonomyPolicy(enabled=True, allowed_kinds=frozenset({ProposalKind.TASK_CREATION})),
        evidence=[observe("task_gap", "pi", "gap")],
    )
    p = api.assess_proposal(p["id"])
    p = api.approve_proposal(p["id"], actor="dima@me.com")
    with pytest.raises(DispatchNotPermittedError):
        api.dispatch_proposal(p["id"], actor="dima@me.com")
    assert api.get_proposal(p["id"])["state"] == ProposalState.APPROVED


def test_api_reports_risk_for_merge(api):
    p = api.create_proposal(
        kind=ProposalKind.MERGE, project="AICC", title="merge", rationale="ready",
        policy=AutonomyPolicy(enabled=True, allowed_kinds={ProposalKind.MERGE}),
        evidence=[observe("pr_state", "github", "open")],
    )
    p = api.assess_proposal(p["id"])
    assert p["risk_level"] == RiskLevel.CRITICAL
    assert p["state"] == ProposalState.AWAITING_APPROVAL
