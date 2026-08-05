"""AML Alert store — database schema and connection helpers."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from command_center import storage

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AlertStoreError(Exception):
    pass


class PermissionDenied(AlertStoreError):
    pass


class InvalidTransition(AlertStoreError):
    pass


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def resolve_db_path(root: Path | None = None) -> Path:
    return storage.resolve_data_dir(root or ROOT) / "aml_alerts.db"


# ---------------------------------------------------------------------------
# Connection context manager
# ---------------------------------------------------------------------------


@contextmanager
def _db(db_path: Path) -> Iterator[sqlite3.Connection]:
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


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------


def init_db(db_path: Path) -> None:
    """Create alert tables if they do not already exist."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=5)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    try:
        connection.executescript(
            """
            BEGIN IMMEDIATE;

            CREATE TABLE IF NOT EXISTS alerts (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                source_ref TEXT,
                subject_id TEXT NOT NULL,
                subject_type TEXT NOT NULL
                    CHECK(subject_type IN ('customer','transaction','account')),
                trigger_desc TEXT NOT NULL,
                priority TEXT NOT NULL
                    CHECK(priority IN ('low','medium','high','critical')),
                priority_rationale TEXT,
                state TEXT NOT NULL DEFAULT 'generated'
                    CHECK(state IN (
                        'generated','assigned','in_triage',
                        'pending_review','escalated','closed','archived'
                    )),
                outcome TEXT
                    CHECK(outcome IN (
                        'true_positive','false_positive',
                        'escalated_to_case','closed_no_action','sar_filed'
                    )),
                disposition_rationale TEXT,
                owner TEXT,
                due_at TEXT,
                overdue INTEGER NOT NULL DEFAULT 0,
                case_id TEXT,
                predecessor_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                schema_version INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS alert_evidence (
                alert_id TEXT NOT NULL REFERENCES alerts(id),
                evidence_ref TEXT NOT NULL,
                attached_at TEXT NOT NULL,
                attached_by TEXT NOT NULL,
                PRIMARY KEY (alert_id, evidence_ref)
            );

            CREATE TABLE IF NOT EXISTS alert_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_id TEXT NOT NULL,
                action TEXT NOT NULL,
                actor TEXT NOT NULL,
                old_state TEXT,
                new_state TEXT,
                rationale TEXT,
                ts TEXT NOT NULL
            );

            PRAGMA user_version = 1;
            COMMIT;
            """
        )
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()
