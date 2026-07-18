"""SQLite runtime store for the v2 Session Supervisor.

Schema (Task 1:N Session, Session 1:N Run, Run 0..1 Report, Run 1:N RunEvent):

    task        -- one row per unit of work (mirrors, but does not replace, the
                   v1.2 Kanban task record)
    session     -- one row per Claude Code conversation. `session.id` *is* the
                   UUID passed to `claude --session-id`/`claude --resume`.
    run         -- one row per subprocess launch against a session. Mutable
                   current-state row, guarded by a `version` column for
                   compare-and-set updates and by an explicit state-transition
                   table (`ALLOWED_TRANSITIONS`) that refuses to move a run back
                   out of a terminal state.
    run_event   -- append-only. Every line of stream-json, every stderr line,
                   and every lifecycle event (process started/exited, cancel
                   requested, reconciliation outcome, ...) is one row here,
                   ordered by a per-run monotonic `seq`.
    report      -- at most one immutable row per run, pointing at the rendered
                   report file under `reports/<PROJECT>/...` (see `reports.py`).

Every write goes through `connect()`/`transaction()`, which open a fresh
connection per operation (safe under WAL: SQLite serializes writers itself, and
`busy_timeout` makes a writer wait rather than fail when another writer briefly
holds the lock) and keep transactions short and explicit. No transaction here
ever spans a subprocess call — the supervisor always closes out (commits) an
event write before it goes back to reading the next line of subprocess output.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, TypeVar

from command_center import storage
from command_center.models import iso_now, new_id


def _new_session_id() -> str:
    """A canonical, dashed UUID string (`8-4-4-4-12`) — unlike `models.new_id`
    (a bare 32-char hex digest, used for `task`/`run` ids which are never
    handed to the `claude` CLI), `session.id` *is* the value passed straight
    to `claude --session-id`/`claude --resume`, and `claude` rejects anything
    that isn't a valid UUID string (verified against the real CLI during
    Sprint 1 end-to-end validation)."""
    return str(uuid.uuid4())

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent.parent


def resolve_db_path(root: Path | None = None) -> Path:
    """`<data dir>/runtime.db`, honoring `AICC_DATA_DIR` like every other module."""
    data_dir = storage.resolve_data_dir(root or ROOT)
    return data_dir / "runtime.db"


# --------------------------------------------------------------------------
# Run / session states
# --------------------------------------------------------------------------

RUN_STATES: list[str] = [
    "PREPARED",
    "QUEUED",
    "RUNNING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "INTERRUPTED",
    "UNKNOWN",
]

TERMINAL_STATES: frozenset[str] = frozenset({"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED", "UNKNOWN"})

EXECUTION_CENTER_ACTIVE_STATES: frozenset[str] = frozenset({"PREPARED", "QUEUED", "RUNNING"})

# Explicit allow-list of state transitions. Anything not listed here is refused
# by `update_run_state` before it ever reaches SQL — in particular, no terminal
# state can transition anywhere, so a terminal run can never be silently moved
# back to RUNNING (or anywhere else).
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "PREPARED": frozenset({"QUEUED", "CANCELLED", "FAILED"}),
    "QUEUED": frozenset({"RUNNING", "CANCELLED", "FAILED"}),
    "RUNNING": frozenset({"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED", "UNKNOWN"}),
    "COMPLETED": frozenset(),
    "FAILED": frozenset(),
    "CANCELLED": frozenset(),
    "INTERRUPTED": frozenset(),
    "UNKNOWN": frozenset(),
}


class DatabaseBusyTimeoutError(Exception):
    """Raised when a genuine SQLITE_BUSY/SQLITE_LOCKED condition did not clear
    within `_BUSY_RETRY_DEADLINE_SECONDS` of retrying (see `_retry_on_busy`).
    Wraps (`raise ... from`) the original `sqlite3.OperationalError`."""


class InvalidTransitionError(Exception):
    """Raised when a run-state transition is not in `ALLOWED_TRANSITIONS`."""


class LostUpdateError(Exception):
    """Raised when a compare-and-set update loses the race (version mismatch)."""


class UnknownRunFieldError(Exception):
    """Raised when `update_run_state`/`update_run_fields` is asked to set a
    column that isn't in `_UPDATABLE_RUN_FIELDS`."""


