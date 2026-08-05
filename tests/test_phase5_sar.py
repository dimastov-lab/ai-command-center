"""Tests for Phase 5: sar_store — SAR filing workflow."""

from __future__ import annotations

from pathlib import Path

import pytest

from command_center import sar_store
from command_center.sar_store import (
    InvalidTransition,
    LostUpdate,
    MissingRequiredField,
    NarrativeLocked,
    PermissionDenied,
    SarNotFound,
    SarStoreError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def s_db(tmp_path: Path) -> Path:
    p = tmp_path / "sars.db"
    sar_store.init_db(p)
    return p


def _make_sar(s_db: Path, **overrides) -> dict:
    defaults = dict(sar_type="str", created_by="ComplianceOfficer")
    defaults.update(overrides)
    return sar_store.create_sar(s_db, **defaults)


def _make_reviewed_sar(s_db: Path, **overrides) -> dict:
    sar = _make_sar(s_db, narrative="Suspicious activity detected.", **overrides)
    return sar_store.submit_for_review(s_db, sar["id"], actor="Analyst")


def _make_approved_sar(s_db: Path, **overrides) -> dict:
    sar = _make_reviewed_sar(s_db, **overrides)
    return sar_store.approve_sar(s_db, sar["id"], actor="MLRO")


def _make_submitted_sar(s_db: Path, **overrides) -> dict:
    sar = _make_approved_sar(s_db, **overrides)
    return sar_store.submit_to_regulator(
        s_db, sar["id"], actor="ComplianceOfficer", submission_ref="FEU-2026-001"
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def test_create_sar_happy(s_db: Path) -> None:
    sar = _make_sar(s_db)
    assert sar["state"] == "draft"
    assert sar["sar_type"] == "str"
    assert sar["sar_number"].startswith("SAR-")
    assert sar["filing_deadline"] is not None


def test_create_sar_sequential_numbers(s_db: Path) -> None:
    s1 = _make_sar(s_db)
    s2 = _make_sar(s_db)
    assert s1["sar_number"] != s2["sar_number"]
    assert int(s1["sar_number"].split("-")[-1]) < int(s2["sar_number"].split("-")[-1])


def test_create_sar_invalid_type(s_db: Path) -> None:
    with pytest.raises(SarStoreError, match="invalid sar_type"):
        sar_store.create_sar(s_db, sar_type="invalid", created_by="CO")


def test_get_sar_not_found(s_db: Path) -> None:
    with pytest.raises(SarNotFound):
        sar_store.get_sar(s_db, "nonexistent")


def test_get_sar_by_number(s_db: Path) -> None:
    sar = _make_sar(s_db)
    fetched = sar_store.get_sar_by_number(s_db, sar["sar_number"])
    assert fetched["id"] == sar["id"]


def test_list_sars_state_filter(s_db: Path) -> None:
    _make_sar(s_db)
    _make_reviewed_sar(s_db)
    drafts = sar_store.list_sars(s_db, state="draft")
    reviews = sar_store.list_sars(s_db, state="under_review")
    assert len(drafts) == 1
    assert len(reviews) == 1


def test_list_sars_type_filter(s_db: Path) -> None:
    _make_sar(s_db, sar_type="str")
    _make_sar(s_db, sar_type="ctr")
    assert len(sar_store.list_sars(s_db, sar_type="str")) == 1
    assert len(sar_store.list_sars(s_db, sar_type="ctr")) == 1


def test_list_sars_case_id_filter(s_db: Path) -> None:
    _make_sar(s_db, case_id="case-1")
    _make_sar(s_db, case_id="case-2")
    _make_sar(s_db)
    assert len(sar_store.list_sars(s_db, case_id="case-1")) == 1


def test_ctr_deadline_shorter_than_str(s_db: Path) -> None:
    str_sar = _make_sar(s_db, sar_type="str")
    ctr_sar = _make_sar(s_db, sar_type="ctr")
    # CTR deadline (1 day) < STR deadline (3 days)
    assert ctr_sar["filing_deadline"] < str_sar["filing_deadline"]


# ---------------------------------------------------------------------------
# Narrative
# ---------------------------------------------------------------------------


def test_update_narrative_in_draft(s_db: Path) -> None:
    sar = _make_sar(s_db)
    updated = sar_store.update_narrative(
        s_db, sar["id"], narrative="Updated narrative.", actor="Analyst"
    )
    assert updated["narrative"] == "Updated narrative."


def test_update_narrative_in_under_review(s_db: Path) -> None:
    sar = _make_reviewed_sar(s_db)
    updated = sar_store.update_narrative(
        s_db, sar["id"], narrative="Corrected narrative.", actor="Analyst"
    )
    assert updated["narrative"] == "Corrected narrative."


def test_update_narrative_blank_raises(s_db: Path) -> None:
    sar = _make_sar(s_db)
    with pytest.raises(MissingRequiredField):
        sar_store.update_narrative(s_db, sar["id"], narrative="   ", actor="Analyst")


def test_update_narrative_locked_after_approval(s_db: Path) -> None:
    sar = _make_approved_sar(s_db)
    with pytest.raises(NarrativeLocked):
        sar_store.update_narrative(s_db, sar["id"], narrative="Try to edit.", actor="Analyst")


def test_update_narrative_locked_when_submitted(s_db: Path) -> None:
    sar = _make_submitted_sar(s_db)
    with pytest.raises(NarrativeLocked):
        sar_store.update_narrative(s_db, sar["id"], narrative="Try to edit.", actor="Analyst")


# ---------------------------------------------------------------------------
# State machine — happy paths
# ---------------------------------------------------------------------------


def test_full_path_to_acknowledged(s_db: Path) -> None:
    sar = _make_sar(s_db, narrative="Suspicious activity.")
    sar = sar_store.submit_for_review(s_db, sar["id"], actor="Analyst")
    assert sar["state"] == "under_review"
    sar = sar_store.approve_sar(s_db, sar["id"], actor="ComplianceOfficer")
    assert sar["state"] == "approved"
    assert sar["approved_by"] == "ComplianceOfficer"
    sar = sar_store.submit_to_regulator(
        s_db, sar["id"], actor="MLRO", submission_ref="FEU-2026-001"
    )
    assert sar["state"] == "submitted"
    assert sar["submission_ref"] == "FEU-2026-001"
    assert sar["submitted_at"] is not None
    sar = sar_store.acknowledge_receipt(
        s_db, sar["id"], actor="MLRO", acknowledgement_ref="ACK-001"
    )
    assert sar["state"] == "acknowledged"
    assert sar["acknowledgement_ref"] == "ACK-001"
    assert sar["acknowledged_at"] is not None


def test_reject_from_under_review(s_db: Path) -> None:
    sar = _make_reviewed_sar(s_db)
    sar = sar_store.reject_sar(
        s_db, sar["id"], actor="ComplianceOfficer", rejection_reason="Incomplete narrative."
    )
    assert sar["state"] == "rejected"
    assert sar["rejection_reason"] == "Incomplete narrative."


def test_reject_from_submitted(s_db: Path) -> None:
    sar = _make_submitted_sar(s_db)
    sar = sar_store.reject_sar(
        s_db, sar["id"], actor="MLRO", rejection_reason="Missing subject info."
    )
    assert sar["state"] == "rejected"


def test_reopen_rejected_to_draft(s_db: Path) -> None:
    sar = _make_reviewed_sar(s_db)
    sar = sar_store.reject_sar(
        s_db, sar["id"], actor="ComplianceOfficer", rejection_reason="Needs revision."
    )
    sar = sar_store.reopen_to_draft(s_db, sar["id"], actor="Analyst")
    assert sar["state"] == "draft"


def test_reopen_approved_to_draft(s_db: Path) -> None:
    sar = _make_approved_sar(s_db)
    sar = sar_store.reopen_to_draft(s_db, sar["id"], actor="ComplianceOfficer")
    assert sar["state"] == "draft"


# ---------------------------------------------------------------------------
# State machine — invalid transitions
# ---------------------------------------------------------------------------


def test_submit_for_review_without_narrative(s_db: Path) -> None:
    sar = _make_sar(s_db)
    with pytest.raises(MissingRequiredField, match="narrative"):
        sar_store.submit_for_review(s_db, sar["id"], actor="Analyst")


def test_invalid_transition_draft_to_approved(s_db: Path) -> None:
    sar = _make_sar(s_db, narrative="Text.")
    with pytest.raises(InvalidTransition):
        sar_store.approve_sar(s_db, sar["id"], actor="ComplianceOfficer")


def test_cannot_resubmit_acknowledged(s_db: Path) -> None:
    sar = _make_submitted_sar(s_db)
    sar_store.acknowledge_receipt(
        s_db, sar["id"], actor="MLRO", acknowledgement_ref="ACK-001"
    )
    final = sar_store.get_sar(s_db, sar["id"])
    with pytest.raises(InvalidTransition):
        sar_store.submit_for_review(s_db, final["id"], actor="Analyst")


# ---------------------------------------------------------------------------
# Permission guards
# ---------------------------------------------------------------------------


def test_approve_requires_permission(s_db: Path) -> None:
    sar = _make_reviewed_sar(s_db)
    with pytest.raises(PermissionDenied):
        sar_store.approve_sar(s_db, sar["id"], actor="Analyst")


def test_submit_to_regulator_requires_permission(s_db: Path) -> None:
    sar = _make_approved_sar(s_db)
    with pytest.raises(PermissionDenied):
        sar_store.submit_to_regulator(
            s_db, sar["id"], actor="Analyst", submission_ref="REF"
        )


def test_submit_to_regulator_requires_ref(s_db: Path) -> None:
    sar = _make_approved_sar(s_db)
    with pytest.raises(MissingRequiredField):
        sar_store.submit_to_regulator(
            s_db, sar["id"], actor="MLRO", submission_ref="  "
        )


def test_acknowledge_requires_ref(s_db: Path) -> None:
    sar = _make_submitted_sar(s_db)
    with pytest.raises(MissingRequiredField):
        sar_store.acknowledge_receipt(s_db, sar["id"], actor="MLRO", acknowledgement_ref="")


def test_reject_requires_reason(s_db: Path) -> None:
    sar = _make_reviewed_sar(s_db)
    with pytest.raises(MissingRequiredField):
        sar_store.reject_sar(s_db, sar["id"], actor="ComplianceOfficer", rejection_reason="")


# ---------------------------------------------------------------------------
# LostUpdate
# ---------------------------------------------------------------------------


def test_lost_update_detection(s_db: Path) -> None:
    sar = _make_reviewed_sar(s_db)
    sar_store.approve_sar(s_db, sar["id"], actor="ComplianceOfficer")
    with pytest.raises(LostUpdate):
        sar_store.approve_sar(s_db, sar["id"], actor="MLRO",
                               expected_state="under_review")


# ---------------------------------------------------------------------------
# Subjects
# ---------------------------------------------------------------------------


def test_add_subject_happy(s_db: Path) -> None:
    sar = _make_sar(s_db)
    subj = sar_store.add_subject(
        s_db, sar["id"],
        subject_type="individual",
        subject_role="primary_subject",
        name="Иван Иванов",
        id_type="passport",
        id_number="4500 123456",
        nationality="RU",
        added_by="Analyst",
    )
    assert subj["name"] == "Иван Иванов"
    assert subj["subject_role"] == "primary_subject"


def test_get_subjects_returns_all(s_db: Path) -> None:
    sar = _make_sar(s_db)
    sar_store.add_subject(
        s_db, sar["id"], subject_type="individual",
        subject_role="primary_subject", name="А", added_by="X",
    )
    sar_store.add_subject(
        s_db, sar["id"], subject_type="legal",
        subject_role="related_party", name="ООО Б", added_by="X",
    )
    subjects = sar_store.get_subjects(s_db, sar["id"])
    assert len(subjects) == 2


def test_add_subject_invalid_type(s_db: Path) -> None:
    sar = _make_sar(s_db)
    with pytest.raises(SarStoreError, match="invalid subject_type"):
        sar_store.add_subject(
            s_db, sar["id"], subject_type="alien",
            subject_role="primary_subject", name="X", added_by="A",
        )


def test_add_subject_invalid_role(s_db: Path) -> None:
    sar = _make_sar(s_db)
    with pytest.raises(SarStoreError, match="invalid subject_role"):
        sar_store.add_subject(
            s_db, sar["id"], subject_type="individual",
            subject_role="villain", name="X", added_by="A",
        )


def test_add_subject_blank_name(s_db: Path) -> None:
    sar = _make_sar(s_db)
    with pytest.raises(MissingRequiredField):
        sar_store.add_subject(
            s_db, sar["id"], subject_type="individual",
            subject_role="primary_subject", name="  ", added_by="A",
        )


def test_add_subject_locked_when_submitted(s_db: Path) -> None:
    sar = _make_submitted_sar(s_db)
    with pytest.raises(NarrativeLocked):
        sar_store.add_subject(
            s_db, sar["id"], subject_type="individual",
            subject_role="primary_subject", name="Late Addition", added_by="A",
        )


def test_add_subject_to_nonexistent_sar(s_db: Path) -> None:
    with pytest.raises(SarNotFound):
        sar_store.add_subject(
            s_db, "bad-id", subject_type="individual",
            subject_role="primary_subject", name="X", added_by="A",
        )


# ---------------------------------------------------------------------------
# Deadline / overdue
# ---------------------------------------------------------------------------


def test_is_overdue_false_for_new_sar(s_db: Path) -> None:
    sar = _make_sar(s_db)
    assert not sar_store.is_overdue(sar)


def test_is_overdue_false_for_submitted(s_db: Path) -> None:
    sar = _make_submitted_sar(s_db)
    assert not sar_store.is_overdue(sar)


def test_list_overdue_empty_when_fresh(s_db: Path) -> None:
    _make_sar(s_db)
    assert sar_store.list_overdue(s_db) == []


def test_list_overdue_detects_past_deadline(s_db: Path) -> None:
    import sqlite3
    sar = _make_sar(s_db)
    # Backdate filing_deadline to yesterday
    conn = sqlite3.connect(s_db)
    conn.execute(
        "UPDATE sars SET filing_deadline='2020-01-01T00:00:00+00:00' WHERE id=?",
        (sar["id"],),
    )
    conn.commit()
    conn.close()
    overdue = sar_store.list_overdue(s_db)
    assert any(s["id"] == sar["id"] for s in overdue)


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


def test_audit_log_created_on_create(s_db: Path) -> None:
    sar = _make_sar(s_db)
    log = sar_store.get_audit_log(s_db, sar["id"])
    assert any(e["action"] == "created" for e in log)


def test_audit_log_records_transitions(s_db: Path) -> None:
    sar = _make_approved_sar(s_db)
    log = sar_store.get_audit_log(s_db, sar["id"])
    actions = {e["action"] for e in log}
    assert any("under_review" in a for a in actions)
    assert any("approved" in a for a in actions)


def test_audit_log_records_narrative_update(s_db: Path) -> None:
    sar = _make_sar(s_db)
    sar_store.update_narrative(s_db, sar["id"], narrative="Updated.", actor="Analyst")
    log = sar_store.get_audit_log(s_db, sar["id"])
    assert any(e["action"] == "narrative_updated" for e in log)


def test_audit_log_records_subject_add(s_db: Path) -> None:
    sar = _make_sar(s_db)
    sar_store.add_subject(
        s_db, sar["id"], subject_type="individual",
        subject_role="primary_subject", name="Test Person", added_by="A",
    )
    log = sar_store.get_audit_log(s_db, sar["id"])
    assert any(e["action"] == "subject_added" for e in log)


# ---------------------------------------------------------------------------
# Case→SAR bridge
# ---------------------------------------------------------------------------


def test_create_sar_from_case(s_db: Path) -> None:
    """SAR can be created with a case_id and listed by it."""
    sar = sar_store.create_sar(
        s_db,
        sar_type="str",
        narrative="Activity identified via Case AML-00001.",
        case_id="case-abc-123",
        created_by="ComplianceOfficer",
    )
    assert sar["case_id"] == "case-abc-123"
    by_case = sar_store.list_sars(s_db, case_id="case-abc-123")
    assert len(by_case) == 1
    assert by_case[0]["id"] == sar["id"]
