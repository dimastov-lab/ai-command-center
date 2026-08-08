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

import contextlib
import json
import os
import socket
import sqlite3
import sys
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Protocol

from command_center.runtime import db as runtime_db

DEFAULT_INTERVAL = timedelta(days=1)
DEFAULT_LEASE = timedelta(hours=12)
DEFAULT_FAILURE_RETRY = timedelta(hours=1)
MAX_FAILURE_RETRY = timedelta(days=1)
DEFAULT_RUN_TIMEOUT_SECONDS = 3_600
DEFAULT_GIT_TIMEOUT_SECONDS = 120
DEFAULT_VALIDATION_TIMEOUT_SECONDS = 900
DEFAULT_COMPLETION_TIMEOUT_SECONDS = 6 * 3_600
DEFAULT_PROVIDER_ID = "claude_code"
DEFAULT_TRANSPORT_RETRY_ATTEMPTS = 3
DEFAULT_TRANSPORT_RETRY_BASE_SECONDS = 2.0
DEFAULT_MAX_CONSECUTIVE_FAILURES = 3
PROJECT = "AICC"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    # Keep sub-second precision when present.  Truncating lease timestamps to
    # whole seconds can make a short lease expire immediately after renewal.
    return value.astimezone(timezone.utc).isoformat()


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
    run_timeout_seconds: int = DEFAULT_RUN_TIMEOUT_SECONDS
    git_timeout_seconds: int = DEFAULT_GIT_TIMEOUT_SECONDS
    validation_timeout_seconds: int = DEFAULT_VALIDATION_TIMEOUT_SECONDS
    completion_timeout_seconds: int = DEFAULT_COMPLETION_TIMEOUT_SECONDS
    lease_heartbeat_seconds: float | None = None
    provider_id: str = DEFAULT_PROVIDER_ID
    transport_retry_attempts: int = DEFAULT_TRANSPORT_RETRY_ATTEMPTS
    transport_retry_base_seconds: float = DEFAULT_TRANSPORT_RETRY_BASE_SECONDS
    max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES

    @classmethod
    def from_environment(cls, repository_path: Path) -> "DailyAuditConfig":
        enabled = os.environ.get("AICC_DAILY_AUDIT_ENABLED", "").strip().lower()
        rounds = int(os.environ.get("AICC_DAILY_AUDIT_MAX_REMEDIATION_ROUNDS", "5"))
        run_timeout = int(
            os.environ.get("AICC_DAILY_AUDIT_RUN_TIMEOUT_SECONDS", str(DEFAULT_RUN_TIMEOUT_SECONDS))
        )
        completion_timeout = int(
            os.environ.get(
                "AICC_DAILY_AUDIT_COMPLETION_TIMEOUT_SECONDS",
                str(DEFAULT_COMPLETION_TIMEOUT_SECONDS),
            )
        )
        provider_id = os.environ.get("AICC_DAILY_AUDIT_PROVIDER_ID", DEFAULT_PROVIDER_ID).strip()
        transport_attempts = int(
            os.environ.get(
                "AICC_DAILY_AUDIT_TRANSPORT_RETRY_ATTEMPTS",
                str(DEFAULT_TRANSPORT_RETRY_ATTEMPTS),
            )
        )
        transport_base = float(
            os.environ.get(
                "AICC_DAILY_AUDIT_TRANSPORT_RETRY_BASE_SECONDS",
                str(DEFAULT_TRANSPORT_RETRY_BASE_SECONDS),
            )
        )
        max_failures = int(
            os.environ.get(
                "AICC_DAILY_AUDIT_MAX_CONSECUTIVE_FAILURES",
                str(DEFAULT_MAX_CONSECUTIVE_FAILURES),
            )
        )
        return cls(
            repository_path=repository_path.resolve(),
            enabled=enabled in {"1", "true", "yes", "on"},
            max_remediation_rounds=max(1, min(rounds, 10)),
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
            run_timeout_seconds=max(30, min(run_timeout, 3_600)),
            completion_timeout_seconds=max(60, min(completion_timeout, 12 * 3_600)),
            provider_id=provider_id or DEFAULT_PROVIDER_ID,
            transport_retry_attempts=max(1, min(transport_attempts, 5)),
            transport_retry_base_seconds=max(0.0, min(transport_base, 30.0)),
            max_consecutive_failures=max(1, min(max_failures, 10)),
        )


