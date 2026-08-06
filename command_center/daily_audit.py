"""Persistent, restart-safe daily self-audit orchestration for Command Center.

The service deliberately keeps scheduling separate from execution.  A daemon
may call :meth:`DailyAuditService.tick` frequently; a persisted lease and
``next_run_at`` make at most one campaign due per day, even across restarts or
multiple hosts.

Campaign execution is delegated to a narrow backend so production can use the
real agent/GitHub adapters while tests exercise scheduling without subprocesses.
The backend must enforce the audit -> remediation -> review -> final-gate ->
CI -> merge contract; a campaign is successful only after the target branch is
verified.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from command_center.runtime import db as runtime_db

DEFAULT_INTERVAL = timedelta(days=1)
DEFAULT_LEASE = timedelta(hours=12)
PROJECT = "AICC"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class DailyAuditConfig:
    repository_path: Path
    enabled: bool = False
    interval: timedelta = DEFAULT_INTERVAL
    lease_duration: timedelta = DEFAULT_LEASE
    max_remediation_rounds: int = 5
    # The independent read-only final-gate run is the review authority for a
    # campaign.  GitHub still gates on every required check and mergeability,
    # but requiring a second approval from the PR author would permanently
    # block a fully headless service.
    merge_mode: str = "auto_after_checks"
    validation_commands: tuple[tuple[str, ...], ...] = (
        ("ruff", "check", "."),
        ("python", "-m", "compileall", "-q", "command_center", "scripts"),
        ("python", "-m", "pytest"),
    )

    @classmethod
    def from_environment(cls, repository_path: Path) -> "DailyAuditConfig":
        enabled = os.environ.get("AICC_DAILY_AUDIT_ENABLED", "").strip().lower()
        rounds = int(os.environ.get("AICC_DAILY_AUDIT_MAX_REMEDIATION_ROUNDS", "5"))
        return cls(
            repository_path=repository_path.resolve(),
            enabled=enabled in {"1", "true", "yes", "on"},
            max_remediation_rounds=max(1, rounds),
            validation_commands=(
                (sys.executable, "-m", "ruff", "check", "."),
                (
                    sys.executable,
                    "-m",
                    "compileall",
                    "-q",
                    "command_center",
                    "scripts",
                ),
                (sys.executable, "-m", "pytest"),
            ),
        )


@dataclass(frozen=True)
class CampaignRequest:
    campaign_id: str
    repository_path: Path
    max_remediation_rounds: int
    merge_mode: str
    validation_commands: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class CampaignResult:
    status: str
    summary: str
    target_verified: bool = False
    pull_request_url: str | None = None


class CampaignBackend(Protocol):
    def run(self, request: CampaignRequest) -> CampaignResult: ...


class DailyAuditStore:
    """Small SQLite store independent from the runtime schema migrations."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _migrate(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS daily_audit_schedule (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    next_run_at TEXT NOT NULL,
                    lease_owner TEXT,
                    lease_until TEXT,
                    active_campaign_id TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS daily_audit_campaign (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    summary TEXT,
                    pull_request_url TEXT,
                    target_verified INTEGER NOT NULL DEFAULT 0
                );
                """
            )

    def ensure_schedule(self, now: datetime) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO daily_audit_schedule
                   (singleton, next_run_at, updated_at) VALUES (1, ?, ?)""",
                (iso(now), iso(now)),
            )

    def acquire_due(
        self, *, now: datetime, owner: str, lease_duration: timedelta
    ) -> str | None:
        """Atomically claim a due campaign; stale leases are recoverable."""
        self.ensure_schedule(now)
        campaign_id = uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM daily_audit_schedule WHERE singleton = 1"
            ).fetchone()
            due = parse_time(row["next_run_at"]) <= now
            lease_expired = not row["lease_until"] or parse_time(row["lease_until"]) <= now
            if not due or not lease_expired:
                connection.rollback()
                return None
            cursor = connection.execute(
                """UPDATE daily_audit_schedule
                   SET lease_owner = ?, lease_until = ?, active_campaign_id = ?, updated_at = ?
                   WHERE singleton = 1 AND next_run_at = ?""",
                (
                    owner,
                    iso(now + lease_duration),
                    campaign_id,
                    iso(now),
                    row["next_run_at"],
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return None
            connection.execute(
                """INSERT INTO daily_audit_campaign (id, status, started_at)
                   VALUES (?, 'running', ?)""",
                (campaign_id, iso(now)),
            )
            connection.commit()
            return campaign_id

    def finish(
        self,
        campaign_id: str,
        result: CampaignResult,
        *,
        now: datetime,
        interval: timedelta,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """UPDATE daily_audit_campaign
                   SET status = ?, finished_at = ?, summary = ?,
                       pull_request_url = ?, target_verified = ?
                   WHERE id = ?""",
                (
                    result.status,
                    iso(now),
                    result.summary,
                    result.pull_request_url,
                    int(result.target_verified),
                    campaign_id,
                ),
            )
            connection.execute(
                """UPDATE daily_audit_schedule
                   SET next_run_at = ?, lease_owner = NULL, lease_until = NULL,
                       active_campaign_id = NULL, updated_at = ?
                   WHERE singleton = 1 AND active_campaign_id = ?""",
                (iso(now + interval), iso(now), campaign_id),
            )
            connection.commit()

    def fail(
        self, campaign_id: str, message: str, *, now: datetime, retry_after: timedelta
    ) -> None:
        self.finish(
            campaign_id,
            CampaignResult(status="failed", summary=message),
            now=now,
            interval=retry_after,
        )

    def status(self) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM daily_audit_schedule WHERE singleton = 1"
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            if result.get("active_campaign_id"):
                campaign = connection.execute(
                    "SELECT * FROM daily_audit_campaign WHERE id = ?",
                    (result["active_campaign_id"],),
                ).fetchone()
                result["active_campaign"] = dict(campaign) if campaign else None
            return result

    def request_run_now(self, *, now: datetime) -> bool:
        """Make the next campaign due without disturbing an active lease.

        Returns ``False`` when a campaign is already active.  The UI uses this
        instead of starting work in the Streamlit process; the persistent
        daemon remains the sole campaign owner.
        """
        self.ensure_schedule(now)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT active_campaign_id FROM daily_audit_schedule WHERE singleton = 1"
            ).fetchone()
            if row and row["active_campaign_id"]:
                connection.rollback()
                return False
            connection.execute(
                """UPDATE daily_audit_schedule
                   SET next_run_at = ?, updated_at = ?
                   WHERE singleton = 1""",
                (iso(now), iso(now)),
            )
            connection.commit()
            return True

    def list_campaigns(self, *, limit: int = 20) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM daily_audit_campaign
                   ORDER BY started_at DESC LIMIT ?""",
                (max(1, min(limit, 100)),),
            ).fetchall()
            return [dict(row) for row in rows]


class DailyAuditService:
    def __init__(
        self,
        config: DailyAuditConfig,
        backend: CampaignBackend,
        *,
        db_path: Path | None = None,
        owner: str | None = None,
        clock=utc_now,
    ) -> None:
        self.config = config
        self.backend = backend
        self.clock = clock
        self.owner = owner or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        resolved_db = db_path or runtime_db.resolve_db_path()
        self.store = DailyAuditStore(resolved_db)

    def tick(self) -> CampaignResult | None:
        if not self.config.enabled:
            return None
        now = self.clock()
        campaign_id = self.store.acquire_due(
            now=now, owner=self.owner, lease_duration=self.config.lease_duration
        )
        if campaign_id is None:
            return None
        request = CampaignRequest(
            campaign_id=campaign_id,
            repository_path=self.config.repository_path,
            max_remediation_rounds=self.config.max_remediation_rounds,
            merge_mode=self.config.merge_mode,
            validation_commands=self.config.validation_commands,
        )
        try:
            result = self.backend.run(request)
        except Exception as exc:  # noqa: BLE001 - persist failure before retrying
            self.store.fail(
                campaign_id,
                f"{type(exc).__name__}: {exc}",
                now=self.clock(),
                retry_after=timedelta(hours=1),
            )
            raise
        if result.status == "completed" and not result.target_verified:
            result = CampaignResult(
                status="failed",
                summary="Backend claimed completion without target-branch verification.",
                pull_request_url=result.pull_request_url,
            )
        self.store.finish(
            campaign_id, result, now=self.clock(), interval=self.config.interval
        )
        return result

    def status_json(self) -> str:
        return json.dumps(self.store.status(), ensure_ascii=False, indent=2)
