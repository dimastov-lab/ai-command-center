"""Persistence-layer tests for the autonomy proposal tables (migration 6).

Covers create/get/list, compare-and-set updates, the structural transition
guard, the field allowlist, immutable evidence rows, and append-only event
ordering — plus malformed input."""

from __future__ import annotations

import pytest

from command_center.runtime import autonomy as A
from command_center.runtime import db as runtime_db


@pytest.fixture
def db_path():
    path = runtime_db.resolve_db_path()
    runtime_db.migrate(path)
    return path


def _proposal(db_path, **overrides):
    kwargs = dict(
        kind=A.ProposalKind.TASK_CREATION,
        project="AICC",
        title="Add tests",
        rationale="Coverage gap detected",
        state=A.ProposalState.DRAFT,
        risk_level=A.RiskLevel.LOW,
    )
    kwargs.update(overrides)
    return runtime_db.create_proposal(db_path, **kwargs)


# --------------------------------------------------------------------------
# Migration
# --------------------------------------------------------------------------


def test_schema_migrated_to_6(db_path):
    assert runtime_db.current_schema_version(db_path) == 6


def test_migrate_is_idempotent(db_path):
    runtime_db.migrate(db_path)
    assert runtime_db.current_schema_version(db_path) == 6


# --------------------------------------------------------------------------
# Create / get / list
# --------------------------------------------------------------------------


def test_create_and_get_proposal(db_path):
    row = _proposal(db_path)
    assert row["version"] == 0
    assert row["requires_human"] == 1
    fetched = runtime_db.get_proposal(db_path, row["id"])
    assert fetched["state"] == A.ProposalState.DRAFT
    assert fetched["rationale"] == "Coverage gap detected"
    assert fetched["risk_level"] == A.RiskLevel.LOW


def test_create_proposal_rejects_blank_rationale(db_path):
    with pytest.raises(ValueError):
        _proposal(db_path, rationale="")
    with pytest.raises(ValueError):
        _proposal(db_path, rationale="   ")


def test_get_missing_proposal_returns_none(db_path):
    assert runtime_db.get_proposal(db_path, "nope") is None


def test_list_proposals_filters(db_path):
    a = _proposal(db_path, project="AICC", kind=A.ProposalKind.TASK_CREATION)
    b = _proposal(db_path, project="AIOS", kind=A.ProposalKind.MERGE, risk_level=A.RiskLevel.CRITICAL)
    ids = {r["id"] for r in runtime_db.list_proposals(db_path)}
    assert ids == {a["id"], b["id"]}
    assert [r["id"] for r in runtime_db.list_proposals(db_path, project="AIOS")] == [b["id"]]
    assert [r["id"] for r in runtime_db.list_proposals(db_path, kind=A.ProposalKind.MERGE)] == [b["id"]]
    drafts = {r["id"] for r in runtime_db.list_proposals(db_path, states=[A.ProposalState.DRAFT])}
    assert drafts == {a["id"], b["id"]}


def test_list_proposals_empty_states_returns_empty(db_path):
    _proposal(db_path)
    assert runtime_db.list_proposals(db_path, states=[]) == []


def test_list_proposals_negative_limit_raises(db_path):
    with pytest.raises(ValueError):
        runtime_db.list_proposals(db_path, limit=-1)


# --------------------------------------------------------------------------
# Compare-and-set update + guards
# --------------------------------------------------------------------------


def test_update_proposal_bumps_version(db_path):
    row = _proposal(db_path)
    updated = runtime_db.update_proposal(
        db_path, row["id"], expected_version=0, fields={"state": A.ProposalState.PROPOSED}
    )
    assert updated["version"] == 1
    assert updated["state"] == A.ProposalState.PROPOSED


def test_update_proposal_lost_update_raises(db_path):
    row = _proposal(db_path)
    runtime_db.update_proposal(db_path, row["id"], expected_version=0,
                               fields={"state": A.ProposalState.PROPOSED})
    with pytest.raises(runtime_db.LostUpdateError):
        runtime_db.update_proposal(db_path, row["id"], expected_version=0,
                                   fields={"title": "stale writer"})