@dataclass(frozen=True)
class CampaignRequest:
    campaign_id: str
    repository_path: Path
    max_remediation_rounds: int
    merge_mode: str
    validation_commands: tuple[tuple[str, ...], ...]
    run_timeout_seconds: int = DEFAULT_RUN_TIMEOUT_SECONDS
    git_timeout_seconds: int = DEFAULT_GIT_TIMEOUT_SECONDS
    validation_timeout_seconds: int = DEFAULT_VALIDATION_TIMEOUT_SECONDS
    completion_timeout_seconds: int = DEFAULT_COMPLETION_TIMEOUT_SECONDS
    provider_id: str = DEFAULT_PROVIDER_ID
    transport_retry_attempts: int = DEFAULT_TRANSPORT_RETRY_ATTEMPTS
    transport_retry_base_seconds: float = DEFAULT_TRANSPORT_RETRY_BASE_SECONDS
    lease_owner: str | None = None
    abort_event: threading.Event | None = field(default=None, repr=False, compare=False)
    lease_check: Callable[[], bool] | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class CampaignResult:
    status: str
    summary: str
    target_verified: bool = False
    pull_request_url: str | None = None


class CampaignBackendError(RuntimeError):
    """Backend failure with an optional provider-supplied retry boundary."""

    def __init__(self, message: str, *, retry_at: datetime | None = None) -> None:
        super().__init__(message)
        self.retry_at = retry_at


class CampaignBackend(Protocol):
    def run(self, request: CampaignRequest) -> CampaignResult: ...


