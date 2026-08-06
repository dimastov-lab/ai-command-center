"""Tests for command_center.alert_store."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from command_center.alert_store import (
    AlertStoreError,
    InvalidTransition,
    LostUpdate,
    OutcomeRequired,
    PermissionDenied,
    archive_alert,
    assign_alert,
    attach_evidence,
    create_alert,
    dispose_alert,
    escalate_alert,
    find_duplicates,
    get_alert,
    get_audit_log,
    init_db,
    list_alerts,
    mark_duplicate,
    mark_overdue,
    start_triage,
    submit_for_review,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "test_alerts.db"
    init_db(p)
    return p


def _make_alert(db: Path, **kwargs) -> dict:
    defaults = dict(
        source="test_source",
        source_ref=None,
        subject_id="cust-001",
        subject_type="customer",
        trigger_desc="Suspicious activity detected",
        priority="medium",
        priority_rationale=None,
        due_at=None,
        actor="Analyst",
    )
    defaults.update(kwargs)
    return create_alert(db, **defaults)


# ---------------------------------------------------------------------------
# create_alert
# ---------------------------------------------------------------------------


def test_create_alert_happy_path(db: Path) -> None:
    alert = _make_alert(db)
    assert alert["state"] == "generated"
    assert alert["source"] == "test_source"
    assert alert["subject_id"] == "cust-001"
    assert alert["priority"] == "medium"
    assert alert["outcome"] is None
    assert alert["overdue"] == 0


def test_create_alert_invalid_subject_type(db: Path) -> None:
    with pytest.raises(InvalidTransition, match="subject_type"):
        _make_alert(db, subject_type="unknown")


def test_create_alert_invalid_priority(db: Path) -> None:
    with pytest.raises(InvalidTransition, match="priority"):
        _make_alert(db, priority="urgent")


def test_create_alert_writes_audit_log(db: Path) -> None:
    alert = _make_alert(db)
    log = get_audit_log(db, alert["id"])
    assert len(log) == 1
    assert log[0]["action"] == "create"
    assert log[0]["new_state"] == "generated"


# ---------------------------------------------------------------------------
# Happy-path lifecycle: generated → assigned → in_triage → pending_review → closed
# ---------------------------------------------------------------------------


def test_full_lifecycle_false_positive(db: Path) -> None:
    alert = _make_alert(db)
    aid = alert["id"]

    alert = assign_alert(db, aid, owner="Analyst", actor="Analyst")
    assert alert["state"] == "assigned"
    assert alert["owner"] == "Analyst"

    alert = start_triage(db, aid, actor="Analyst")
    assert alert["state"] == "in_triage"

    alert = submit_for_review(db, aid, actor="Analyst", rationale="Reviewed thoroughly")
    assert alert["state"] == "pending_review"

    alert = dispose_alert(db, aid, actor="Analyst", outcome="false_positive", rationale="No risk found")
    assert alert["state"] == "closed"
    assert alert["outcome"] == "false_positive"
    assert alert["disposition_rationale"] == "No risk found"

    log = get_audit_log(db, aid)
    actions = [e["action"] for e in log]
    assert actions == ["create", "assign", "start_triage", "submit_for_review", "dispose"]


# ---------------------------------------------------------------------------
# Escalation path
# ---------------------------------------------------------------------------


def test_escalation_path(db: Path) -> None:
    alert = _make_alert(db)
    aid = alert["id"]

    assign_alert(db, aid, owner="Analyst", actor="Analyst")
    start_triage(db, aid, actor="Analyst")
    submit_for_review(db, aid, actor="Analyst", rationale="Needs review")

    alert = escalate_alert(db, aid, actor="SeniorAnalyst", case_id="case-999", rationale="Material")
    assert alert["state"] == "escalated"
    assert alert["case_id"] == "case-999"
    assert alert["outcome"] == "escalated_to_case"


# ---------------------------------------------------------------------------
# dispose_alert validations
# ---------------------------------------------------------------------------


def test_dispose_requires_outcome(db: Path) -> None:
    alert = _make_alert(db)
    aid = alert["id"]
    assign_alert(db, aid, owner="Analyst", actor="Analyst")
    start_triage(db, aid, actor="Analyst")
    submit_for_review(db, aid, actor="Analyst", rationale="ok")

    with pytest.raises(OutcomeRequired):
        dispose_alert(db, aid, actor="Analyst", outcome="", rationale="x")


def test_dispose_rejects_escalated_to_case_outcome(db: Path) -> None:
    alert = _make_alert(db)
    aid = alert["id"]
    assign_alert(db, aid, owner="Analyst", actor="Analyst")
    start_triage(db, aid, actor="Analyst")
    submit_for_review(db, aid, actor="Analyst", rationale="ok")

    with pytest.raises(InvalidTransition, match="escalate_alert"):
        dispose_alert(db, aid, actor="Analyst", outcome="escalated_to_case", rationale="x")


# ---------------------------------------------------------------------------
# assign_alert guard
# ---------------------------------------------------------------------------


def test_assign_alert_requires_generated_state(db: Path) -> None:
    alert = _make_alert(db)
    aid = alert["id"]
    assign_alert(db, aid, owner="Analyst", actor="Analyst")

    with pytest.raises(LostUpdate):
        assign_alert(db, aid, owner="Analyst", actor="Analyst")


# ---------------------------------------------------------------------------
# LostUpdate (optimistic concurrency)
# ---------------------------------------------------------------------------


def test_lost_update_raised_on_wrong_expected_state(db: Path) -> None:
    alert = _make_alert(db)
    aid = alert["id"]

    with pytest.raises(LostUpdate):
        start_triage(db, aid, actor="Analyst", expected_state="assigned")


# ---------------------------------------------------------------------------
# mark_overdue
# ---------------------------------------------------------------------------


def test_mark_overdue_marks_past_due(db: Path) -> None:
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    alert = _make_alert(db, due_at=past)
    count = mark_overdue(db)
    assert count == 1
    updated = get_alert(db, alert["id"])
    assert updated["overdue"] == 1


def test_mark_overdue_ignores_future(db: Path) -> None:
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    _make_alert(db, due_at=future)
    count = mark_overdue(db)
    assert count == 0


def test_mark_overdue_ignores_closed(db: Path) -> None:
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    alert = _make_alert(db, due_at=past)
    aid = alert["id"]
    assign_alert(db, aid, owner="Analyst", actor="Analyst")
    start_triage(db, aid, actor="Analyst")
    submit_for_review(db, aid, actor="Analyst", rationale="ok")
    dispose_alert(db, aid, actor="Analyst", outcome="false_positive", rationale="x")

    count = mark_overdue(db)
    assert count == 0


# ---------------------------------------------------------------------------
# attach_evidence
# ---------------------------------------------------------------------------


def test_attach_evidence_happy_path(db: Path) -> None:
    alert = _make_alert(db)
    attach_evidence(db, alert["id"], evidence_ref="doc-001", actor="Analyst")


def test_attach_evidence_raises_on_closed(db: Path) -> None:
    alert = _make_alert(db)
    aid = alert["id"]
    assign_alert(db, aid, owner="Analyst", actor="Analyst")
    start_triage(db, aid, actor="Analyst")
    submit_for_review(db, aid, actor="Analyst", rationale="ok")
    dispose_alert(db, aid, actor="Analyst", outcome="false_positive", rationale="x")

    with pytest.raises(AlertStoreError, match="closed/archived"):
        attach_evidence(db, aid, evidence_ref="doc-late", actor="Analyst")


# ---------------------------------------------------------------------------
# get_audit_log ordering
# ---------------------------------------------------------------------------


def test_audit_log_ordered_asc(db: Path) -> None:
    alert = _make_alert(db)
    aid = alert["id"]
    assign_alert(db, aid, owner="Analyst", actor="Analyst")
    start_triage(db, aid, actor="Analyst")

    log = get_audit_log(db, aid)
    assert len(log) == 3
    assert log[0]["action"] == "create"
    assert log[1]["action"] == "assign"
    assert log[2]["action"] == "start_triage"


# ---------------------------------------------------------------------------
# archive_alert permission guard
# ---------------------------------------------------------------------------


def test_archive_requires_compliance_officer(db: Path) -> None:
    alert = _make_alert(db)
    aid = alert["id"]
    assign_alert(db, aid, owner="Analyst", actor="Analyst")
    start_triage(db, aid, actor="Analyst")
    submit_for_review(db, aid, actor="Analyst", rationale="ok")
    dispose_alert(db, aid, actor="Analyst", outcome="false_positive", rationale="x")

    with pytest.raises(PermissionDenied):
        archive_alert(db, aid, actor="Analyst", rationale="archive")

    archived = archive_alert(db, aid, actor="ComplianceOfficer", rationale="archive")
    assert archived["state"] == "archived"


# ---------------------------------------------------------------------------
# list_alerts filters
# ---------------------------------------------------------------------------


def test_list_alerts_filter_by_state(db: Path) -> None:
    a1 = _make_alert(db, subject_id="cust-001")
    a2 = _make_alert(db, subject_id="cust-002")
    assign_alert(db, a1["id"], owner="Analyst", actor="Analyst")

    results = list_alerts(db, state="assigned")
    assert len(results) == 1
    assert results[0]["id"] == a1["id"]

    results = list_alerts(db, state="generated")
    assert len(results) == 1
    assert results[0]["id"] == a2["id"]


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def test_find_duplicates_returns_match(db: Path) -> None:
    a1 = _make_alert(db, subject_id="cust-dup", trigger_desc="Suspicious transfer pattern")
    matches = find_duplicates(db, subject_id="cust-dup", trigger_desc="Suspicious transfer pattern")
    assert len(matches) == 1
    assert matches[0]["id"] == a1["id"]


def test_find_duplicates_case_insensitive(db: Path) -> None:
    _make_alert(db, subject_id="cust-dup", trigger_desc="SUSPICIOUS TRANSFER PATTERN")
    matches = find_duplicates(db, subject_id="cust-dup", trigger_desc="suspicious transfer pattern")
    assert len(matches) == 1


def test_find_duplicates_ignores_closed(db: Path) -> None:
    alert = _make_alert(db, subject_id="cust-dup", trigger_desc="Suspicious transfer pattern")
    aid = alert["id"]
    assign_alert(db, aid, owner="Analyst", actor="Analyst")
    start_triage(db, aid, actor="Analyst")
    submit_for_review(db, aid, actor="Analyst", rationale="ok")
    dispose_alert(db, aid, actor="Analyst", outcome="false_positive", rationale="x")

    matches = find_duplicates(db, subject_id="cust-dup", trigger_desc="Suspicious transfer pattern")
    assert matches == []


def test_find_duplicates_empty_outside_window(db: Path) -> None:
    _make_alert(db, subject_id="cust-old", trigger_desc="Old pattern")
    matches = find_duplicates(
        db, subject_id="cust-old", trigger_desc="Old pattern", window_days=0
    )
    assert matches == []


def test_mark_duplicate_sets_field_and_audit(db: Path) -> None:
    a1 = _make_alert(db, subject_id="cust-x", trigger_desc="Pattern A")
    a2 = _make_alert(db, subject_id="cust-x", trigger_desc="Pattern A")

    mark_duplicate(db, a2["id"], duplicate_of=a1["id"], actor="Analyst")

    updated = get_alert(db, a2["id"])
    assert updated["duplicate_of"] == a1["id"]

    log = get_audit_log(db, a2["id"])
    actions = [e["action"] for e in log]
    assert "mark_duplicate" in actions
