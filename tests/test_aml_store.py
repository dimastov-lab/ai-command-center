from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

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


def test_v0_migration_reconciles_legacy_sar_and_case_states(tmp_path):
    path = _db(tmp_path)
    case = aml_store.list_cases(path)[0]
    review = aml_store.transition_case(
        case["id"], "assign", actor="Anna", role="Analyst", reason="Проверка",
        expected_version=case["version"], db_path=path,
    )
    escalated = aml_store.transition_case(
        case["id"], "escalate", actor="Anna", role="Analyst", reason="Требуется решение",
        expected_version=review["version"], confirmed=True, db_path=path,
    )

    with sqlite3.connect(path) as connection:
        connection.execute("DROP INDEX idx_aml_sar_active_case")
        connection.execute("PRAGMA user_version = 0")
        connection.execute(
            """INSERT INTO aml_sar
               (id, case_id, filing_type, rationale, status, created_by, approved_by,
                created_at, updated_at)
               VALUES ('SAR-APPROVED', ?, 'SAR / STR', 'legacy approved', 'approved',
                       'Anna', 'Maria', '2026-07-01', '2026-07-03')""",
            (case["id"],),
        )
        connection.execute(
            """INSERT INTO aml_sar
               (id, case_id, filing_type, rationale, status, created_by,
                created_at, updated_at)
               VALUES ('SAR-DRAFT', ?, 'SAR / STR', 'legacy duplicate', 'draft',
                       'Anna', '2026-07-02', '2026-07-04')""",
            (case["id"],),
        )
        connection.execute(
            "UPDATE aml_case SET status = 'closed' WHERE id = ?", (case["id"],)
        )

    migrated = next(item for item in aml_store.list_cases(path) if item["id"] == case["id"])
    reports = {item["id"]: item for item in aml_store.list_sar(path)}

    assert migrated["status"] == "reported"
    assert migrated["version"] == escalated["version"] + 1
    assert reports["SAR-APPROVED"]["status"] == "approved"
    assert reports["SAR-DRAFT"]["status"] == "superseded"
    migration_events = [
        event for event in aml_store.list_audit_events(case["id"], path)
        if event["actor"] == "AML schema migration"
    ]
    assert len(migration_events) == 2
    assert any("SAR-DRAFT помечен superseded" in event["details"] for event in migration_events)


def test_v0_migration_reopens_closed_case_with_pending_draft(tmp_path):
    path = _db(tmp_path)
    case = aml_store.list_cases(path)[0]
    with sqlite3.connect(path) as connection:
        connection.execute("DROP INDEX idx_aml_sar_active_case")
        connection.execute("PRAGMA user_version = 0")
        connection.execute(
            """INSERT INTO aml_sar
               (id, case_id, filing_type, rationale, status, created_by, created_at, updated_at)
               VALUES ('SAR-DRAFT', ?, 'SAR / STR', 'legacy pending', 'draft',
                       'Anna', '2026-07-02', '2026-07-04')""",
            (case["id"],),
        )
        connection.execute(
            "UPDATE aml_case SET status = 'closed' WHERE id = ?", (case["id"],)
        )

    migrated = next(item for item in aml_store.list_cases(path) if item["id"] == case["id"])

    assert migrated["status"] == "escalated"
    assert aml_store.list_sar(path)[0]["status"] == "draft"


def test_audit_events_are_append_only_at_database_boundary(tmp_path):
    path = _db(tmp_path)

    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE aml_audit_event SET details = 'rewritten' WHERE id = 1")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM aml_audit_event WHERE id = 1")


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

    review = aml_store.transition_case(
        case["id"], "assign", actor="Anna", role="Analyst", reason="Проверка",
        expected_version=case["version"], db_path=path,
    )
    escalated = aml_store.transition_case(
        case["id"], "escalate", actor="Anna", role="Analyst", reason="Требуется решение",
        expected_version=review["version"], confirmed=True, db_path=path,
    )
    closed = aml_store.transition_case(
        case["id"], "close", actor="Maria MLRO", role="MLRO", reason="Нет подозрений",
        expected_version=escalated["version"], confirmed=True, db_path=path,
    )
    assert closed["status"] == "closed"


def test_repository_rejects_invalid_source_state_and_blank_actor(tmp_path):
    path = _db(tmp_path)
    case = aml_store.list_cases(path)[0]

    with pytest.raises(aml_store.InvalidTransition):
        aml_store.transition_case(
            case["id"], "close", actor="Maria", role="MLRO", reason="Закрыть",
            expected_version=case["version"], confirmed=True, db_path=path,
        )


def test_concurrent_case_updates_allow_exactly_one_winner(tmp_path):
    path = _db(tmp_path)
    case = aml_store.list_cases(path)[0]
    barrier = Barrier(2)

    def assign(actor):
        barrier.wait()
        try:
            aml_store.transition_case(
                case["id"], "assign", actor=actor, role="Analyst", reason="Проверка",
                expected_version=case["version"], db_path=path,
            )
        except aml_store.LostUpdate:
            return "lost"
        return "won"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(assign, ("Anna", "Boris")))

    assert sorted(outcomes) == ["lost", "won"]
    persisted = next(item for item in aml_store.list_cases(path) if item["id"] == case["id"])
    assert persisted["version"] == case["version"] + 1
    assert persisted["owner"] in {"Anna", "Boris"}
    with pytest.raises(aml_store.AmlStoreError, match="пользователя"):
        aml_store.transition_case(
            case["id"], "assign", actor="  ", role="Analyst", reason="Проверка",
            expected_version=case["version"], db_path=path,
        )