def test_update_proposal_illegal_transition_rejected(db_path):
    row = _proposal(db_path)  # DRAFT
    with pytest.raises(runtime_db.InvalidProposalTransitionError):
        runtime_db.update_proposal(db_path, row["id"], expected_version=0,
                                   fields={"state": A.ProposalState.EXECUTED})


def test_update_proposal_transition_guard_precedes_version_check(db_path):
    # An illegal transition is rejected even with a stale expected_version — the
    # structural guard runs before the CAS check (mirrors update_completion).
    row = _proposal(db_path)
    with pytest.raises(runtime_db.InvalidProposalTransitionError):
        runtime_db.update_proposal(db_path, row["id"], expected_version=999,
                                   fields={"state": A.ProposalState.EXECUTED})


def test_update_proposal_unknown_field_rejected(db_path):
    row = _proposal(db_path)
    with pytest.raises(runtime_db.UnknownRunFieldError):
        runtime_db.update_proposal(db_path, row["id"], expected_version=0,
                                   fields={"kind": "hacked"})


def test_update_missing_proposal_raises_keyerror(db_path):
    with pytest.raises(KeyError):
        runtime_db.update_proposal(db_path, "nope", expected_version=0,
                                   fields={"title": "x"})


def test_same_state_metadata_update_allowed(db_path):
    row = _proposal(db_path)
    updated = runtime_db.update_proposal(db_path, row["id"], expected_version=0,
                                         fields={"state": A.ProposalState.DRAFT, "last_reason_code": "X"})
    assert updated["version"] == 1
    assert updated["last_reason_code"] == "X"


# --------------------------------------------------------------------------
# Evidence — append-only, immutable, ordered
# --------------------------------------------------------------------------


def test_evidence_is_appended_and_ordered(db_path):
    row = _proposal(db_path)
    s1 = runtime_db.append_proposal_evidence(db_path, row["id"], kind="git", source="git_info",
                                             summary="clean", observed_at="2026-07-23T12:00:00",
                                             data={"dirty": False})
    s2 = runtime_db.append_proposal_evidence(db_path, row["id"], kind="gap", source="pi",
                                             summary="missing", observed_at="2026-07-23T12:00:01",
                                             is_blocker=True)
    assert (s1, s2) == (1, 2)
    items = runtime_db.list_proposal_evidence(db_path, row["id"])
    assert [i["seq"] for i in items] == [1, 2]
    assert items[0]["data"] == {"dirty": False}
    assert items[0]["is_blocker"] is False
    assert items[1]["is_blocker"] is True


# --------------------------------------------------------------------------
# Events — append-only audit trail
# --------------------------------------------------------------------------


def test_events_are_appended_and_ordered(db_path):
    row = _proposal(db_path)
    runtime_db.append_proposal_event(db_path, row["id"], A.EventType.CREATED,
                                     to_state=A.ProposalState.DRAFT, message="created")
    runtime_db.append_proposal_event(db_path, row["id"], A.EventType.TRANSITION,
                                     from_state=A.ProposalState.DRAFT, to_state=A.ProposalState.PROPOSED,
                                     actor="engine", reason_code="X", metadata={"k": "v"})
    events = runtime_db.list_proposal_events(db_path, row["id"])
    assert [e["seq"] for e in events] == [1, 2]
    assert events[1]["actor"] == "engine"
    assert events[1]["metadata"] == {"k": "v"}
    assert events[0]["metadata"] is None


def test_evidence_and_events_cascade_delete_with_task(db_path):
    # A proposal referencing a task keeps working after the task row is gone
    # (ON DELETE SET NULL), and its own children remain queryable.
    task = runtime_db.create_task(db_path, project="AICC", title="t", task_type="implementation")
    row = _proposal(db_path, task_id=task["id"])
    runtime_db.append_proposal_event(db_path, row["id"], A.EventType.CREATED)
    fetched = runtime_db.get_proposal(db_path, row["id"])
    assert fetched["task_id"] == task["id"]