class DailyAuditStore:
    """Small SQLite store independent from the runtime schema migrations."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._migrate()

    @contextlib.contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=30000")
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            # sqlite3.Connection.__exit__ commits/rolls back but deliberately
            # does not close the descriptor.  The daemon polls forever, so an
            # explicit close is required to keep DB/WAL descriptors bounded.
            connection.close()

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
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    circuit_open INTEGER NOT NULL DEFAULT 0,
                    circuit_reason TEXT,
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
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(daily_audit_schedule)").fetchall()
            }
            if "consecutive_failures" not in columns:
                connection.execute(
                    """ALTER TABLE daily_audit_schedule
                       ADD COLUMN consecutive_failures INTEGER NOT NULL DEFAULT 0"""
                )
            if "circuit_open" not in columns:
                connection.execute(
                    """ALTER TABLE daily_audit_schedule
                       ADD COLUMN circuit_open INTEGER NOT NULL DEFAULT 0"""
                )
            if "circuit_reason" not in columns:
                connection.execute(
                    "ALTER TABLE daily_audit_schedule ADD COLUMN circuit_reason TEXT"
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
            if bool(row["circuit_open"]) or not due or not lease_expired:
                connection.rollback()
                return None
            abandoned = row["active_campaign_id"]
            if abandoned:
                connection.execute(
                    """UPDATE daily_audit_campaign
                       SET status = 'interrupted', finished_at = ?,
                           summary = 'Lease expired before the previous campaign finished.'
                       WHERE id = ? AND status = 'running'""",
                    (iso(now), abandoned),
                )
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

    def renew_lease(
        self,
        campaign_id: str,
        *,
        owner: str,
        now: datetime,
        lease_duration: timedelta,
    ) -> bool:
        """Extend only the lease still owned by this exact campaign and owner.

        Completion may have granted a longer publication-action fence in the
        same row.  A normal scheduler heartbeat must never shorten that fence,
        so the update is a monotonic max rather than an unconditional write.
        """
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT lease_until FROM daily_audit_schedule
                   WHERE singleton = 1 AND active_campaign_id = ? AND lease_owner = ?""",
                (campaign_id, owner),
            ).fetchone()
            if row is None or not row["lease_until"]:
                connection.rollback()
                return False
            current_until = parse_time(row["lease_until"])
            if current_until <= now:
                connection.rollback()
                return False
            extended_until = max(current_until, now + lease_duration)
            cursor = connection.execute(
                """UPDATE daily_audit_schedule
                   SET lease_until = ?, updated_at = ?
                   WHERE singleton = 1 AND active_campaign_id = ? AND lease_owner = ?
                     AND lease_until = ?""",
                (
                    iso(extended_until),
                    iso(now),
                    campaign_id,
                    owner,
                    row["lease_until"],
                ),
            )
            connection.commit()
            return cursor.rowcount == 1

    def owns_lease(
        self,
        campaign_id: str,
        *,
        owner: str,
        now: datetime,
    ) -> bool:
        """Read the current fencing state immediately before a backend side effect."""
        with self._connect() as connection:
            row = connection.execute(
                """SELECT active_campaign_id, lease_owner, lease_until
                   FROM daily_audit_schedule WHERE singleton = 1"""
            ).fetchone()
        return bool(
            row
            and row["active_campaign_id"] == campaign_id
            and row["lease_owner"] == owner
            and row["lease_until"]
            and parse_time(row["lease_until"]) > now
        )

    def finish(
        self,
        campaign_id: str,
        result: CampaignResult,
        *,
        owner: str,
        now: datetime,
        interval: timedelta,
    ) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            schedule = connection.execute(
                """SELECT active_campaign_id, lease_owner, lease_until
                   FROM daily_audit_schedule WHERE singleton = 1"""
            ).fetchone()
            owns_lease = bool(
                schedule
                and schedule["active_campaign_id"] == campaign_id
                and schedule["lease_owner"] == owner
                and schedule["lease_until"]
                and parse_time(schedule["lease_until"]) > now
            )
            if not owns_lease:
                connection.execute(
                    """UPDATE daily_audit_campaign
                       SET status = 'interrupted', finished_at = ?,
                           summary = 'Campaign finished after losing its scheduler lease.'
                       WHERE id = ? AND status = 'running'""",
                    (iso(now), campaign_id),
                )
                connection.commit()
                return False
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
                       active_campaign_id = NULL, consecutive_failures = 0,
                       circuit_open = 0, circuit_reason = NULL, updated_at = ?
                   WHERE singleton = 1 AND active_campaign_id = ?""",
                (iso(now + interval), iso(now), campaign_id),
            )
            connection.commit()
            return True

    def fail(
        self,
        campaign_id: str,
        message: str,
        *,
        owner: str,
        now: datetime,
        retry_after: timedelta = DEFAULT_FAILURE_RETRY,
        max_retry_after: timedelta = MAX_FAILURE_RETRY,
        retry_at: datetime | None = None,
        max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES,
    ) -> datetime | None:
        """Persist failure and atomically schedule bounded exponential backoff."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            schedule = connection.execute(
                "SELECT * FROM daily_audit_schedule WHERE singleton = 1"
            ).fetchone()
            owns_live_lease = bool(
                schedule
                and schedule["active_campaign_id"] == campaign_id
                and schedule["lease_owner"] == owner
                and schedule["lease_until"]
                and parse_time(schedule["lease_until"]) > now
            )
            if not owns_live_lease:
                connection.execute(
                    """UPDATE daily_audit_campaign
                       SET status = 'interrupted', finished_at = COALESCE(finished_at, ?),
                           summary = COALESCE(
                               summary,
                               'Campaign lost its scheduler lease before failure was persisted.'
                           )
                       WHERE id = ? AND status = 'running'""",
                    (iso(now), campaign_id),
                )
                connection.commit()
                return None
            connection.execute(
                """UPDATE daily_audit_campaign
                   SET status = 'failed', finished_at = ?, summary = ?
                   WHERE id = ?""",
                (iso(now), message, campaign_id),
            )
            failures = int(schedule["consecutive_failures"] or 0) + 1
            circuit_open = failures >= max(1, max_consecutive_failures)
            multiplier = 2 ** min(failures - 1, 10)
            delay_seconds = min(
                retry_after.total_seconds() * multiplier,
                max_retry_after.total_seconds(),
            )
            next_run = now + timedelta(seconds=delay_seconds)
            if retry_at is not None:
                normalized_retry_at = (
                    retry_at.astimezone(timezone.utc)
                    if retry_at.tzinfo
                    else retry_at.replace(tzinfo=timezone.utc)
                )
                next_run = max(next_run, normalized_retry_at)
            connection.execute(
                """UPDATE daily_audit_schedule
                   SET next_run_at = ?, lease_owner = NULL, lease_until = NULL,
                       active_campaign_id = NULL, consecutive_failures = ?,
                       circuit_open = ?, circuit_reason = ?, updated_at = ?
                   WHERE singleton = 1 AND active_campaign_id = ?""",
                (
                    iso(next_run),
                    failures,
                    int(circuit_open),
                    message[:1_000] if circuit_open else None,
                    iso(now),
                    campaign_id,
                ),
            )
            connection.commit()
            return next_run

    def interrupt(
        self,
        campaign_id: str,
        message: str,
        *,
        owner: str,
        now: datetime,
    ) -> bool:
        """Persist an operator shutdown without disturbing a replacement owner.

        A live campaign is released with its existing due time so a restarted
        daemon may resume immediately.  If ownership was already lost, only
        this campaign's still-running history row is terminalized.
        """
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            schedule = connection.execute(
                """SELECT active_campaign_id, lease_owner, lease_until
                   FROM daily_audit_schedule WHERE singleton = 1"""
            ).fetchone()
            owns_live_lease = bool(
                schedule
                and schedule["active_campaign_id"] == campaign_id
                and schedule["lease_owner"] == owner
                and schedule["lease_until"]
                and parse_time(schedule["lease_until"]) > now
            )
            connection.execute(
                """UPDATE daily_audit_campaign
                   SET status = 'interrupted', finished_at = COALESCE(finished_at, ?),
                       summary = ?
                   WHERE id = ? AND status = 'running'""",
                (iso(now), message, campaign_id),
            )
            if owns_live_lease:
                connection.execute(
                    """UPDATE daily_audit_schedule
                       SET lease_owner = NULL, lease_until = NULL,
                           active_campaign_id = NULL, updated_at = ?
                       WHERE singleton = 1 AND active_campaign_id = ?
                         AND lease_owner = ?""",
                    (iso(now), campaign_id, owner),
                )
            connection.commit()
            return owns_live_lease

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
                """SELECT active_campaign_id, circuit_open
                   FROM daily_audit_schedule WHERE singleton = 1"""
            ).fetchone()
            if row and (row["active_campaign_id"] or row["circuit_open"]):
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

    def reset_circuit(self, *, now: datetime, interval: timedelta) -> bool:
        """Explicitly re-arm an idle scheduler for its next normal interval."""
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
                   SET next_run_at = ?, consecutive_failures = 0,
                       circuit_open = 0, circuit_reason = NULL, updated_at = ?
                   WHERE singleton = 1""",
                (iso(now + interval), iso(now)),
            )
            connection.commit()
            return True

    def enforce_failure_limit(self, *, max_consecutive_failures: int, now: datetime) -> None:
        """Migrate an already failing schedule into the fail-closed state."""
        with self._connect() as connection:
            connection.execute(
                """UPDATE daily_audit_schedule
                   SET circuit_open = 1,
                       circuit_reason = COALESCE(
                           circuit_reason,
                           'Consecutive failure limit reached before circuit migration.'
                       ),
                       updated_at = ?
                   WHERE singleton = 1 AND active_campaign_id IS NULL
                     AND consecutive_failures >= ?""",
                (iso(now), max(1, max_consecutive_failures)),
            )

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
        self.store.enforce_failure_limit(
            max_consecutive_failures=self.config.max_consecutive_failures,
            now=self.clock(),
        )
        self._stop_requested = threading.Event()
        # RLock avoids self-deadlock if Python dispatches SIGTERM/SIGINT while
        # the daemon's main thread is inside one of these tiny critical sections.
        self._active_abort_lock = threading.RLock()
        self._active_abort_event: threading.Event | None = None

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested.is_set()

    def request_stop(self) -> None:
        """Request bounded cancellation of the currently active campaign."""
        self._stop_requested.set()
        with self._active_abort_lock:
            if self._active_abort_event is not None:
                self._active_abort_event.set()

    def tick(self) -> CampaignResult | None:
        if not self.config.enabled or self._stop_requested.is_set():
            return None
        now = self.clock()
        campaign_id = self.store.acquire_due(
            now=now, owner=self.owner, lease_duration=self.config.lease_duration
        )
        if campaign_id is None:
            return None
        heartbeat_stop = threading.Event()
        campaign_abort = threading.Event()
        with self._active_abort_lock:
            self._active_abort_event = campaign_abort
            if self._stop_requested.is_set():
                campaign_abort.set()

        def lease_check() -> bool:
            if campaign_abort.is_set():
                return False
            try:
                owned = self.store.owns_lease(
                    campaign_id,
                    owner=self.owner,
                    now=self.clock(),
                )
            except Exception:  # noqa: BLE001 - a fencing read must fail closed
                campaign_abort.set()
                return False
            if not owned:
                campaign_abort.set()
            return owned

        request = CampaignRequest(
            campaign_id=campaign_id,
            repository_path=self.config.repository_path,
            max_remediation_rounds=self.config.max_remediation_rounds,
            merge_mode=self.config.merge_mode,
            validation_commands=self.config.validation_commands,
            run_timeout_seconds=self.config.run_timeout_seconds,
            git_timeout_seconds=self.config.git_timeout_seconds,
            validation_timeout_seconds=self.config.validation_timeout_seconds,
            completion_timeout_seconds=self.config.completion_timeout_seconds,
            provider_id=self.config.provider_id,
            transport_retry_attempts=self.config.transport_retry_attempts,
            transport_retry_base_seconds=self.config.transport_retry_base_seconds,
            lease_owner=self.owner,
            abort_event=campaign_abort,
            lease_check=lease_check,
        )
        heartbeat_seconds = self.config.lease_heartbeat_seconds
        if heartbeat_seconds is None:
            heartbeat_seconds = min(60.0, self.config.lease_duration.total_seconds() / 3)
        heartbeat_seconds = max(0.01, heartbeat_seconds)

        def heartbeat() -> None:
            while not heartbeat_stop.wait(heartbeat_seconds):
                if campaign_abort.is_set():
                    return
                try:
                    renewed = self.store.renew_lease(
                        campaign_id,
                        owner=self.owner,
                        now=self.clock(),
                        lease_duration=self.config.lease_duration,
                    )
                except Exception:  # noqa: BLE001 - lost lease must fail closed
                    campaign_abort.set()
                    return
                if not renewed:
                    campaign_abort.set()
                    return

        heartbeat_thread = threading.Thread(
            target=heartbeat,
            name=f"daily-audit-lease-{campaign_id[:8]}",
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            result = self.backend.run(request)
            if campaign_abort.is_set():
                raise CampaignBackendError("Campaign scheduler lease was lost during execution.")
            if result.status == "completed" and not result.target_verified:
                result = CampaignResult(
                    status="failed",
                    summary="Backend claimed completion without target-branch verification.",
                    pull_request_url=result.pull_request_url,
                )
            finished = self.store.finish(
                campaign_id,
                result,
                owner=self.owner,
                now=self.clock(),
                interval=self.config.interval,
            )
            if not finished:
                raise CampaignBackendError("Campaign finished after losing its scheduler lease.")
            return result
        except Exception as exc:  # noqa: BLE001 - persist failure before retrying
            if self._stop_requested.is_set():
                summary = f"Campaign interrupted by service shutdown: {type(exc).__name__}: {exc}"
                self.store.interrupt(
                    campaign_id,
                    summary,
                    owner=self.owner,
                    now=self.clock(),
                )
                return CampaignResult(status="interrupted", summary=summary)
            self.store.fail(
                campaign_id,
                f"{type(exc).__name__}: {exc}",
                owner=self.owner,
                now=self.clock(),
                retry_at=getattr(exc, "retry_at", None),
                max_consecutive_failures=self.config.max_consecutive_failures,
            )
            raise
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=min(heartbeat_seconds, 1.0))
            with self._active_abort_lock:
                if self._active_abort_event is campaign_abort:
                    self._active_abort_event = None

    def status_json(self) -> str:
        return json.dumps(self.store.status(), ensure_ascii=False, indent=2)

    def reset_circuit(self) -> bool:
        return self.store.reset_circuit(now=self.clock(), interval=self.config.interval)