def test_sar_draft_and_approval_are_role_gated_and_audited(tmp_path):
    path = _db(tmp_path)
    case = aml_store.list_cases(path)[0]
    review = aml_store.transition_case(
        case["id"], "assign", actor="Anna", role="Analyst", reason="Проверка",
        expected_version=case["version"], db_path=path,
    )
    aml_store.transition_case(
        case["id"], "escalate", actor="Anna", role="Analyst", reason="Требуется решение",
        expected_version=review["version"], confirmed=True, db_path=path,
    )
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
    reported = next(case for case in aml_store.list_cases(path) if case["id"] == "AML-2026-0418")
    assert reported["status"] == "reported"
    assert reported["version"] == 3
    assert aml_store.list_audit_events("AML-2026-0418", path)[0]["event"] == "Регуляторное сообщение утверждено"


def test_sar_requires_escalated_case(tmp_path):
    path = _db(tmp_path)

    with pytest.raises(aml_store.InvalidTransition, match="эскалированного"):
        aml_store.create_sar_draft(
            "AML-2026-0418", filing_type="SAR / STR", rationale="Подозрительная операция",
            actor="Anna", role="Analyst", db_path=path,
        )


def test_case_with_sar_cannot_be_closed_without_report_or_receive_duplicate_draft(tmp_path):
    path = _db(tmp_path)
    case = aml_store.list_cases(path)[0]
    review = aml_store.transition_case(
        case["id"], "assign", actor="Anna", role="Analyst", reason="Проверка",
        expected_version=case["version"], db_path=path,
    )
    escalated = aml_store.transition_case(
        case["id"], "escalate", actor="Anna", role="Analyst", reason="Требуется решение",
        expected_version=review["version"], confirmed=True, db_path=path,
    )
    aml_store.create_sar_draft(
        case["id"], filing_type="SAR / STR", rationale="Подозрительная операция",
        actor="Anna", role="Analyst", db_path=path,
    )

    with pytest.raises(aml_store.InvalidTransition, match="нельзя закрыть без сообщения"):
        aml_store.transition_case(
            case["id"], "close", actor="Maria", role="MLRO", reason="Нет подозрений",
            expected_version=escalated["version"], confirmed=True, db_path=path,
        )
    with pytest.raises(aml_store.InvalidTransition, match="уже существует"):
        aml_store.create_sar_draft(
            case["id"], filing_type="SAR / STR", rationale="Дубликат",
            actor="Anna", role="Analyst", db_path=path,
        )


def test_concurrent_sar_creation_and_close_preserve_cross_entity_invariant(tmp_path):
    path = _db(tmp_path)
    case = aml_store.list_cases(path)[0]
    review = aml_store.transition_case(
        case["id"], "assign", actor="Anna", role="Analyst", reason="Проверка",
        expected_version=case["version"], db_path=path,
    )
    escalated = aml_store.transition_case(
        case["id"], "escalate", actor="Anna", role="Analyst", reason="Требуется решение",
        expected_version=review["version"], confirmed=True, db_path=path,
    )
    barrier = Barrier(2)

    def create_report():
        barrier.wait()
        try:
            aml_store.create_sar_draft(
                case["id"], filing_type="SAR / STR", rationale="Подозрительная операция",
                actor="Anna", role="Analyst", db_path=path,
            )
        except aml_store.InvalidTransition:
            return "sar_rejected"
        return "sar_created"

    def close_without_report():
        barrier.wait()
        try:
            aml_store.transition_case(
                case["id"], "close", actor="Maria", role="MLRO", reason="Нет подозрений",
                expected_version=escalated["version"], confirmed=True, db_path=path,
            )
        except aml_store.InvalidTransition:
            return "close_rejected"
        return "case_closed"

    with ThreadPoolExecutor(max_workers=2) as executor:
        create_future = executor.submit(create_report)
        close_future = executor.submit(close_without_report)
        outcomes = {create_future.result(), close_future.result()}

    persisted = next(item for item in aml_store.list_cases(path) if item["id"] == case["id"])
    sar_records = aml_store.list_sar(path)
    if outcomes == {"case_closed", "sar_rejected"}:
        assert persisted["status"] == "closed"
        assert sar_records == []
    else:
        assert outcomes == {"sar_created", "close_rejected"}
        assert persisted["status"] == "escalated"
        assert len(sar_records) == 1


def test_reads_do_not_wait_for_writer_lock_in_wal_mode(tmp_path):
    path = _db(tmp_path)
    writer = sqlite3.connect(path, timeout=1)
    try:
        writer.execute("PRAGMA journal_mode = WAL")
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE aml_case SET score = 95 WHERE id = 'AML-2026-0418'")

        cases = aml_store.list_cases(path)
    finally:
        writer.rollback()
        writer.close()

    assert next(case for case in cases if case["id"] == "AML-2026-0418")["score"] == 94