# The only `run` columns `update_run_state`/`update_run_fields` may set via
# their `fields` dict. `id`/`session_id`/`task_id`/`sequence`/`is_resume`/
# `project`/`task_type`/`repository_path`/`prompt`/`command_json`/
# `timeout_seconds`/`created_at` are write-once (set only by `create_run`);
# `state`/`version`/`updated_at` are handled explicitly by the two update
# functions themselves, never via the caller-supplied `fields` dict. This is
# an allowlist checked *before* any SQL is built — every caller today passes
# fixed literal keys, so this is defense in depth (F6), not a fix for a
# reachable injection today, but it turns "a future caller forwards an
# unexpected key" into an immediate, clear exception instead of a dynamically
# constructed `UPDATE ... SET <key> = ...` clause.
_UPDATABLE_RUN_FIELDS: frozenset[str] = frozenset(
    {
        "pid",
        "process_start_identity",
        "pre_run_git_status",
        "post_run_git_status",
        "working_tree_changed",
        "exit_code",
        "cancel_requested",
        "cancel_requested_at",
        "started_at",
        "completed_at",
        "failure_reason",
        # v2 Live Execution Center fields (migration 3) — commit_hash/
        # pull_request_url are the only ones ever set post-create (once, at
        # terminal-state task sync, via `set_run_result_fields`);
        # expected_branch/launch_source/prompt_version are write-once at
        # `create_run` time and never updated afterward, but are still listed
        # here as a defense-in-depth allowlist entry like every other column.
        "commit_hash",
        "pull_request_url",
    }
)


def _validate_updatable_fields(fields: dict) -> None:
    unknown = set(fields) - _UPDATABLE_RUN_FIELDS
    if unknown:
        raise UnknownRunFieldError(f"Not an updatable run field: {sorted(unknown)}")


# --------------------------------------------------------------------------
# Schema (idempotent — every statement is `IF NOT EXISTS`, so re-running the
# full script after a partially-applied migration is always safe)
# --------------------------------------------------------------------------

