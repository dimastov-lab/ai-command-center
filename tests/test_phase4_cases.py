"""Tests for Phase 4: case_store — case management and alert linking."""

from __future__ import annotations

from pathlib import Path

import pytest

from command_center import alert_store, case_store
from command_center.case_store import (
    CaseNotFound,
    InvalidTransition,
    LostUpdate,
    MissingRequiredField,
    PermissionDenied,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def c_db(tmp_path: Path) -> Path:
    p = tmp_path / "cases.db"
    case_store.init_db(p)
    return p


@pytest.fixture()
def a_db(tmp_path: Path) -> Path:
    p = tmp_path / "alerts.db"
    alert_store.init_db(p)
    return p


def _make_case(c_db: Path, **overrides) -> dict:
    defaults = dict(title="Test Case", priority="medium", created_by="ComplianceOfficer")
    defaults.update(overrides)
    return case_store.create_case(c_db, **defaults)


def _make_alert(a_db: Path) -> dict:
    return alert_store.create_alert(
        a_db,
        source="manual",
        source_ref="test-ref",
        subject_id="cust-1",
        subject_type="customer",
        trigger_desc="Test alert",
        priority="high",
        priority_rationale="Phase 4 test",
        due_at=None,
        actor="Analyst",
    )


# ---------------------------------------------------------------------------
# Case CRUD
# ---------------------------------------------------------------------------


def test_create_case_happy(c_db: Path) -> None:
    c = _make_case(c_db)
    assert c["state"] == "open"
    assert c["case_number"].startswith("AML-")
    assert c["priority"] == "medium"


def test_create_case_assigns_sequential_number(c_db: Path) -> None:
    c1 = _make_case(c_db, title="A")
    c2 = _make_case(c_db, title="B")
    assert c1["case_number"] == "AML-00001"
    assert c2["case_number"] == "AML-00002"


def test_create_case_blank_title_raises(c_db: Path) -> None:
    with pytest.raises(MissingRequiredField):
        _make_case(c_db, title="   ")


def test_create_case_invalid_priority_raises(c_db: Path) -> None:
    with pytest.raises(case_store.CaseStoreError):
        _make_case(c_db, priority="extreme")


def test_get_case_not_found(c_db: Path) -> None:
    with pytest.raises(CaseNotFound):
        case_store.get_case(c_db, "nonexistent")


def test_get_case_by_number(c_db: Path) -> None:
    c = _make_case(c_db)
    fetched = case_store.get_case_by_number(c_db, c["case_number"])
    assert fetched["id"] == c["id"]


def test_list_cases_state_filter(c_db: Path) -> None:
    c = _make_case(c_db)
    case_store.start_investigation(c_db, c["id"], actor="Analyst")
    open_cases = case_store.list_cases(c_db, state="open")
    inv_cases = case_store.list_cases(c_db, state="under_investigation")
    assert len(open_cases) == 0
    assert len(inv_cases) == 1


def test_list_cases_priority_filter(c_db: Path) -> None:
    _make_case(c_db, title="Low", priority="low")
    _make_case(c_db, title="High", priority="high")
    assert len(case_store.list_cases(c_db, priority="low")) == 1
    assert len(case_store.list_cases(c_db, priority="high")) == 1


def test_list_cases_assigned_filter(c_db: Path) -> None:
    c = _make_case(c_db)
    case_store.assign_investigator(c_db, c["id"], assigned_to="Alice", actor="Manager")
    alice_cases = case_store.list_cases(c_db, assigned_to="Alice")
    assert len(alice_cases) == 1


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


def test_full_happy_path_to_close(c_db: Path) -> None:
    c = _make_case(c_db)
    c = case_store.start_investigation(c_db, c["id"], actor="Analyst")
    assert c["state"] == "under_investigation"
    c = case_store.submit_for_review(c_db, c["id"], actor="Analyst")
    assert c["state"] == "pending_review"
    c = case_store.close_case(c_db, c["id"], actor="ComplianceOfficer",
                               closure_reason="No suspicious activity found.")
    assert c["state"] == "closed"
    assert c["closure_reason"] == "No suspicious activity found."
    assert c["closed_at"] is not None


def test_full_happy_path_to_sar(c_db: Path) -> None:
    c = _make_case(c_db)
    case_store.start_investigation(c_db, c["id"], actor="Analyst")
    case_store.submit_for_review(c_db, c["id"], actor="Analyst")
    c = case_store.escalate_to_sar(c_db, c["id"], actor="MLRO", sar_ref="SAR-2026-001")
    assert c["state"] == "escalated_to_sar"
    assert c["sar_ref"] == "SAR-2026-001"


def test_invalid_transition_from_open_to_closed(c_db: Path) -> None:
    c = _make_case(c_db)
    with pytest.raises(InvalidTransition):
        case_store.close_case(c_db, c["id"], actor="ComplianceOfficer",
                               closure_reason="Skipping steps.")


def test_invalid_transition_reopen_closed(c_db: Path) -> None:
    c = _make_case(c_db)
    case_store.start_investigation(c_db, c["id"], actor="Analyst")
    case_store.submit_for_review(c_db, c["id"], actor="Analyst")
    case_store.close_case(c_db, c["id"], actor="ComplianceOfficer",
                           closure_reason="Done.")
    with pytest.raises(InvalidTransition):
        case_store.start_investigation(c_db, c["id"], actor="Analyst")


def test_close_requires_permission(c_db: Path) -> None:
    c = _make_case(c_db)
    case_store.start_investigation(c_db, c["id"], actor="Analyst")
    case_store.submit_for_review(c_db, c["id"], actor="Analyst")
    with pytest.raises(PermissionDenied):
        case_store.close_case(c_db, c["id"], actor="Analyst",
                               closure_reason="Analyst tries to close.")


def test_escalate_to_sar_requires_permission(c_db: Path) -> None:
    c = _make_case(c_db)
    case_store.start_investigation(c_db, c["id"], actor="Analyst")
    case_store.submit_for_review(c_db, c["id"], actor="Analyst")
    with pytest.raises(PermissionDenied):
        case_store.escalate_to_sar(c_db, c["id"], actor="Analyst", sar_ref="SAR-X")


def test_close_requires_closure_reason(c_db: Path) -> None:
    c = _make_case(c_db)
    case_store.start_investigation(c_db, c["id"], actor="Analyst")
    case_store.submit_for_review(c_db, c["id"], actor="Analyst")
    with pytest.raises(MissingRequiredField):
        case_store.close_case(c_db, c["id"], actor="ComplianceOfficer",
                               closure_reason="  ")


def test_escalate_to_sar_requires_sar_ref(c_db: Path) -> None:
    c = _make_case(c_db)
    case_store.start_investigation(c_db, c["id"], actor="Analyst")
    case_store.submit_for_review(c_db, c["id"], actor="Analyst")
    with pytest.raises(MissingRequiredField):
        case_store.escalate_to_sar(c_db, c["id"], actor="MLRO", sar_ref="")


def test_lost_update_detection(c_db: Path) -> None:
    c = _make_case(c_db)
    # Transition happens concurrently — expected_state is stale
    case_store.start_investigation(c_db, c["id"], actor="Analyst")
    with pytest.raises(LostUpdate):
        case_store.submit_for_review(c_db, c["id"], actor="Analyst",
                                     expected_state="open")


# ---------------------------------------------------------------------------
# Investigator assignment
# ---------------------------------------------------------------------------


def test_assign_investigator(c_db: Path) -> None:
    c = _make_case(c_db)
    assert c["assigned_to"] is None
    updated = case_store.assign_investigator(c_db, c["id"], assigned_to="Bob", actor="Manager")
    assert updated["assigned_to"] == "Bob"


def test_reassign_investigator(c_db: Path) -> None:
    c = _make_case(c_db, assigned_to="Alice")
    case_store.assign_investigator(c_db, c["id"], assigned_to="Bob", actor="Manager")
    fetched = case_store.get_case(c_db, c["id"])
    assert fetched["assigned_to"] == "Bob"


# ---------------------------------------------------------------------------
# Alert linking
# ---------------------------------------------------------------------------


def test_link_alert_to_case(c_db: Path, a_db: Path) -> None:
    c = _make_case(c_db)
    a = _make_alert(a_db)
    case_store.link_alert(c_db, c["id"], a["id"], actor="Analyst")
    links = case_store.get_case_alerts(c_db, c["id"])
    assert len(links) == 1
    assert links[0]["alert_id"] == a["id"]


def test_link_alert_idempotent(c_db: Path, a_db: Path) -> None:
    c = _make_case(c_db)
    a = _make_alert(a_db)
    case_store.link_alert(c_db, c["id"], a["id"], actor="Analyst")
    case_store.link_alert(c_db, c["id"], a["id"], actor="Analyst")  # should not raise
    assert len(case_store.get_case_alerts(c_db, c["id"])) == 1


def test_unlink_alert(c_db: Path, a_db: Path) -> None:
    c = _make_case(c_db)
    a = _make_alert(a_db)
    case_store.link_alert(c_db, c["id"], a["id"], actor="Analyst")
    case_store.unlink_alert(c_db, c["id"], a["id"], actor="Analyst")
    assert case_store.get_case_alerts(c_db, c["id"]) == []


def test_find_cases_for_alert(c_db: Path, a_db: Path) -> None:
    c1 = _make_case(c_db, title="Case 1")
    c2 = _make_case(c_db, title="Case 2")
    a = _make_alert(a_db)
    case_store.link_alert(c_db, c1["id"], a["id"], actor="Analyst")
    case_store.link_alert(c_db, c2["id"], a["id"], actor="Analyst")
    cases = case_store.find_cases_for_alert(c_db, a["id"])
    ids = {c["id"] for c in cases}
    assert c1["id"] in ids
    assert c2["id"] in ids


def test_link_alert_case_not_found(c_db: Path) -> None:
    with pytest.raises(CaseNotFound):
        case_store.link_alert(c_db, "nonexistent", "alert-id", actor="Analyst")


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


def test_audit_log_records_transitions(c_db: Path) -> None:
    c = _make_case(c_db)
    case_store.start_investigation(c_db, c["id"], actor="Analyst")
    case_store.submit_for_review(c_db, c["id"], actor="SeniorAnalyst")
    log = case_store.get_audit_log(c_db, c["id"])
    actions = [e["action"] for e in log]
    # newest first
    assert any("pending_review" in a for a in actions)
    assert any("under_investigation" in a for a in actions)
    assert any("created" in a for a in actions)


def test_audit_log_records_assignment(c_db: Path) -> None:
    c = _make_case(c_db)
    case_store.assign_investigator(c_db, c["id"], assigned_to="Eve", actor="Manager")
    log = case_store.get_audit_log(c_db, c["id"])
    assert any(e["action"] == "assigned" for e in log)


def test_audit_log_records_alert_link(c_db: Path, a_db: Path) -> None:
    c = _make_case(c_db)
    a = _make_alert(a_db)
    case_store.link_alert(c_db, c["id"], a["id"], actor="Analyst")
    log = case_store.get_audit_log(c_db, c["id"])
    assert any(e["action"] == "alert_linked" for e in log)


# ---------------------------------------------------------------------------
# Alert→Case bridge via alert_store.escalate_alert
# ---------------------------------------------------------------------------


def test_alert_escalate_then_link_to_case(c_db: Path, a_db: Path) -> None:
    """Full workflow: escalate alert → create case → link alert → start investigation."""
    a = _make_alert(a_db)
    # Move alert through state machine to escalatable state
    alert_store.assign_alert(a_db, a["id"], actor="Analyst", owner="Analyst")
    alert_store.start_triage(a_db, a["id"], actor="Analyst")
    alert_store.submit_for_review(a_db, a["id"], actor="Analyst", rationale="Ready for review")

    # Create case and escalate the alert with the case_id
    c = _make_case(c_db, title="Investigation of alert")
    alert_store.escalate_alert(a_db, a["id"], actor="ComplianceOfficer",
                               case_id=c["id"], rationale="Linked to AML case")
    case_store.link_alert(c_db, c["id"], a["id"], actor="ComplianceOfficer")

    updated_alert = alert_store.get_alert(a_db, a["id"])
    assert updated_alert["state"] == "escalated"
    assert updated_alert["case_id"] == c["id"]

    links = case_store.get_case_alerts(c_db, c["id"])
    assert links[0]["alert_id"] == a["id"]
