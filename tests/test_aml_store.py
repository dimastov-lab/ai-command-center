from __future__ import annotations

import pytest

from command_center import aml_store
from command_center.ui.aml_panel import DEMO_CASES


def _db(tmp_path):
    path = tmp_path / "aml.db"
    aml_store.seed_cases(DEMO_CASES, path)
    return path


def test_seed_persists_normalized_cases_transactions_and_audit(tmp_path):
    path = _db(tmp_path)
    cases = aml_store.list_cases(path)

    assert len(cases) == 4
    assert cases[0]["id"] == "AML-2026-0418"
    assert len(cases[0]["transactions"]) == 3
    assert aml_store.list_audit_events("AML-2026-0418", path)[0]["event"] == "Создан алерт"


def test_analyst_transition_is_persistent_versioned_and_audited(tmp_path):
    path = _db(tmp_path)
    case = aml_store.list_cases(path)[0]

    updated = aml_store.transition_case(
        case["id"],
        "assign",
        actor="Anna Analyst",
        role="Analyst",
        reason="Начата проверка",
        expected_version=case["version"],
        db_path=path,
    )

    assert updated["status"] == "review"
    assert updated["owner"] == "Anna Analyst"
    assert updated["version"] == 1
    assert aml_store.list_cases(path)[0]["status"] == "review"
    assert aml_store.list_audit_events(case["id"], path)[0]["actor"] == "Anna Analyst"

    with pytest.raises(aml_store.LostUpdate):
        aml_store.transition_case(
            case["id"], "documents", actor="Other", role="Analyst", reason="stale",
            expected_version=0, db_path=path,
        )


def test_critical_actions_require_role_confirmation_and_reason(tmp_path):
    path = _db(tmp_path)
    case = aml_store.list_cases(path)[0]

    with pytest.raises(aml_store.ConfirmationRequired):
        aml_store.transition_case(
            case["id"], "escalate", actor="Anna", role="Analyst", reason="",
            expected_version=case["version"], db_path=path,
        )
    with pytest.raises(aml_store.PermissionDenied):
        aml_store.transition_case(
            case["id"], "close", actor="Anna", role="Analyst", reason="Закрыть",
            expected_version=case["version"], confirmed=True, db_path=path,
        )

    closed = aml_store.transition_case(
        case["id"], "close", actor="Maria MLRO", role="MLRO", reason="Нет подозрений",
        expected_version=case["version"], confirmed=True, db_path=path,
    )
    assert closed["status"] == "closed"


def test_sar_draft_and_approval_are_role_gated_and_audited(tmp_path):
    path = _db(tmp_path)
    sar_id = aml_store.create_sar_draft(
        "AML-2026-0418", filing_type="SAR / STR", rationale="Необычный транзит средств",
        actor="Anna", role="Analyst", db_path=path,
    )

    with pytest.raises(aml_store.PermissionDenied):
        aml_store.approve_sar(
            sar_id, actor="Anna", role="Analyst", reason="approve", confirmed=True, db_path=path,
        )

    aml_store.approve_sar(
        sar_id, actor="Maria", role="MLRO", reason="Материалы проверены", confirmed=True, db_path=path,
    )
    assert aml_store.list_sar(path)[0]["status"] == "approved"
    assert aml_store.list_audit_events("AML-2026-0418", path)[0]["event"] == "Регуляторное сообщение утверждено"
