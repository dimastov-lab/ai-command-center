"""Persistent AML store with role-gated, audited state transitions."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from command_center import storage

ROOT = Path(__file__).resolve().parent.parent
ROLES = ("Analyst", "MLRO")
CRITICAL_ACTIONS = frozenset({"escalate", "close", "approve_sar"})
ACTION_POLICY = {
    "assign": (frozenset({"Analyst"}), "review", "Кейс взят в работу"),
    "documents": (frozenset({"Analyst"}), "waiting", "Запрошены документы"),
    "escalate": (frozenset({"Analyst"}), "escalated", "Кейс эскалирован MLRO"),
    "close": (frozenset({"MLRO"}), "closed", "Кейс закрыт"),
}


class AmlStoreError(Exception):
    pass


class PermissionDenied(AmlStoreError):
    pass


class ConfirmationRequired(AmlStoreError):
    pass


class LostUpdate(AmlStoreError):
    pass


def resolve_db_path(root: Path | None = None) -> Path:
    return storage.resolve_data_dir(root or ROOT) / "aml.db"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@contextmanager
def _transaction(db_path: Path) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except Exception:
        connection.rollback()
        raise
    else:
        connection.commit()
    finally:
        connection.close()


def init_db(db_path: Path | None = None) -> Path:
    path = db_path or resolve_db_path()
    with _transaction(path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS aml_case (
                id TEXT PRIMARY KEY, customer TEXT NOT NULL, country TEXT NOT NULL,
                risk TEXT NOT NULL, score INTEGER NOT NULL CHECK(score BETWEEN 0 AND 100),
                status TEXT NOT NULL, amount REAL NOT NULL, currency TEXT NOT NULL,
                opened TEXT NOT NULL, owner TEXT, scenario TEXT NOT NULL, summary TEXT NOT NULL,
                factors_json TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS aml_transaction (
                id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES aml_case(id) ON DELETE CASCADE,
                occurred_at TEXT NOT NULL, transaction_type TEXT NOT NULL,
                amount_display TEXT NOT NULL, counterparty TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_aml_transaction_case ON aml_transaction(case_id);
            CREATE TABLE IF NOT EXISTS aml_audit_event (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL REFERENCES aml_case(id) ON DELETE CASCADE,
                occurred_at TEXT NOT NULL, actor TEXT NOT NULL, role TEXT NOT NULL,
                event TEXT NOT NULL, details TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_aml_audit_case ON aml_audit_event(case_id, id DESC);
            CREATE TABLE IF NOT EXISTS aml_sar (
                id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES aml_case(id) ON DELETE CASCADE,
                filing_type TEXT NOT NULL, rationale TEXT NOT NULL, status TEXT NOT NULL,
                created_by TEXT NOT NULL, approved_by TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            """
        )
    return path


def seed_cases(cases: tuple[dict[str, Any], ...], db_path: Path | None = None) -> None:
    path = init_db(db_path)
    with _transaction(path) as connection:
        if connection.execute("SELECT 1 FROM aml_case LIMIT 1").fetchone():
            return
        now = _now()
        for case in cases:
            connection.execute(
                """INSERT INTO aml_case
                   (id, customer, country, risk, score, status, amount, currency, opened,
                    owner, scenario, summary, factors_json, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    case["id"], case["customer"], case["country"], case["risk"], case["score"],
                    case["status"], case["amount"], case["currency"], case["opened"],
                    None if case["owner"] == "Не назначен" else case["owner"],
                    case["scenario"], case["summary"], json.dumps(case["factors"], ensure_ascii=False), now,
                ),
            )
            for occurred_at, transaction_type, amount_display, counterparty in case["transactions"]:
                connection.execute(
                    """INSERT INTO aml_transaction
                       (id, case_id, occurred_at, transaction_type, amount_display, counterparty)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (uuid.uuid4().hex, case["id"], occurred_at, transaction_type, amount_display, counterparty),
                )
            connection.execute(
                """INSERT INTO aml_audit_event
                   (case_id, occurred_at, actor, role, event, details) VALUES (?, ?, ?, ?, ?, ?)""",
                (case["id"], now, "Monitoring engine", "System", "Создан алерт",
                 f"{case['scenario']} · risk score {case['score']}"),
            )