SCHEMA_VERSION = 3

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS task (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    title TEXT NOT NULL,
    task_type TEXT NOT NULL,
    legacy_task_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES task(id) ON DELETE CASCADE,
    project TEXT NOT NULL,
    repository_path TEXT NOT NULL,
    legacy_run_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_session_task_id ON session(task_id);

CREATE TABLE IF NOT EXISTS run (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL REFERENCES task(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    is_resume INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL,
    project TEXT NOT NULL,
    task_type TEXT NOT NULL,
    repository_path TEXT NOT NULL,
    prompt TEXT NOT NULL,
    command_json TEXT,
    timeout_seconds INTEGER,
    pid INTEGER,
    process_start_identity TEXT,
    pre_run_git_status TEXT,
    post_run_git_status TEXT,
    working_tree_changed INTEGER,
    exit_code INTEGER,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    cancel_requested_at TEXT,
    started_at TEXT,
    completed_at TEXT,
    version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_run_session_id ON run(session_id);
CREATE INDEX IF NOT EXISTS idx_run_task_id ON run(task_id);
CREATE INDEX IF NOT EXISTS idx_run_state ON run(state);

CREATE TABLE IF NOT EXISTS run_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES run(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_run_event_run_id ON run_event(run_id);

CREATE TABLE IF NOT EXISTS report (
    run_id TEXT PRIMARY KEY REFERENCES run(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _migration_2_add_failure_reason(conn: sqlite3.Connection) -> None:
    """Adds `run.failure_reason` (machine-readable terminal-state detail, e.g.
    `"timeout"` — see `supervisor.py`'s watchdog). `ALTER TABLE ADD COLUMN`
    isn't naturally idempotent like `CREATE TABLE IF NOT EXISTS`, so this
    checks `PRAGMA table_info` first, making it safe to re-run against a db
    where migration 2 was already (fully or partially) applied.

    The check-then-add is wrapped in one `transaction()` (`BEGIN IMMEDIATE`)
    so it is also safe under genuine concurrent execution, not just
    sequential re-runs: `BEGIN IMMEDIATE` takes the write lock for the whole
    check+`ALTER TABLE`, so a second process racing the same migration
    always sees either "not yet added, lock free" (and adds it) or blocks
    until the first process's transaction commits, then sees "already
    added" and correctly skips it — never both processes deciding
    concurrently that the column is missing and both trying to add it
    (which previously surfaced as an unretried `sqlite3.OperationalError:
    duplicate column name: failure_reason`, distinct from — and not fixed
    by — the busy/locked retry in `_retry_on_busy`, since a genuine
    duplicate-column conflict is `SQLITE_ERROR`, not `SQLITE_BUSY`, and must
    never be retried)."""
    with transaction(conn):
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(run)").fetchall()}
        if "failure_reason" not in existing:
            conn.execute("ALTER TABLE run ADD COLUMN failure_reason TEXT")


def _migration_3_add_live_execution_center_v2_fields(conn: sqlite3.Connection) -> None:
    """Adds the Live Execution Center v2 columns (see `docs/adr` for the
    Increment 2 brief): `expected_branch` (resolved once at launch time —
    task branch, else project default branch, else NULL — and never
    recomputed afterward, so it can't drift if project config changes mid-
    run), `launch_source` (`"kanban_task"` or `"execution_center_adhoc"`),
    `prompt_version` (the launching task's `prompt_version` at launch time,
    or NULL for an ad-hoc run), and `commit_hash`/`pull_request_url`
    (populated once, at terminal-state task sync, by parsing the run's final
    result text with the existing `report_parser` — see `task_sync.py`).

    Same idempotent check-then-`ALTER TABLE ADD COLUMN` shape as migration 2
    (`_migration_2_add_failure_reason`) — safe to re-run against a db where
    this migration was already (fully or partially) applied, and safe under
    genuine concurrent execution via the same `BEGIN IMMEDIATE` transaction.
    """
    with transaction(conn):
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(run)").fetchall()}
        for column in ("expected_branch", "launch_source", "commit_hash", "pull_request_url"):
            if column not in existing:
                conn.execute(f"ALTER TABLE run ADD COLUMN {column} TEXT")
        if "prompt_version" not in existing:
            conn.execute("ALTER TABLE run ADD COLUMN prompt_version INTEGER")


# Each migration is either a raw SQL script (applied via `executescript`, every
# statement `IF NOT EXISTS`) or a callable(conn) for changes — like `ALTER
# TABLE ADD COLUMN` — that need their own idempotency check.
MIGRATIONS: list[tuple[int, str | Callable[[sqlite3.Connection], None]]] = [
    (1, _SCHEMA_V1),
    (2, _migration_2_add_failure_reason),
    (3, _migration_3_add_live_execution_center_v2_fields),
]


# --------------------------------------------------------------------------
# Busy/locked retry — for the narrow class of statements that can return
# SQLITE_BUSY as a single immediate failure *without* looping through the
# connection's own busy handler (see `_retry_on_busy`'s docstring).
# --------------------------------------------------------------------------

_BUSY_RETRY_DEADLINE_SECONDS = 30.0
_BUSY_RETRY_INITIAL_SLEEP_SECONDS = 0.01
_BUSY_RETRY_MAX_SLEEP_SECONDS = 0.5

_T = TypeVar("_T")


def _is_busy_or_locked(exc: sqlite3.OperationalError) -> bool:
    """True only for a genuine SQLITE_BUSY/SQLITE_LOCKED (or an extended code
    in either family, e.g. SQLITE_BUSY_SNAPSHOT) condition — never for an
    unrelated `OperationalError` (bad SQL, missing table, unreadable file,
    ...), which must always propagate immediately, unretried."""
    name = getattr(exc, "sqlite_errorname", None)
    if name is not None:
        return name.startswith("SQLITE_BUSY") or name.startswith("SQLITE_LOCKED")
    # `sqlite_errorname`/`sqlite_errorcode` are only populated on Python's
    # sqlite3 module for 3.11+; fall back to matching the two known
    # busy/locked message shapes for older interpreters.
    msg = str(exc).lower()
    return "database is locked" in msg or "database table is locked" in msg


def _retry_on_busy(fn: Callable[[], _T], *, deadline_seconds: float = _BUSY_RETRY_DEADLINE_SECONDS) -> _T:
    """Call `fn()` until it succeeds, the deadline elapses, or it raises an
    `OperationalError` that is not a busy/locked condition (re-raised
    immediately, never retried).

    Exists because a handful of statements — chief among them `PRAGMA
    journal_mode=WAL` when multiple processes race to initialize WAL mode on
    a database file that does not yet exist — can return SQLITE_BUSY as one
    immediate failure without the connection's own `busy_timeout` ever
    retrying it. This was confirmed empirically, not assumed: reading back
    `PRAGMA busy_timeout` immediately before the failing call shows it
    already correctly configured (30000ms) at the moment `PRAGMA
    journal_mode=WAL` still raises `sqlite3.OperationalError: database is
    locked` with `sqlite_errorcode == sqlite3.SQLITE_BUSY`. Ordinary reads/
    writes made inside a `BEGIN IMMEDIATE` transaction (see `transaction()`)
    do not need this wrapper — busy_timeout already retries those reliably,
    as proven by the multi-thread/multi-process writer tests in
    `tests/test_runtime_db.py`, which never exercise this path and never
    flake. This wrapper is applied only to the non-transactional, DDL-shaped
    statements executed during connection setup and first-time migration —
    the earliest first-open/first-DDL race, not later INSERT conflicts
    (those are already handled by `migrate()`'s
    `except sqlite3.IntegrityError` on the `schema_version` insert).
    """
    deadline = time.monotonic() + deadline_seconds
    sleep_seconds = _BUSY_RETRY_INITIAL_SLEEP_SECONDS
    last_exc: sqlite3.OperationalError | None = None
    while True:
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            if not _is_busy_or_locked(exc):
                raise
            last_exc = exc
            if time.monotonic() >= deadline:
                break
            time.sleep(min(sleep_seconds, max(deadline - time.monotonic(), 0)))
            sleep_seconds = min(sleep_seconds * 2, _BUSY_RETRY_MAX_SLEEP_SECONDS)
    raise DatabaseBusyTimeoutError(
        f"Gave up waiting for a SQLite busy/locked condition to clear after {deadline_seconds}s"
    ) from last_exc


# --------------------------------------------------------------------------
# Connection / transaction management
# --------------------------------------------------------------------------


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    """One connection for one operation. Sets WAL, busy_timeout, foreign_keys
    on every connection (WAL persists in the file after the first time, but the
    other two are per-connection settings, so they must be reapplied here).

    The three PRAGMAs are each wrapped in `_retry_on_busy`: `journal_mode=WAL`
    in particular is the statement proven to race unretried under concurrent
    first-time database creation (see `_retry_on_busy`'s docstring) — this is
    what makes opening a connection safe even when several independent
    processes call `connect()` against the same not-yet-existent db file at
    the same instant.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        _retry_on_busy(lambda: conn.execute("PRAGMA journal_mode=WAL"))
        _retry_on_busy(lambda: conn.execute("PRAGMA busy_timeout=30000"))
        _retry_on_busy(lambda: conn.execute("PRAGMA foreign_keys=ON"))
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """A short, explicit BEGIN IMMEDIATE / COMMIT (or ROLLBACK on error).

    BEGIN IMMEDIATE takes the write lock up front, so concurrent writers
    serialize here (retrying under `busy_timeout` instead of racing) rather
    than failing with SQLITE_BUSY the way a deferred transaction could.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


_SCHEMA_VERSION_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
)


def migrate(db_path: Path) -> None:
    """Apply every migration newer than the recorded schema version.

    Idempotent in two senses: every SQL-script migration statement is `IF NOT
    EXISTS` (safe to re-run a partially-applied migration), and
    `schema_version.version` is itself a `PRIMARY KEY` — if two processes
    both start up against a brand-new db file at once and both decide
    migration N still needs applying, the loser's `INSERT` raises
    `sqlite3.IntegrityError`, which is caught and treated as "someone else
    already recorded this migration", not an error (see F5/F8: this is a
    real scenario, not hypothetical — two separate CLI invocations against
    the same fresh db file race here in practice).

    The `CREATE TABLE IF NOT EXISTS schema_version` and each migration's own
    DDL (`executescript`/callable `step`) run outside any explicit
    transaction (autocommit, like the PRAGMAs in `connect()`) and are
    therefore wrapped in `_retry_on_busy` too — this is the "earliest
    first-DDL race" (as opposed to the `schema_version` INSERT race just
    above, which was already handled): two processes racing to create
    `schema_version` or apply migration 1's `CREATE TABLE`s for the very
    first time on a brand-new file can hit the same unretried-SQLITE_BUSY
    behavior `connect()`'s docstring describes for `journal_mode=WAL`.
    """
    with connect(db_path) as conn:
        _retry_on_busy(lambda: conn.execute(_SCHEMA_VERSION_TABLE_SQL))
        row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        current = row["v"] if row and row["v"] is not None else 0
        for version, step in MIGRATIONS:
            if version <= current:
                continue
            if callable(step):
                _retry_on_busy(lambda step=step: step(conn))
            else:
                _retry_on_busy(lambda step=step: conn.executescript(step))
            try:
                with transaction(conn):
                    conn.execute(
                        "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                        (version, iso_now()),
                    )
            except sqlite3.IntegrityError:
                pass  # another process already recorded this migration version
            current = version


def current_schema_version(db_path: Path) -> int:
    with connect(db_path) as conn:
        _retry_on_busy(lambda: conn.execute(_SCHEMA_VERSION_TABLE_SQL))
        row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        return row["v"] if row and row["v"] is not None else 0


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


# --------------------------------------------------------------------------
# Task
# --------------------------------------------------------------------------


def create_task(
    db_path: Path,
    *,
    project: str,
    title: str,
    task_type: str,
    task_id: str | None = None,
    legacy_task_id: str | None = None,
) -> dict:
    now = iso_now()
    record = {
        "id": task_id or new_id(),
        "project": project,
        "title": title,
        "task_type": task_type,
        "legacy_task_id": legacy_task_id,
        "created_at": now,
        "updated_at": now,
    }
    with connect(db_path) as conn:
        with transaction(conn):
            conn.execute(
                """INSERT INTO task (id, project, title, task_type, legacy_task_id, created_at, updated_at)
                   VALUES (:id, :project, :title, :task_type, :legacy_task_id, :created_at, :updated_at)""",
                record,
            )
    return record


def get_task(db_path: Path, task_id: str) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM task WHERE id = ?", (task_id,)).fetchone()
        return _row_to_dict(row)


def list_tasks(db_path: Path, *, project: str | None = None) -> list[dict]:
    with connect(db_path) as conn:
        if project:
            rows = conn.execute(
                "SELECT * FROM task WHERE project = ? ORDER BY created_at DESC", (project,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM task ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]


# --------------------------------------------------------------------------
# Session
# --------------------------------------------------------------------------


def create_session(
    db_path: Path,
    *,
    task_id: str,
    project: str,
    repository_path: str,
    session_id: str | None = None,
    legacy_run_id: str | None = None,
) -> dict:
    now = iso_now()
    record = {
        "id": session_id or _new_session_id(),
        "task_id": task_id,
        "project": project,
        "repository_path": repository_path,
        "legacy_run_id": legacy_run_id,
        "created_at": now,
        "updated_at": now,
    }
    with connect(db_path) as conn:
        with transaction(conn):
            conn.execute(
                """INSERT INTO session (id, task_id, project, repository_path, legacy_run_id, created_at, updated_at)
                   VALUES (:id, :task_id, :project, :repository_path, :legacy_run_id, :created_at, :updated_at)""",
                record,
            )
    return record


def get_session(db_path: Path, session_id: str) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM session WHERE id = ?", (session_id,)).fetchone()
        return _row_to_dict(row)


def list_sessions(db_path: Path, *, task_id: str | None = None) -> list[dict]:
    with connect(db_path) as conn:
        if task_id:
            rows = conn.execute(
                "SELECT * FROM session WHERE task_id = ? ORDER BY created_at DESC", (task_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM session ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------


def create_run(
    db_path: Path,
    *,
    session_id: str,
    task_id: str,
    project: str,
    task_type: str,
    repository_path: str,
    prompt: str,
    is_resume: bool,
    timeout_seconds: int | None = None,
    command: list[str] | None = None,
    run_id: str | None = None,
    expected_branch: str | None = None,
    launch_source: str | None = None,
    prompt_version: int | None = None,
) -> dict:
    """`expected_branch`/`launch_source`/`prompt_version` are write-once, like
    `project`/`task_type`/`repository_path` above — resolved by the caller
    once at launch time and never recomputed or overwritten afterward (they
    are deliberately absent from `_UPDATABLE_RUN_FIELDS`)."""
    with connect(db_path) as conn:
        with transaction(conn):
            row = conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_seq FROM run WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            sequence = row["next_seq"]
            now = iso_now()
            record = {
                "id": run_id or new_id(),
                "session_id": session_id,
                "task_id": task_id,
                "sequence": sequence,
                "is_resume": 1 if is_resume else 0,
                "state": "PREPARED",
                "project": project,
                "task_type": task_type,
                "repository_path": repository_path,
                "prompt": prompt,
                "command_json": json.dumps(command, ensure_ascii=False) if command is not None else None,
                "timeout_seconds": timeout_seconds,
                "pid": None,
                "process_start_identity": None,
                "pre_run_git_status": None,
                "post_run_git_status": None,
                "working_tree_changed": None,
                "exit_code": None,
                "cancel_requested": 0,
                "cancel_requested_at": None,
                "started_at": None,
                "completed_at": None,
                "expected_branch": expected_branch,
                "launch_source": launch_source,
                "prompt_version": prompt_version,
                "commit_hash": None,
                "pull_request_url": None,
                "version": 0,
                "created_at": now,
                "updated_at": now,
            }
            conn.execute(
                """INSERT INTO run (
                    id, session_id, task_id, sequence, is_resume, state, project, task_type,
                    repository_path, prompt, command_json, timeout_seconds, pid,
                    process_start_identity, pre_run_git_status, post_run_git_status,
                    working_tree_changed, exit_code, cancel_requested, cancel_requested_at,
                    started_at, completed_at, expected_branch, launch_source, prompt_version,
                    commit_hash, pull_request_url, version, created_at, updated_at
                ) VALUES (
                    :id, :session_id, :task_id, :sequence, :is_resume, :state, :project, :task_type,
                    :repository_path, :prompt, :command_json, :timeout_seconds, :pid,
                    :process_start_identity, :pre_run_git_status, :post_run_git_status,
                    :working_tree_changed, :exit_code, :cancel_requested, :cancel_requested_at,
                    :started_at, :completed_at, :expected_branch, :launch_source, :prompt_version,
                    :commit_hash, :pull_request_url, :version, :created_at, :updated_at
                )""",
                record,
            )
    return record


def get_run(db_path: Path, run_id: str) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM run WHERE id = ?", (run_id,)).fetchone()
        return _row_to_dict(row)


def list_runs(
    db_path: Path,
    *,
    session_id: str | None = None,
    task_id: str | None = None,
    state: str | None = None,
    states: Iterable[str] | None = None,
    limit: int | None = None,
) -> list[dict]:
    """`state` (singular, exact match) and `states` (plural, `IN (...)`) are mutually
    exclusive — passing both raises `ValueError` before any SQL is built, so a caller
    can never end up with an ambiguous, silently-ORed filter. `limit`, if given, is
    applied as a SQL `LIMIT` after the existing `ORDER BY created_at DESC`, bounding
    the result set inside SQLite rather than truncating a full-table fetch in Python;
    a negative `limit` raises `ValueError` before any SQL runs (SQLite's own
    `LIMIT -1` means "unlimited," which would silently defeat the bound this
    parameter exists to provide) — `limit=0` remains a valid request that returns
    `[]`.
    """
    if state is not None and states is not None:
        raise ValueError("Pass either `state` or `states`, not both.")
    if limit is not None and limit < 0:
        raise ValueError(f"limit must be >= 0, got {limit!r}")
    clauses = []
    params: list[Any] = []
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)
    if task_id:
        clauses.append("task_id = ?")
        params.append(task_id)
    if state:
        clauses.append("state = ?")
        params.append(state)
    if states is not None:
        states_list = list(states)
        if states_list:
            placeholders = ", ".join("?" for _ in states_list)
            clauses.append(f"state IN ({placeholders})")
            params.extend(states_list)
        else:
            clauses.append("0")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    limit_clause = " LIMIT ?" if limit is not None else ""
    if limit is not None:
        params.append(limit)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM run {where} ORDER BY created_at DESC{limit_clause}", params
        ).fetchall()
        return [dict(row) for row in rows]


def update_run_state(
    db_path: Path,
    run_id: str,
    *,
    expected_version: int,
    new_state: str,
    fields: dict | None = None,
) -> dict:
    """Compare-and-set transition of `run.state`.

    Raises `InvalidTransitionError` if `new_state` is not reachable from the
    run's *current* state (fetched fresh, inside the same transaction) —
    this is what keeps a terminal state from ever moving anywhere, including
    back to RUNNING, regardless of what `expected_version` the caller has.
    Raises `LostUpdateError` if `expected_version` no longer matches (someone
    else updated the row first) — the caller must re-read and decide whether
    to retry.
    """
    if new_state not in RUN_STATES:
        raise ValueError(f"Unknown run state: {new_state!r}")
    fields = dict(fields or {})
    _validate_updatable_fields(fields)
    with connect(db_path) as conn:
        with transaction(conn):
            row = conn.execute("SELECT * FROM run WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(f"No such run: {run_id!r}")
            current_state = row["state"]
            allowed = ALLOWED_TRANSITIONS.get(current_state, frozenset())
            if new_state not in allowed:
                raise InvalidTransitionError(
                    f"Run {run_id!r} cannot transition {current_state!r} -> {new_state!r}"
                )
            if row["version"] != expected_version:
                raise LostUpdateError(
                    f"Run {run_id!r} version mismatch: expected {expected_version}, actual {row['version']}"
                )
            fields["state"] = new_state
            fields["updated_at"] = iso_now()
            set_clause = ", ".join(f"{key} = :{key}" for key in fields)
            params = dict(fields)
            params["run_id"] = run_id
            params["expected_version"] = expected_version
            cur = conn.execute(
                f"""UPDATE run SET {set_clause}, version = version + 1
                    WHERE id = :run_id AND version = :expected_version""",
                params,
            )
            if cur.rowcount != 1:
                # Should be unreachable (we just read this row in the same
                # transaction), but never silently succeed if it happens.
                raise LostUpdateError(f"Run {run_id!r} update affected {cur.rowcount} rows")
            updated = conn.execute("SELECT * FROM run WHERE id = ?", (run_id,)).fetchone()
            return dict(updated)


def set_run_result_fields(
    db_path: Path,
    run_id: str,
    *,
    expected_version: int,
    commit_hash: str | None = None,
    pull_request_url: str | None = None,
) -> dict:
    """Thin `update_run_fields` wrapper for the one terminal-sync write site
    (`task_sync.sync_task_from_run`) — populates the two fields deterministic
    report parsing can extract, once, at terminal state."""
    return update_run_fields(
        db_path,
        run_id,
        expected_version=expected_version,
        fields={"commit_hash": commit_hash, "pull_request_url": pull_request_url},
    )


def update_run_fields(db_path: Path, run_id: str, *, expected_version: int, fields: dict) -> dict:
    """Compare-and-set update of non-state fields (e.g. recording a PID right
    after Popen succeeds, before any state transition). Does not touch `state`."""
    fields = dict(fields)
    fields.pop("state", None)
    fields.pop("version", None)
    _validate_updatable_fields(fields)
    with connect(db_path) as conn:
        with transaction(conn):
            row = conn.execute("SELECT version FROM run WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(f"No such run: {run_id!r}")
            if row["version"] != expected_version:
                raise LostUpdateError(
                    f"Run {run_id!r} version mismatch: expected {expected_version}, actual {row['version']}"
                )
            fields["updated_at"] = iso_now()
            set_clause = ", ".join(f"{key} = :{key}" for key in fields)
            params = dict(fields)
            params["run_id"] = run_id
            params["expected_version"] = expected_version
            cur = conn.execute(
                f"""UPDATE run SET {set_clause}, version = version + 1
                    WHERE id = :run_id AND version = :expected_version""",
                params,
            )
            if cur.rowcount != 1:
                raise LostUpdateError(f"Run {run_id!r} update affected {cur.rowcount} rows")
            updated = conn.execute("SELECT * FROM run WHERE id = ?", (run_id,)).fetchone()
            return dict(updated)


# --------------------------------------------------------------------------
# RunEvent (append-only)
# --------------------------------------------------------------------------


def append_run_event(db_path: Path, run_id: str, event_type: str, payload: dict) -> int:
    """Append one event and return its per-run sequence number.

    The next `seq` is computed and inserted inside one `BEGIN IMMEDIATE`
    transaction, so concurrent writers (the stdout reader thread and the
    stderr reader thread for the same run) serialize on SQLite's write lock
    rather than racing on the sequence number in application memory.
    """
    payload_json = json.dumps(payload, ensure_ascii=False)
    now = iso_now()
    with connect(db_path) as conn:
        with transaction(conn):
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM run_event WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            seq = row["next_seq"]
            conn.execute(
                """INSERT INTO run_event (run_id, seq, event_type, payload_json, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (run_id, seq, event_type, payload_json, now),
            )
            return seq


def list_run_events(
    db_path: Path, run_id: str, *, after_seq: int = 0, limit: int = 1000, event_type: str | None = None
) -> list[dict]:
    """`event_type`, if given, filters in SQL (e.g. `"lifecycle"` for
    `log_tail.session_timeline`) — bounded by `limit` the same way an
    unfiltered call is, never a full-table read followed by an in-Python
    filter."""
    with connect(db_path) as conn:
        if event_type is not None:
            rows = conn.execute(
                """SELECT run_id, seq, event_type, payload_json, created_at FROM run_event
                   WHERE run_id = ? AND seq > ? AND event_type = ? ORDER BY seq ASC LIMIT ?""",
                (run_id, after_seq, event_type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT run_id, seq, event_type, payload_json, created_at FROM run_event
                   WHERE run_id = ? AND seq > ? ORDER BY seq ASC LIMIT ?""",
                (run_id, after_seq, limit),
            ).fetchall()
        events = []
        for row in rows:
            event = dict(row)
            event["payload"] = json.loads(event.pop("payload_json"))
            events.append(event)
        return events


def tail_run_events(db_path: Path, run_id: str, *, limit: int = 200) -> list[dict]:
    """Bounded log tail: the *last* `limit` events for a run, oldest-first —
    never the whole table. Orders `DESC` (so SQLite can stop after `limit`
    rows without scanning every event this run has ever produced) and
    reverses in Python before returning, so callers see them in the same
    chronological order `list_run_events` already returns."""
    with connect(db_path) as conn:
        rows = conn.execute(
            """SELECT run_id, seq, event_type, payload_json, created_at FROM run_event
               WHERE run_id = ? ORDER BY seq DESC LIMIT ?""",
            (run_id, limit),
        ).fetchall()
        events = []
        for row in rows:
            event = dict(row)
            event["payload"] = json.loads(event.pop("payload_json"))
            events.append(event)
        events.reverse()
        return events


# --------------------------------------------------------------------------
# Report (immutable, at most one per run)
# --------------------------------------------------------------------------


def create_report(db_path: Path, run_id: str, path: str) -> dict:
    now = iso_now()
    with connect(db_path) as conn:
        with transaction(conn):
            conn.execute(
                "INSERT INTO report (run_id, path, created_at) VALUES (?, ?, ?)",
                (run_id, path, now),
            )
    return {"run_id": run_id, "path": path, "created_at": now}


def get_report(db_path: Path, run_id: str) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM report WHERE run_id = ?", (run_id,)).fetchone()
        return _row_to_dict(row)