def _case_from_row(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    case = dict(row)
    case["factors"] = tuple(json.loads(case.pop("factors_json")))
    case["owner"] = case["owner"] or "Не назначен"
    case["transactions"] = tuple(
        (tx["occurred_at"], tx["transaction_type"], tx["amount_display"], tx["counterparty"])
        for tx in connection.execute(
            """SELECT occurred_at, transaction_type, amount_display, counterparty
               FROM aml_transaction WHERE case_id = ? ORDER BY rowid""",
            (case["id"],),
        )
    )
    return case


def list_cases(db_path: Path | None = None) -> list[dict[str, Any]]:
    path = init_db(db_path)
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT * FROM aml_case ORDER BY score DESC, id DESC").fetchall()
        return [_case_from_row(connection, row) for row in rows]


def list_audit_events(case_id: str | None = None, db_path: Path | None = None) -> list[dict[str, Any]]:
    path = init_db(db_path)
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        if case_id:
            rows = connection.execute(
                "SELECT * FROM aml_audit_event WHERE case_id = ? ORDER BY id DESC", (case_id,)
            ).fetchall()
        else:
            rows = connection.execute("SELECT * FROM aml_audit_event ORDER BY id DESC").fetchall()
        return [dict(row) for row in rows]


def transition_case(
    case_id: str,
    action: str,
    *,
    actor: str,
    role: str,
    reason: str,
    expected_version: int,
    confirmed: bool = False,
    db_path: Path | None = None,
) -> dict[str, Any]:
    if role not in ROLES:
        raise PermissionDenied(f"Неизвестная роль: {role}")
    if action not in ACTION_POLICY:
        raise AmlStoreError(f"Неизвестное действие: {action}")
    allowed_roles, target_status, event = ACTION_POLICY[action]
    if role not in allowed_roles:
        raise PermissionDenied(f"Роль {role} не может выполнить действие {action}")
    clean_reason = reason.strip()
    if action in CRITICAL_ACTIONS and (not confirmed or not clean_reason):
        raise ConfirmationRequired("Критическое действие требует подтверждения и причины")

    path = init_db(db_path)
    with _transaction(path) as connection:
        row = connection.execute("SELECT * FROM aml_case WHERE id = ?", (case_id,)).fetchone()
        if row is None:
            raise AmlStoreError(f"Кейс не найден: {case_id}")
        if row["version"] != expected_version:
            raise LostUpdate("Кейс уже изменён другим пользователем; обновите страницу")
        owner = actor if action == "assign" or not row["owner"] else row["owner"]
        cursor = connection.execute(
            """UPDATE aml_case SET status = ?, owner = ?, version = version + 1, updated_at = ?
               WHERE id = ? AND version = ?""",
            (target_status, owner, _now(), case_id, expected_version),
        )
        if cursor.rowcount != 1:
            raise LostUpdate("Кейс уже изменён другим пользователем; обновите страницу")
        connection.execute(
            """INSERT INTO aml_audit_event
               (case_id, occurred_at, actor, role, event, details) VALUES (?, ?, ?, ?, ?, ?)""",
            (case_id, _now(), actor, role, event, clean_reason or "Без дополнительного комментария"),
        )
        updated = connection.execute("SELECT * FROM aml_case WHERE id = ?", (case_id,)).fetchone()
        return _case_from_row(connection, updated)


def create_sar_draft(
    case_id: str,
    *,
    filing_type: str,
    rationale: str,
    actor: str,
    role: str,
    db_path: Path | None = None,
) -> str:
    if role not in ROLES:
        raise PermissionDenied("Создать черновик может Analyst или MLRO")
    if not rationale.strip():
        raise AmlStoreError("Укажите основание для сообщения")
    path = init_db(db_path)
    sar_id = f"SAR-{uuid.uuid4().hex[:8].upper()}"
    now = _now()
    with _transaction(path) as connection:
        if not connection.execute("SELECT 1 FROM aml_case WHERE id = ?", (case_id,)).fetchone():
            raise AmlStoreError(f"Кейс не найден: {case_id}")
        connection.execute(
            """INSERT INTO aml_sar
               (id, case_id, filing_type, rationale, status, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'draft', ?, ?, ?)""",
            (sar_id, case_id, filing_type, rationale.strip(), actor, now, now),
        )
        connection.execute(
            """INSERT INTO aml_audit_event
               (case_id, occurred_at, actor, role, event, details)
               VALUES (?, ?, ?, ?, 'Создан черновик отчёта', ?)""",
            (case_id, now, actor, role, filing_type),
        )
    return sar_id


def list_sar(db_path: Path | None = None) -> list[dict[str, Any]]:
    path = init_db(db_path)
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute("SELECT * FROM aml_sar ORDER BY created_at DESC")]


def approve_sar(
    sar_id: str,
    *,
    actor: str,
    role: str,
    reason: str,
    confirmed: bool,
    db_path: Path | None = None,
) -> None:
    if role != "MLRO":
        raise PermissionDenied("Утвердить сообщение может только MLRO")
    if not confirmed or not reason.strip():
        raise ConfirmationRequired("Утверждение требует подтверждения и причины")
    path = init_db(db_path)
    with _transaction(path) as connection:
        sar = connection.execute("SELECT * FROM aml_sar WHERE id = ?", (sar_id,)).fetchone()
        if sar is None:
            raise AmlStoreError(f"Черновик не найден: {sar_id}")
        if sar["status"] != "draft":
            raise AmlStoreError("Сообщение уже обработано")
        connection.execute(
            "UPDATE aml_sar SET status = 'approved', approved_by = ?, updated_at = ? WHERE id = ?",
            (actor, _now(), sar_id),
        )
        connection.execute(
            """INSERT INTO aml_audit_event
               (case_id, occurred_at, actor, role, event, details)
               VALUES (?, ?, ?, ?, 'Регуляторное сообщение утверждено', ?)""",
            (sar["case_id"], _now(), actor, role, reason.strip()),
        )
