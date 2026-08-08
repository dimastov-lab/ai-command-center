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
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, TypeVar

from command_center import storage
from command_center.models import iso_now, new_id
from command_center.runtime import autonomy as autonomy_domain
from command_center.runtime import completion as completion_domain


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
#
# PREPARED/QUEUED both also allow INTERRUPTED/UNKNOWN — the same crash-
# recovery targets RUNNING already allowed — because `Supervisor.reconcile()`
# now inspects every `EXECUTION_CENTER_ACTIVE_STATES` row at startup, not just
# RUNNING ones: a Supervisor process can crash between `create_run` (PREPARED)
# or the QUEUED transition and the process actually being launched, leaving a
# row that would otherwise sit stuck "active" forever (and, since Sprint
# 2's workspace lock, forever blocking that workspace).
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "PREPARED": frozenset({"QUEUED", "CANCELLED", "FAILED", "INTERRUPTED", "UNKNOWN"}),
    "QUEUED": frozenset({"RUNNING", "CANCELLED", "FAILED", "INTERRUPTED", "UNKNOWN"}),
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


class InvalidCompletionTransitionError(Exception):
    """Raised when an `update_completion` call would move `completion_state`
    along an illegal edge (a backward jump, or any move out of a terminal
    state) — see `runtime.completion.COMPLETION_TRANSITIONS`. Same-state updates
    (retry metadata / evidence enrichment) are always permitted. This is the
    completion-pipeline analogue of `InvalidTransitionError` for `run.state`."""


class InvalidProposalTransitionError(Exception):
    """Raised when an `update_proposal` call would move `state` along an illegal
    edge (a backward jump, or any move out of a terminal state) — see
    `runtime.autonomy.PROPOSAL_TRANSITIONS`. This is the autonomy-proposal
    analogue of `InvalidCompletionTransitionError`."""


class ProposalFieldFrozenError(Exception):
    """Raised when proposal fields are mutated outside the lifecycle states in
    which they are authoritative. In particular, the action, policy, evidence
    digest, eligibility verdict, and execution plan are frozen after assessment."""


class ProposalEvidenceFrozenError(Exception):
    """Raised when evidence is appended after proposal assessment has begun."""


class LostUpdateError(Exception):
    """Raised when a compare-and-set update loses the race (version mismatch)."""


class WorkspaceLockedError(Exception):
    """Raised by `create_run(..., enforce_workspace_lock=True)` when another
    run is already active (`EXECUTION_CENTER_ACTIVE_STATES`) against the same
    `repository_path`. Carries the conflicting run so the caller can report
    it (id, state, task_id, ...) rather than just a message.

    The check-then-insert this guards against is done inside the *same*
    `BEGIN IMMEDIATE` transaction as the new row's `INSERT` (see
    `create_run`), not as a separate query beforehand — `BEGIN IMMEDIATE`
    takes SQLite's write lock up front, so two concurrent callers targeting
    the same workspace serialize here instead of racing: whichever commits
    first makes the row the second one's own conflict check will see."""

    def __init__(self, conflicting_run: dict) -> None:
        self.conflicting_run = conflicting_run
        super().__init__(
            f"Workspace {conflicting_run['repository_path']!r} already has an active run "
            f"({conflicting_run['id']!r}, state={conflicting_run['state']!r})."
        )


class TaskAlreadyActiveError(Exception):
    """Raised by `create_run(..., enforce_workspace_lock=True)` when the same
    `task_id` already has an active run (`EXECUTION_CENTER_ACTIVE_STATES`),
    possibly in a *different* workspace than this launch resolved to.

    The workspace lock alone (`WorkspaceLockedError`) only catches a second
    launch that resolves to the SAME `repository_path`; if a task's configured
    workspace/branch changes between two in-flight launches they can resolve to
    different paths and both slip past it. This check runs inside the same
    `BEGIN IMMEDIATE` transaction as the `INSERT`, so it is a true atomic
    per-task invariant rather than a raceable pre-flight. Carries the
    conflicting run for reporting."""

    def __init__(self, conflicting_run: dict) -> None:
        self.conflicting_run = conflicting_run
        super().__init__(
            f"Task {conflicting_run['task_id']!r} already has an active run "
            f"({conflicting_run['id']!r}, state={conflicting_run['state']!r})."
        )


class GlobalConcurrencyLimitError(Exception):
    """Raised by `create_run(..., max_global_concurrency=N)` when N runs are
    already active (`EXECUTION_CENTER_ACTIVE_STATES`) across *all* workspaces.

    Enforced inside `create_run`'s own `BEGIN IMMEDIATE` transaction (like the
    workspace lock above), so the global cap is a true atomic invariant every
    launch path shares — not an advisory pre-flight count that each entry point
    (the scheduler, the queue's `launch_ready`, portfolio launches, the review
    gate) has to remember to run and that a batch 'launch all READY' would race
    straight past."""

    def __init__(self, active_count: int, limit: int) -> None:
        self.active_count = active_count
        self.limit = limit
        super().__init__(
            f"Global concurrency limit reached ({active_count}/{limit} runs already active)."
        )


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
        # First moment the spawned process produced any output on stdout/
        # stderr — the "Claude startup/handshake" milestone, distinct from
        # `started_at` (the moment `Popen` returned a live PID). Written once,
        # best-effort, by the stdout/stderr reader threads (see
        # `supervisor._record_handshake`); its absence never fails a run.
        "first_output_at",
        # v2 Live Execution Center fields (migration 3) — commit_hash/
        # pull_request_url are the only ones ever set post-create (once, at
        # terminal-state task sync, via `set_run_result_fields`);
        # expected_branch/launch_source/prompt_version are write-once at
        # `create_run` time and never updated afterward, but are still listed
        # here as a defense-in-depth allowlist entry like every other column.
        "commit_hash",
        "pull_request_url",
        # Migration 11: short HEAD captured at launch so the post-run classifier
        # can tell "the agent committed" (HEAD advanced) from "the agent left the
        # tree clean without doing anything" — a committed change leaves the
        # working tree clean, so the porcelain-status diff alone under-counts
        # completed work (see `supervisor._supervise` and `outcome.classify_`).
        "pre_run_head",
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

SCHEMA_VERSION = 14

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


def _migration_4_add_first_output_at(conn: sqlite3.Connection) -> None:
    """Adds `run.first_output_at` (ISO timestamp of the first stdout/stderr
    line the spawned process produced — the "Claude startup/handshake"
    milestone, recorded once, best-effort, by the supervisor's reader
    threads; see `supervisor._record_handshake`).

    This column is the persisted signal that lets the display layer
    distinguish a run that has *spawned but not yet spoken* (a valid PID, no
    output yet — `session_view.STATUS_STARTING`) from one that is genuinely
    streaming output (`STATUS_RUNNING`), so a slow-to-handshake but perfectly
    healthy run is never surfaced as a failure. Its absence is never itself a
    failure — a run that exits cleanly having produced no stdout at all still
    leaves this NULL and is classified purely on its exit facts.

    Same idempotent check-then-`ALTER TABLE ADD COLUMN` shape as migrations 2
    and 3 — safe to re-run against a db where this migration was already
    (fully or partially) applied, and safe under genuine concurrent execution
    via the same `BEGIN IMMEDIATE` transaction.
    """
    with transaction(conn):
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(run)").fetchall()}
        if "first_output_at" not in existing:
            conn.execute("ALTER TABLE run ADD COLUMN first_output_at TEXT")


def _migration_9_add_execution_provider_fields(conn: sqlite3.Connection) -> None:
    """Persist the selected provider and its redacted, deterministic launch metadata."""
    with transaction(conn):
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(run)").fetchall()}
        if "provider_id" not in existing:
            conn.execute("ALTER TABLE run ADD COLUMN provider_id TEXT NOT NULL DEFAULT 'claude_code'")
        if "provider_metadata_json" not in existing:
            conn.execute("ALTER TABLE run ADD COLUMN provider_metadata_json TEXT")


def _migration_11_add_pre_run_head(conn: sqlite3.Connection) -> None:
    """Capture the short HEAD at launch so the post-run outcome classifier can
    distinguish "agent committed its work" (HEAD advanced, tree clean) from
    "agent did nothing" (HEAD unchanged, tree clean). Without this, any agent
    that commits — copilot_cli, claude_code — is mis-classified
    `incomplete:working_tree_unchanged` and re-run forever (AICC-DESKTOP-017).

    Same idempotent check-then-`ALTER TABLE ADD COLUMN` shape as migrations 2,
    3, 4, 9 — safe to re-run and safe under concurrent first-application via
    the `BEGIN IMMEDIATE` transaction in `migrate()`."""
    with transaction(conn):
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(run)").fetchall()}
        if "pre_run_head" not in existing:
            conn.execute("ALTER TABLE run ADD COLUMN pre_run_head TEXT")


# Autonomous completion pipeline (AICC-AUTONOMY-001). One `completion` row per
# run drives a *separate* state machine — the post-execution "is the engineering
# task actually merged into the target branch" lifecycle — distinct from and
# additive to the `run` row's execution state machine (which stays terminal once
# a process exits). It is a mutable current-state row, guarded by the same
# `version` compare-and-set column pattern as `run`. `completion_validation`
# records one row per validation command per attempt (bounded stdout/stderr
# summaries, never unbounded logs). `completion_event` is an append-only audit
# trail of the completion lifecycle (PR created, closed-unmerged, merged,
# target-branch verified, ...), ordered by a per-run monotonic `seq`, mirroring
# `run_event`.
_SCHEMA_V5 = """
CREATE TABLE IF NOT EXISTS completion (
    run_id TEXT PRIMARY KEY REFERENCES run(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL REFERENCES task(id) ON DELETE CASCADE,
    session_id TEXT REFERENCES session(id) ON DELETE CASCADE,
    project TEXT NOT NULL,
    repository_path TEXT NOT NULL,
    branch TEXT,
    base_branch TEXT,
    head_commit TEXT,
    remote TEXT,
    remote_branch TEXT,
    pull_request_number INTEGER,
    pull_request_url TEXT,
    pull_request_state TEXT,
    replaced_pull_request_number INTEGER,
    replaced_pull_request_url TEXT,
    merge_commit TEXT,
    merge_mode TEXT,
    merge_method TEXT,
    completion_state TEXT NOT NULL,
    last_reason_code TEXT,
    requires_human INTEGER NOT NULL DEFAULT 0,
    is_recoverable INTEGER NOT NULL DEFAULT 0,
    recommended_action TEXT,
    validation_summary TEXT,
    policy_json TEXT,
    last_checked_at TEXT,
    next_retry_at TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    recovery_count INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_completion_task_id ON completion(task_id);
CREATE INDEX IF NOT EXISTS idx_completion_state ON completion(completion_state);

CREATE TABLE IF NOT EXISTS completion_validation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES run(id) ON DELETE CASCADE,
    attempt INTEGER NOT NULL,
    command TEXT NOT NULL,
    exit_code INTEGER,
    started_at TEXT,
    finished_at TEXT,
    stdout_summary TEXT,
    stderr_summary TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_completion_validation_run_id ON completion_validation(run_id);

CREATE TABLE IF NOT EXISTS completion_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES run(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    reason_code TEXT,
    message TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_completion_event_run_id ON completion_event(run_id);
"""


# Autonomy proposal foundation (AICC-AUTONOMY-002). The pre-execution decision
# layer: a proposal is an evidence-backed, risk-classified suggestion that moves
# through the `runtime.autonomy` state machine under an explicit policy. It never
# executes anything itself — `dispatched_run_id`/`dispatched_task_id` only ever
# *record* an execution the caller performed through the existing routes.
#
#   proposal          -- one mutable current-state row per proposal, guarded by
#                        a `version` column (compare-and-set) and the
#                        `autonomy.is_valid_proposal_transition` structural guard.
#   proposal_evidence -- append-only, immutable. The observations a decision was
#                        made on; never updated, so the audit trail cannot be
#                        rewritten after the fact.
#   proposal_event    -- append-only audit trail, ordered by per-proposal `seq`.
_SCHEMA_V6 = """
CREATE TABLE IF NOT EXISTS proposal (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    project TEXT NOT NULL,
    task_id TEXT REFERENCES task(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    rationale TEXT NOT NULL,
    state TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    policy_json TEXT,
    eligibility_json TEXT,
    plan_json TEXT,
    evidence_digest TEXT,
    requires_human INTEGER NOT NULL DEFAULT 1,
    last_reason_code TEXT,
    decided_by TEXT,
    decision_reason TEXT,
    dispatched_run_id TEXT REFERENCES run(id) ON DELETE SET NULL,
    dispatched_task_id TEXT REFERENCES task(id) ON DELETE SET NULL,
    version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_proposal_state ON proposal(state);
CREATE INDEX IF NOT EXISTS idx_proposal_project ON proposal(project);
CREATE INDEX IF NOT EXISTS idx_proposal_task_id ON proposal(task_id);

CREATE TABLE IF NOT EXISTS proposal_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id TEXT NOT NULL REFERENCES proposal(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    summary TEXT,
    observed_at TEXT NOT NULL,
    is_blocker INTEGER NOT NULL DEFAULT 0,
    data_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(proposal_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_proposal_evidence_proposal_id ON proposal_evidence(proposal_id);

CREATE TABLE IF NOT EXISTS proposal_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id TEXT NOT NULL REFERENCES proposal(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT,
    actor TEXT,
    reason_code TEXT,
    message TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(proposal_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_proposal_event_proposal_id ON proposal_event(proposal_id);
"""


def _migration_7_add_proposal_parameters_json(conn: sqlite3.Connection) -> None:
    """Add the immutable, canonical action payload approved by a proposal.

    Existing schema-6 databases contain proposals without a structured action
    payload. Backfill those rows with the empty-object sentinel rather than
    NULL so every caller can safely parse `parameters_json` after migration.
    The check-and-add runs under the same write lock used by earlier callable
    migrations, making concurrent and repeated migration attempts safe.
    """
    with transaction(conn):
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(proposal)").fetchall()}
        if "parameters_json" not in existing:
            conn.execute(
                "ALTER TABLE proposal "
                "ADD COLUMN parameters_json TEXT NOT NULL DEFAULT '{}'"
            )


def _migration_8_add_independent_review_fields(conn: sqlite3.Connection) -> None:
    """Add the independent-review verdict to `completion`.

    The blocking review gate needs three facts per completion: which run
    produced the verdict, what the verdict was, and the reviewer's reasoning.
    They live on the completion row rather than in a side table because there is
    exactly one review outcome per completion and it is read on the same access
    path as every other completion field.

    Existing schema-7 databases keep NULLs, which read as "no verdict yet" — the
    same thing a brand-new row means. That is the safe direction: with the gate
    enabled, no verdict means *wait*, never *proceed*. The check-and-add runs
    under the same write lock as the earlier callable migrations, so concurrent
    and repeated migration attempts are safe.
    """
    with transaction(conn):
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(completion)").fetchall()}
        for column in ("review_verdict", "review_run_id", "review_summary"):
            if column not in existing:
                conn.execute(f"ALTER TABLE completion ADD COLUMN {column} TEXT")


_SCHEMA_V10 = """
-- ADR 0007 step 1: the execution queue's home in the execution-state store.
--
-- Mirrors `data/execution_queue.json`'s entry shape exactly, field for field,
-- because during the dual-write phases the two must be comparable without any
-- translation — a divergence check that had to normalise shapes first would be
-- checking its own translation as much as the data.
--
-- `task_id` is deliberately NOT a foreign key. The task lives in tasks.json,
-- which stays a human-editable file (see the ADR); the queue's reference to it
-- is advisory, and an entry whose task has vanished resolves to `cancelled`
-- with a reason, exactly as `evaluate_readiness` already does today.
CREATE TABLE IF NOT EXISTS queue_entry (
    id            TEXT PRIMARY KEY,
    task_id       TEXT,
    project       TEXT,
    state         TEXT NOT NULL,
    reason        TEXT,
    run_id        TEXT,
    added_at      TEXT,
    evaluated_at  TEXT,
    launched_at   TEXT,
    -- Preserves the JSON file's list order, which is load-bearing: the queue is
    -- displayed and planned in insertion order, and a set-shaped table would
    -- silently reorder it.
    position      INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_queue_entry_state ON queue_entry(state);
CREATE INDEX IF NOT EXISTS idx_queue_entry_task ON queue_entry(task_id);
"""

def _migration_12_add_executor_capability_fields(conn: sqlite3.Connection) -> None:
    """Adds the executor-capability columns (see the executor-capabilities
    brief): `capability_profile` (the granted profile — `READ_ONLY` /
    `WORKSPACE_WRITE`), `capability_override` (the normalized per-task override,
    or NULL), `required_capabilities` / `granted_capabilities` (comma-joined
    canonical tool lists), `capability_preflight` (`ok` / `mismatch`, the
    pre-spawn decision), and `command_policy` (a secret-free identity of the
    tool-permission policy the command encodes — profile + permission-mode +
    tool flag, never the prompt).

    All are write-once, resolved by the launcher at run-creation time. A run
    row that predates this migration keeps NULL for every one of them; readers
    (`session_view`, `reports`) treat NULL as "legacy / unknown" and fall back
    to deriving the profile from `task_type` deterministically, so legacy rows
    render a stable, safe default rather than crashing.

    Same idempotent check-then-`ALTER TABLE ADD COLUMN` shape as migrations 2
    and 3, wrapped in one `BEGIN IMMEDIATE` transaction — safe to re-run and
    safe under genuine concurrent execution.
    """
    with transaction(conn):
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(run)").fetchall()}
        for column in (
            "capability_profile",
            "capability_override",
            "required_capabilities",
            "granted_capabilities",
            "capability_preflight",
            "command_policy",
        ):
            if column not in existing:
                conn.execute(f"ALTER TABLE run ADD COLUMN {column} TEXT")


_SCHEMA_V13 = """
CREATE TABLE IF NOT EXISTS run_provenance (
    run_id TEXT PRIMARY KEY REFERENCES run(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL REFERENCES task(id) ON DELETE CASCADE,
    repository_path TEXT,
    worktree_path TEXT,
    branch TEXT,
    base_branch TEXT,
    base_sha TEXT,
    head_sha TEXT,
    pull_request_number INTEGER,
    pull_request_url TEXT,
    pull_request_head_sha TEXT,
    ci_conclusions_json TEXT,
    ci_observed_at TEXT,
    accepted_sha TEXT,
    accepted_at TEXT,
    deployed_sha TEXT,
    deployment_environment TEXT,
    deployed_at TEXT,
    deployment_verified_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_run_provenance_task_id ON run_provenance(task_id);
CREATE INDEX IF NOT EXISTS idx_run_provenance_pr ON run_provenance(pull_request_number);

CREATE TABLE IF NOT EXISTS provenance_evidence (
    integrity_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES run(id) ON DELETE CASCADE,
    adapter TEXT NOT NULL,
    status TEXT NOT NULL,
    candidate_sha TEXT,
    reported_sha TEXT,
    native_payload_json TEXT NOT NULL,
    normalized_json TEXT NOT NULL,
    observed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_provenance_evidence_run_id
    ON provenance_evidence(run_id);
"""


_SCHEMA_V14 = """
CREATE TABLE IF NOT EXISTS run_provider_route (
    run_id TEXT PRIMARY KEY REFERENCES run(id) ON DELETE CASCADE,
    providers_json TEXT NOT NULL,
    max_attempts INTEGER NOT NULL CHECK (max_attempts >= 1),
    selection_reason TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_attempt (
    run_id TEXT NOT NULL REFERENCES run(id) ON DELETE CASCADE,
    attempt_number INTEGER NOT NULL CHECK (attempt_number >= 1),
    provider_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    classification TEXT,
    disposition TEXT,
    error_code TEXT,
    parent_attempt_number INTEGER,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    PRIMARY KEY (run_id, attempt_number)
);

CREATE INDEX IF NOT EXISTS idx_provider_attempt_run_id
    ON provider_attempt(run_id, attempt_number);
"""


# Each migration is either a raw SQL script (applied via `executescript`, every
# statement `IF NOT EXISTS`) or a callable(conn) for changes — like `ALTER
# TABLE ADD COLUMN` — that need their own idempotency check.
MIGRATIONS: list[tuple[int, str | Callable[[sqlite3.Connection], None]]] = [
    (1, _SCHEMA_V1),
    (2, _migration_2_add_failure_reason),
    (3, _migration_3_add_live_execution_center_v2_fields),
    (4, _migration_4_add_first_output_at),
    (5, _SCHEMA_V5),
    (6, _SCHEMA_V6),
    (7, _migration_7_add_proposal_parameters_json),
    (8, _migration_8_add_independent_review_fields),
    # Renumbered from 6 on integration: the execution-provider branch and the
    # autonomy/review work both grew migrations from a shared base of 5, so this
    # one moves to the end of the sequence. Its content is unchanged and it is
    # idempotent, so a database that already ran it under the old number simply
    # finds the columns present.
    (9, _migration_9_add_execution_provider_fields),
    (10, _SCHEMA_V10),
    (11, _migration_11_add_pre_run_head),
    (12, _migration_12_add_executor_capability_fields),
    (13, _SCHEMA_V13),
    (14, _SCHEMA_V14),
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
    if current >= 13:
        backfill_run_provenance(db_path, limit=500)
    # Optional, operator-opted-in retention: run after the migration connection
    # above has closed so VACUUM (if enabled) does not contend with it. See
    # `apply_runtime_retention`.
    maybe_apply_runtime_retention(db_path)


def apply_runtime_retention(db_path: Path, *, retention_days: int) -> int:
    """Delete `run_event` rows (and orphaned `report` rows) for runs that have
    been terminal for longer than `retention_days`, returning the number of
    `run_event` rows removed.

    Bounded and conservative:
      * only *terminal* runs are eligible (their events are historical audit
        trail, not live state);
      * the cutoff is computed in Python with the same naive-local ISO
        convention every other timestamp in this app uses (`models.iso_now`),
        so it never disagrees with `completed_at` by a UTC offset;
      * the run row itself is kept (its `state`/`completed_at` remain visible
        in the Execution Center and to reconciliation); only the bulky
        per-output-event history is pruned;
      * runs with a NULL `completed_at` are left untouched.

    Does not VACUUM here — reclaiming disk is a separate, heavier, lock-holding
    operation the operator should run deliberately (see `maybe_apply_runtime_retention`).
    """
    if retention_days <= 0:
        return 0
    cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat(timespec="seconds")
    placeholders = ",".join("?" for _ in TERMINAL_STATES)
    with connect(db_path) as conn:
        with transaction(conn):
            cur = conn.execute(
                f"""
                DELETE FROM run_event
                 WHERE run_id IN (
                    SELECT id FROM run
                     WHERE state IN ({placeholders})
                       AND completed_at IS NOT NULL
                       AND completed_at < ?
                 )
                """,
                (*TERMINAL_STATES, cutoff),
            )
            removed = cur.rowcount
        # `report` rows cascade-delete with `run` via FK, but a terminal run's
        # report file on disk is also historical; leave the DB row (the path is
        # small) — only events are bulky.
    return removed


def maybe_apply_runtime_retention(db_path: Path) -> None:
    """Apply retention iff the operator set `AICC_RUNTIME_RETENTION_DAYS` to a
    positive integer. Default (unset / <= 0) is a no-op, so this never changes
    behavior for existing installs or the test suite.

    A companion `AICC_RUNTIME_VACUUM_ON_START=1` runs `VACUUM` after pruning to
    reclaim disk. VACUUM rewrites the whole database under an exclusive lock, so
    it is opt-in and should only be enabled on a single-host install that can
    pause other writers briefly.
    """
    raw = os.environ.get("AICC_RUNTIME_RETENTION_DAYS")
    if not raw:
        return
    try:
        retention_days = int(raw)
    except ValueError:
        return
    if retention_days <= 0:
        return
    apply_runtime_retention(db_path, retention_days=retention_days)
    if os.environ.get("AICC_RUNTIME_VACUUM_ON_START") == "1":
        with connect(db_path) as conn:
            conn.execute("VACUUM")


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


def delete_task(db_path: Path, task_id: str) -> bool:
    """Delete a task and everything that hangs off it, returning True iff a row
    was removed.

    The FK graph already declares `ON DELETE CASCADE` from session/run/run_event/
    report/completion(+validation/event) to their parents, and proposal task refs
    are `ON DELETE SET NULL`, so deleting the `task` row (with `foreign_keys=ON`,
    set per connection) removes every dependent runtime.db row atomically. This
    closes the AR-1 orphan gap: `tasks_repository.delete_task` only rewrites the
    tasks.json card and previously left these rows behind forever.
    """
    with connect(db_path) as conn:
        with transaction(conn):
            cur = conn.execute("DELETE FROM task WHERE id = ?", (task_id,))
            return cur.rowcount > 0


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
    capability_profile: str | None = None,
    capability_override: str | None = None,
    required_capabilities: str | None = None,
    granted_capabilities: str | None = None,
    capability_preflight: str | None = None,
    command_policy: str | None = None,
    provider_id: str = "claude_code",
    provider_metadata_json: str | None = None,
    provider_route: tuple[str, ...] | None = None,
    max_provider_attempts: int | None = None,
    provider_route_reason: str | None = None,
    provider_policy_version: str | None = None,
    canonical_repository_path: str | None = None,
    worktree_path: str | None = None,
    branch: str | None = None,
    base_branch: str | None = None,
    base_sha: str | None = None,
    head_sha: str | None = None,
    enforce_workspace_lock: bool = False,
    max_global_concurrency: int | None = None,
) -> dict:
    """`expected_branch`/`launch_source`/`prompt_version` are write-once, like
    `project`/`task_type`/`repository_path` above — resolved by the caller
    once at launch time and never recomputed or overwritten afterward (they
    are deliberately absent from `_UPDATABLE_RUN_FIELDS`).

    `enforce_workspace_lock`, default `False`, preserves this function's
    original behavior for every direct/low-level caller (including this
    module's own test suite, which routinely creates several concurrently-
    "active" `run` rows against the same throwaway `repository_path` purely
    to exercise persistence mechanics, with no process ever actually
    running). Only `Supervisor.start_raw` — the one path that actually spawns
    a subprocess against `repository_path` — passes `True`. When it does, the
    conflict check (any other row already in `EXECUTION_CENTER_ACTIVE_STATES`
    for this exact `repository_path`) runs inside this same `BEGIN IMMEDIATE`
    transaction as the `INSERT`, so it cannot lose a race against a second,
    concurrent `create_run(..., enforce_workspace_lock=True)` call for the
    same workspace the way a separate pre-flight query (e.g. `launch_service.
    find_active_run_conflict`) can — raises `WorkspaceLockedError` instead of
    inserting."""
    if provider_route is not None:
        if not provider_route or any(not item for item in provider_route):
            raise ValueError("provider_route must contain non-empty provider ids")
        if provider_route[0] != provider_id:
            raise ValueError("provider_id must equal the first provider_route entry")
        if max_provider_attempts is None:
            max_provider_attempts = len(provider_route)
        if not 1 <= max_provider_attempts <= len(provider_route):
            raise ValueError("max_provider_attempts must fit inside provider_route")
        if len(set(provider_route)) != len(provider_route):
            raise ValueError("provider_route may attempt each provider at most once")
        provider_route_reason = provider_route_reason or "explicit_request"
        provider_policy_version = provider_policy_version or "project_policy_v1"
    elif max_provider_attempts is not None:
        raise ValueError("max_provider_attempts requires provider_route")

    with connect(db_path) as conn:
        with transaction(conn):
            if enforce_workspace_lock:
                placeholders = ", ".join("?" for _ in EXECUTION_CENTER_ACTIVE_STATES)
                conflict = conn.execute(
                    f"SELECT * FROM run WHERE repository_path = ? AND state IN ({placeholders})",
                    (repository_path, *EXECUTION_CENTER_ACTIVE_STATES),
                ).fetchone()
                if conflict is not None:
                    raise WorkspaceLockedError(_row_to_dict(conflict))

                # Task-id exclusivity (audit M1): the workspace lock above only
                # catches a double-launch that resolves to the SAME workspace. If
                # a task's configured workspace/branch changes between two
                # in-flight launches they can resolve to *different* paths and
                # slip past it — two agents running for one task. Checked in the
                # same BEGIN IMMEDIATE transaction as the INSERT, so it cannot
                # lose the race a separate pre-flight query can. Ordered after the
                # workspace check so a same-workspace conflict still surfaces as
                # WorkspaceLockedError (unchanged behaviour).
                task_conflict = conn.execute(
                    f"SELECT * FROM run WHERE task_id = ? AND state IN ({placeholders})",
                    (task_id, *EXECUTION_CENTER_ACTIVE_STATES),
                ).fetchone()
                if task_conflict is not None:
                    raise TaskAlreadyActiveError(_row_to_dict(task_conflict))

            if max_global_concurrency is not None:
                # Global cap as an atomic invariant, in the SAME transaction as
                # the INSERT (like the workspace lock above): count every run
                # currently active across all workspaces and refuse the launch if
                # the cap is already met. Two concurrent launches serialize on the
                # BEGIN IMMEDIATE write lock, so they cannot both slip past the
                # count — the way per-caller pre-flight checks can.
                placeholders = ", ".join("?" for _ in EXECUTION_CENTER_ACTIVE_STATES)
                active_count = conn.execute(
                    f"SELECT COUNT(*) AS n FROM run WHERE state IN ({placeholders})",
                    tuple(EXECUTION_CENTER_ACTIVE_STATES),
                ).fetchone()["n"]
                if active_count >= max_global_concurrency:
                    raise GlobalConcurrencyLimitError(active_count, max_global_concurrency)

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
                "provider_id": provider_id,
                "provider_metadata_json": provider_metadata_json,
                "commit_hash": None,
                "pull_request_url": None,
                "capability_profile": capability_profile,
                "capability_override": capability_override,
                "required_capabilities": required_capabilities,
                "granted_capabilities": granted_capabilities,
                "capability_preflight": capability_preflight,
                "command_policy": command_policy,
                "version": 0,
                "created_at": now,
                "updated_at": now,
            }
            # Build the column list from the table as it exists rather than
            # from a fixed literal. A database migrated only part-way — which
            # is exactly what the historical-schema migration tests construct —
            # has no `provider_*` columns yet, and naming them unconditionally
            # would make `create_run` unusable against any schema older than
            # the one that introduced them.
            table_columns = {row["name"] for row in conn.execute("PRAGMA table_info(run)")}
            insert_columns = [name for name in record if name in table_columns]
            conn.execute(
                f"""INSERT INTO run ({", ".join(insert_columns)})
                    VALUES ({", ".join(f":{name}" for name in insert_columns)})""",
                record,
            )
            provenance_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'run_provenance'"
            ).fetchone()
            if provenance_table is not None:
                conn.execute(
                    """INSERT INTO run_provenance (
                           run_id, task_id, repository_path, worktree_path, branch,
                           base_branch, base_sha, head_sha, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        record["id"],
                        task_id,
                        canonical_repository_path,
                        worktree_path or repository_path,
                        branch,
                        base_branch,
                        base_sha,
                        head_sha,
                        now,
                        now,
                    ),
                )
            provider_route_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'run_provider_route'"
            ).fetchone()
            if provider_route_table is not None and provider_route is not None:
                conn.execute(
                    """INSERT INTO run_provider_route (
                           run_id, providers_json, max_attempts, selection_reason,
                           policy_version, created_at
                       ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        record["id"],
                        json.dumps(provider_route, ensure_ascii=False),
                        max_provider_attempts,
                        provider_route_reason,
                        provider_policy_version,
                        now,
                    ),
                )
    return record


def get_run(db_path: Path, run_id: str) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM run WHERE id = ?", (run_id,)).fetchone()
        return _row_to_dict(row)


def get_latest_run_for_task(db_path: Path, task_id: str) -> dict | None:
    """Newest run row for `task_id`, or None if the task has no runs.

    Distinct from `list_runs(task_id=..., limit=1)`: that path orders only by
    `created_at DESC`, and `created_at` is second-granularity (`iso_now()`),
    so two runs created in the same second tie and SQLite returns them in
    unspecified rowid order. We add `rowid DESC` as a stable tiebreak — rowid
    is insertion order, so the higher rowid is the newer row — guaranteeing
    the actually-newest run is returned. Used by `task_sync.sync_tasks` to
    self-heal tasks whose `current_run_id` was orphaned by a lost update.
    """
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM run WHERE task_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (task_id,),
        ).fetchone()
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


def count_runs(
    db_path: Path,
    *,
    states: Iterable[str] | None = None,
) -> int:
    """Cheap `COUNT(*)` over `run` — the authoritative total run count without
    materializing any rows. Used by the dashboard footer/health so "Всего" and
    success-rate denominators are not silently truncated by the Live Board's
    `limit=200` window (`list_runs(limit=200)` only ever sees the 200 newest
    runs, which under-counts on busy installs). Accepts the same `states`
    filter as `list_runs` for windowed counts (e.g. terminal runs in the last
    sprint); `None` counts every run regardless of state."""
    clauses: list[str] = []
    params: list[Any] = []
    if states is not None:
        states_list = list(states)
        if states_list:
            placeholders = ", ".join("?" for _ in states_list)
            clauses.append(f"state IN ({placeholders})")
            params.extend(states_list)
        else:
            clauses.append("0")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect(db_path) as conn:
        (n,) = conn.execute(f"SELECT COUNT(*) FROM run {where}", params).fetchone()
        return int(n)


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
# Canonical run provenance (schema 13)
# --------------------------------------------------------------------------


def backfill_run_provenance(db_path: Path, *, limit: int = 500) -> int:
    """Backfill at most ``limit`` legacy runs without inventing evidence.

    The historical ``run.repository_path`` named the process cwd, so it is a
    truthful worktree path but not necessarily the canonical repository. The
    latter therefore remains NULL. Completion facts are copied only when they
    were already persisted; accepted SHA is derived only from an explicit
    TARGET_VERIFIED terminal completion. Repeated calls insert only missing
    rows and are therefore idempotent.
    """
    if limit < 0:
        raise ValueError("limit must be non-negative")
    if limit == 0:
        return 0
    now = iso_now()
    with connect(db_path) as conn:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'run_provenance'"
        ).fetchone()
        if table is None:
            return 0
        with transaction(conn):
            cursor = conn.execute(
                """INSERT INTO run_provenance (
                       run_id, task_id, repository_path, worktree_path, branch,
                       base_branch, base_sha, head_sha, pull_request_number,
                       pull_request_url, pull_request_head_sha, accepted_sha,
                       accepted_at, created_at, updated_at
                   )
                   SELECT r.id, r.task_id, NULL, r.repository_path, c.branch,
                          c.base_branch, NULL, c.head_commit, c.pull_request_number,
                          c.pull_request_url,
                          CASE WHEN c.pull_request_number IS NOT NULL THEN c.head_commit END,
                          CASE
                              WHEN c.completion_state = 'COMPLETED'
                               AND c.last_reason_code = 'TARGET_VERIFIED'
                              THEN COALESCE(c.merge_commit, c.head_commit)
                          END,
                          CASE
                              WHEN c.completion_state = 'COMPLETED'
                               AND c.last_reason_code = 'TARGET_VERIFIED'
                              THEN c.updated_at
                          END,
                          r.created_at, ?
                   FROM run AS r
                   LEFT JOIN completion AS c ON c.run_id = r.id
                   WHERE NOT EXISTS (
                       SELECT 1 FROM run_provenance AS p WHERE p.run_id = r.id
                   )
                   ORDER BY r.created_at, r.rowid
                   LIMIT ?""",
                (now, limit),
            )
            return cursor.rowcount


def get_run_provenance(db_path: Path, run_id: str) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM run_provenance WHERE run_id = ?", (run_id,)
        ).fetchone()
        return _row_to_dict(row)


def get_run_provenance_for_runs(db_path: Path, run_ids: list[str]) -> dict[str, dict]:
    if not run_ids:
        return {}
    placeholders = ", ".join("?" for _ in run_ids)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM run_provenance WHERE run_id IN ({placeholders})",
            tuple(run_ids),
        ).fetchall()
    return {row["run_id"]: dict(row) for row in rows}


def update_run_provenance(db_path: Path, run_id: str, *, fields: dict) -> dict:
    allowed = {
        "repository_path",
        "worktree_path",
        "branch",
        "base_branch",
        "base_sha",
        "head_sha",
        "pull_request_number",
        "pull_request_url",
        "pull_request_head_sha",
        "ci_conclusions_json",
        "ci_observed_at",
        "accepted_sha",
        "accepted_at",
        "deployed_sha",
        "deployment_environment",
        "deployed_at",
        "deployment_verified_at",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise UnknownRunFieldError(f"Not an updatable provenance field: {sorted(unknown)}")
    if not fields:
        row = get_run_provenance(db_path, run_id)
        if row is None:
            raise KeyError(f"No provenance for run: {run_id!r}")
        return row
    values = dict(fields)
    values["updated_at"] = iso_now()
    set_clause = ", ".join(f"{key} = :{key}" for key in values)
    values["run_id"] = run_id
    with connect(db_path) as conn:
        with transaction(conn):
            cursor = conn.execute(
                f"UPDATE run_provenance SET {set_clause} WHERE run_id = :run_id", values
            )
            if cursor.rowcount != 1:
                raise KeyError(f"No provenance for run: {run_id!r}")
            row = conn.execute(
                "SELECT * FROM run_provenance WHERE run_id = ?", (run_id,)
            ).fetchone()
            return dict(row)


def set_run_provenance_once(
    db_path: Path,
    run_id: str,
    *,
    field: str,
    value: str,
    fields: dict,
) -> tuple[dict, bool]:
    """Atomically set an immutable provenance fact once.

    ``matched`` is false only when another immutable value already exists.
    A replay of the same value is idempotent and does not rewrite timestamps.
    """
    if field not in {"accepted_sha", "deployed_sha"}:
        raise UnknownRunFieldError(f"Not an immutable provenance field: {field!r}")
    if fields.get(field) != value:
        raise ValueError(f"fields must bind {field!r} to the immutable value")
    allowed = {
        "accepted_sha",
        "accepted_at",
        "deployed_sha",
        "deployment_environment",
        "deployed_at",
        "deployment_verified_at",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise UnknownRunFieldError(f"Not an immutable provenance field: {sorted(unknown)}")
    with connect(db_path) as conn:
        with transaction(conn):
            row = conn.execute(
                "SELECT * FROM run_provenance WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"No provenance for run: {run_id!r}")
            current = dict(row)
            existing = current.get(field)
            if existing is not None:
                return current, existing == value
            values = dict(fields)
            values["updated_at"] = iso_now()
            values["run_id"] = run_id
            set_clause = ", ".join(f"{key} = :{key}" for key in values if key != "run_id")
            conn.execute(
                f"UPDATE run_provenance SET {set_clause} WHERE run_id = :run_id", values
            )
            updated = conn.execute(
                "SELECT * FROM run_provenance WHERE run_id = ?", (run_id,)
            ).fetchone()
            return dict(updated), True


def create_provenance_evidence(
    db_path: Path,
    *,
    run_id: str,
    integrity_id: str,
    adapter: str,
    status: str,
    candidate_sha: str | None,
    reported_sha: str | None,
    native_payload_json: str,
    normalized_json: str,
    observed_at: str,
) -> dict:
    """Insert one native evidence event, or return its exact prior record.

    Replaying the same integrity id with different content is rejected rather
    than silently rewriting audit history.
    """
    record = {
        "integrity_id": integrity_id,
        "run_id": run_id,
        "adapter": adapter,
        "status": status,
        "candidate_sha": candidate_sha,
        "reported_sha": reported_sha,
        "native_payload_json": native_payload_json,
        "normalized_json": normalized_json,
        "observed_at": observed_at,
    }
    with connect(db_path) as conn:
        with transaction(conn):
            existing = conn.execute(
                "SELECT * FROM provenance_evidence WHERE integrity_id = ?",
                (integrity_id,),
            ).fetchone()
            if existing is not None:
                current = dict(existing)
                comparable = {key: current[key] for key in record}
                if comparable != record:
                    raise ValueError(
                        f"Evidence integrity id {integrity_id!r} already has different content"
                    )
                return current
            conn.execute(
                """INSERT INTO provenance_evidence (
                       integrity_id, run_id, adapter, status, candidate_sha,
                       reported_sha, native_payload_json, normalized_json, observed_at
                   ) VALUES (
                       :integrity_id, :run_id, :adapter, :status, :candidate_sha,
                       :reported_sha, :native_payload_json, :normalized_json, :observed_at
                   )""",
                record,
            )
            return record


# --------------------------------------------------------------------------
# Explicit provider route and immutable attempt evidence (schema 14)
# --------------------------------------------------------------------------


def get_provider_route(db_path: Path, run_id: str) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM run_provider_route WHERE run_id = ?", (run_id,)
        ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["providers"] = json.loads(result.pop("providers_json"))
    return result


def get_provider_routes_for_runs(db_path: Path, run_ids: list[str]) -> dict[str, dict]:
    if not run_ids:
        return {}
    placeholders = ", ".join("?" for _ in run_ids)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM run_provider_route WHERE run_id IN ({placeholders})",
            tuple(run_ids),
        ).fetchall()
    result: dict[str, dict] = {}
    for row in rows:
        item = dict(row)
        item["providers"] = json.loads(item.pop("providers_json"))
        result[item["run_id"]] = item
    return result


def start_provider_attempt(
    db_path: Path,
    *,
    run_id: str,
    attempt_number: int,
    provider_id: str,
    started_at: str,
) -> dict:
    record = {
        "run_id": run_id,
        "attempt_number": attempt_number,
        "provider_id": provider_id,
        "outcome": "started",
        "classification": None,
        "disposition": None,
        "error_code": None,
        "parent_attempt_number": attempt_number - 1 if attempt_number > 1 else None,
        "started_at": started_at,
        "completed_at": None,
    }
    with connect(db_path) as conn:
        with transaction(conn):
            existing = conn.execute(
                """SELECT * FROM provider_attempt
                   WHERE run_id = ? AND attempt_number = ?""",
                (run_id, attempt_number),
            ).fetchone()
            if existing is not None:
                current = dict(existing)
                if current != record:
                    raise ValueError(
                        f"Provider attempt {run_id!r}/{attempt_number} already differs"
                    )
                return current
            conn.execute(
                """INSERT INTO provider_attempt (
                       run_id, attempt_number, provider_id, outcome,
                       classification, disposition, error_code,
                       parent_attempt_number, started_at, completed_at
                   ) VALUES (
                       :run_id, :attempt_number, :provider_id, :outcome,
                       :classification, :disposition, :error_code,
                       :parent_attempt_number, :started_at, :completed_at
                   )""",
                record,
            )
    return record


def finish_provider_attempt(
    db_path: Path,
    *,
    run_id: str,
    attempt_number: int,
    outcome: str,
    classification: str,
    disposition: str,
    error_code: str | None,
    completed_at: str,
) -> dict:
    if outcome not in {"succeeded", "failed", "cancelled"}:
        raise ValueError(f"Invalid provider attempt outcome: {outcome!r}")
    with connect(db_path) as conn:
        with transaction(conn):
            row = conn.execute(
                """SELECT * FROM provider_attempt
                   WHERE run_id = ? AND attempt_number = ?""",
                (run_id, attempt_number),
            ).fetchone()
            if row is None:
                raise KeyError(f"No provider attempt {run_id!r}/{attempt_number}")
            current = dict(row)
            expected = {
                **current,
                "outcome": outcome,
                "classification": classification,
                "disposition": disposition,
                "error_code": error_code,
                "completed_at": completed_at,
            }
            if current["outcome"] != "started":
                if current != expected:
                    raise ValueError(
                        f"Provider attempt {run_id!r}/{attempt_number} is immutable"
                    )
                return current
            conn.execute(
                """UPDATE provider_attempt
                   SET outcome = ?, classification = ?, disposition = ?,
                       error_code = ?, completed_at = ?
                   WHERE run_id = ? AND attempt_number = ? AND outcome = 'started'""",
                (
                    outcome,
                    classification,
                    disposition,
                    error_code,
                    completed_at,
                    run_id,
                    attempt_number,
                ),
            )
            updated = conn.execute(
                """SELECT * FROM provider_attempt
                   WHERE run_id = ? AND attempt_number = ?""",
                (run_id, attempt_number),
            ).fetchone()
            return dict(updated)


def list_provider_attempts(db_path: Path, run_id: str) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """SELECT * FROM provider_attempt
               WHERE run_id = ? ORDER BY attempt_number""",
            (run_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_provider_attempts_for_runs(
    db_path: Path, run_ids: list[str]
) -> dict[str, list[dict]]:
    if not run_ids:
        return {}
    placeholders = ", ".join("?" for _ in run_ids)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""SELECT * FROM provider_attempt
                WHERE run_id IN ({placeholders})
                ORDER BY run_id, attempt_number""",
            tuple(run_ids),
        ).fetchall()
    result: dict[str, list[dict]] = {run_id: [] for run_id in run_ids}
    for row in rows:
        result[row["run_id"]].append(dict(row))
    return result


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


def latest_events_for_runs(db_path: Path, run_ids: list[str]) -> dict[str, dict]:
    """The single most recent event per run, keyed by run_id — one query for a
    whole board instead of a `sqlite3.connect()` per run (audit H5 N+1). Same
    shaping as `tail_run_events` (payload_json decoded into `payload`). Uses a
    MAX(seq) join rather than a window function so it works on any SQLite the
    rest of the module targets."""
    if not run_ids:
        return {}
    placeholders = ", ".join("?" for _ in run_ids)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""SELECT e.run_id, e.seq, e.event_type, e.payload_json, e.created_at
                FROM run_event e
                JOIN (
                    SELECT run_id, MAX(seq) AS mx FROM run_event
                    WHERE run_id IN ({placeholders}) GROUP BY run_id
                ) m ON e.run_id = m.run_id AND e.seq = m.mx""",
            tuple(run_ids),
        ).fetchall()
    result: dict[str, dict] = {}
    for row in rows:
        event = dict(row)
        event["payload"] = json.loads(event.pop("payload_json"))
        result[event["run_id"]] = event
    return result


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


def get_reports_for_runs(db_path: Path, run_ids: list[str]) -> dict[str, dict]:
    """Batch of `get_report` keyed by run_id — one query for a whole board
    instead of a `sqlite3.connect()` per run (audit H5 N+1)."""
    if not run_ids:
        return {}
    placeholders = ", ".join("?" for _ in run_ids)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM report WHERE run_id IN ({placeholders})", tuple(run_ids)
        ).fetchall()
    return {row["run_id"]: _row_to_dict(row) for row in rows}


# --------------------------------------------------------------------------
# Completion pipeline (AICC-AUTONOMY-001)
#
# One `completion` row per run. Like `run`, it is a mutable current-state row
# updated only through a compare-and-set (`update_completion`) that bumps
# `version`; the set of columns that update may touch is the allowlist below
# (write-once identity columns — run_id/task_id/project/created_at — are
# deliberately absent). The completion *state* string is validated by the
# domain layer (`runtime.completion`), not here, so this module stays agnostic
# to the lifecycle vocabulary.
# --------------------------------------------------------------------------

_UPDATABLE_COMPLETION_FIELDS: frozenset[str] = frozenset(
    {
        "session_id",
        "branch",
        "base_branch",
        "head_commit",
        "remote",
        "remote_branch",
        "pull_request_number",
        "pull_request_url",
        "pull_request_state",
        "replaced_pull_request_number",
        "replaced_pull_request_url",
        "merge_commit",
        "merge_mode",
        "merge_method",
        "completion_state",
        "last_reason_code",
        "requires_human",
        "is_recoverable",
        "recommended_action",
        "validation_summary",
        "review_verdict",
        "review_run_id",
        "review_summary",
        "policy_json",
        "last_checked_at",
        "next_retry_at",
        "retry_count",
        "recovery_count",
    }
)


def _validate_updatable_completion_fields(fields: dict) -> None:
    unknown = set(fields) - _UPDATABLE_COMPLETION_FIELDS
    if unknown:
        raise UnknownRunFieldError(f"Not an updatable completion field: {sorted(unknown)}")


_COMPLETION_INSERT_COLUMNS: tuple[str, ...] = (
    "run_id",
    "task_id",
    "session_id",
    "project",
    "repository_path",
    "branch",
    "base_branch",
    "head_commit",
    "remote",
    "remote_branch",
    "pull_request_number",
    "pull_request_url",
    "pull_request_state",
    "replaced_pull_request_number",
    "replaced_pull_request_url",
    "merge_commit",
    "merge_mode",
    "merge_method",
    "completion_state",
    "last_reason_code",
    "requires_human",
    "is_recoverable",
    "recommended_action",
    "validation_summary",
    "policy_json",
    "last_checked_at",
    "next_retry_at",
    "retry_count",
    "recovery_count",
    "version",
    "created_at",
    "updated_at",
)


def create_completion(
    db_path: Path,
    *,
    run_id: str,
    task_id: str,
    project: str,
    repository_path: str,
    completion_state: str,
    session_id: str | None = None,
    branch: str | None = None,
    base_branch: str | None = None,
    head_commit: str | None = None,
    remote: str | None = None,
    remote_branch: str | None = None,
    merge_mode: str | None = None,
    merge_method: str | None = None,
    policy_json: str | None = None,
    last_reason_code: str | None = None,
) -> dict:
    """Create the single `completion` row for a run.

    Raises `sqlite3.IntegrityError` if a completion row already exists for the
    run (the PRIMARY KEY on `run_id`) — this is the pipeline's restart-safe
    idempotency guard: callers (`runtime.completion_service.begin_completion`)
    check `get_completion` first, so a re-processed terminal run never gets a
    second completion row (and therefore never a duplicate PR)."""
    now = iso_now()
    record = {name: None for name in _COMPLETION_INSERT_COLUMNS}
    record.update(
        {
            "run_id": run_id,
            "task_id": task_id,
            "session_id": session_id,
            "project": project,
            "repository_path": repository_path,
            "branch": branch,
            "base_branch": base_branch,
            "head_commit": head_commit,
            "remote": remote,
            "remote_branch": remote_branch,
            "merge_mode": merge_mode,
            "merge_method": merge_method,
            "completion_state": completion_state,
            "last_reason_code": last_reason_code,
            "requires_human": 0,
            "is_recoverable": 0,
            "policy_json": policy_json,
            "retry_count": 0,
            "recovery_count": 0,
            "version": 0,
            "created_at": now,
            "updated_at": now,
        }
    )
    columns = ", ".join(_COMPLETION_INSERT_COLUMNS)
    placeholders = ", ".join(f":{name}" for name in _COMPLETION_INSERT_COLUMNS)
    with connect(db_path) as conn:
        with transaction(conn):
            conn.execute(f"INSERT INTO completion ({columns}) VALUES ({placeholders})", record)
    return record


def get_completion(db_path: Path, run_id: str) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM completion WHERE run_id = ?", (run_id,)).fetchone()
        return _row_to_dict(row)


def get_completions_for_runs(db_path: Path, run_ids: list[str]) -> dict[str, dict]:
    """Batch of `get_completion` keyed by run_id — one query for a whole board
    of runs instead of one `sqlite3.connect()` per run (audit H5 N+1). Runs with
    no completion row are simply absent from the result."""
    if not run_ids:
        return {}
    placeholders = ", ".join("?" for _ in run_ids)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM completion WHERE run_id IN ({placeholders})", tuple(run_ids)
        ).fetchall()
    return {row["run_id"]: _row_to_dict(row) for row in rows}


def get_completion_by_task(db_path: Path, task_id: str) -> dict | None:
    """The most recently created completion row for a task (there is one per
    run, and a task may have several runs over time).

    `created_at` is an ISO timestamp at *second* resolution, so two completions
    for the same task created within the same second tie, and a bare
    `ORDER BY created_at DESC` would return an arbitrary one of them. That is
    not hypothetical: an automatic rework relaunches a task as soon as its
    failure is observed, so several completions per task is the normal case, and
    a caller reading the stale row would act on a failure that has already been
    superseded. `rowid` — monotonic per insert — breaks the tie in true
    insertion order."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM completion WHERE task_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        return _row_to_dict(row)


def list_completions(
    db_path: Path,
    *,
    states: Iterable[str] | None = None,
    due_before: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """List completion rows, optionally restricted to a set of
    `completion_state` values and to rows whose `next_retry_at` is due
    (NULL, i.e. never scheduled, or `<= due_before`). Used by the bounded
    completion poller to find work without scanning terminal rows."""
    clauses: list[str] = []
    params: list[Any] = []
    states = list(states) if states is not None else None
    if states is not None:
        if not states:
            return []
        placeholders = ", ".join("?" for _ in states)
        clauses.append(f"completion_state IN ({placeholders})")
        params.extend(states)
    if due_before is not None:
        clauses.append("(next_retry_at IS NULL OR next_retry_at <= ?)")
        params.append(due_before)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    if limit < 0:
        raise ValueError(f"limit must be non-negative, got {limit}")
    params.append(limit)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM completion{where} ORDER BY created_at ASC LIMIT ?", params
        ).fetchall()
        return [dict(row) for row in rows]


def update_completion(db_path: Path, run_id: str, *, expected_version: int, fields: dict) -> dict:
    """Compare-and-set update of a `completion` row, mirroring
    `update_run_fields`: validates `fields` against
    `_UPDATABLE_COMPLETION_FIELDS`, bumps `version`, sets `updated_at`, and
    raises `LostUpdateError` if `expected_version` no longer matches."""
    fields = dict(fields)
    fields.pop("version", None)
    fields.pop("created_at", None)
    _validate_updatable_completion_fields(fields)
    with connect(db_path) as conn:
        with transaction(conn):
            row = conn.execute(
                "SELECT completion_state, version FROM completion WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"No such completion: {run_id!r}")
            # Compare-and-set FIRST: a caller whose `expected_version` no longer
            # matches has read a stale row, so it must lose with `LostUpdateError`
            # *before* we judge its intended transition against a state it never
            # saw. Evaluating the transition guard first would let a stale loser
            # be reported as an `InvalidCompletionTransitionError` (its stale
            # target measured against the winner's newer state) — misclassifying
            # benign concurrency as a hard state-machine violation. See
            # AICC-AUTONOMY-002.
            if row["version"] != expected_version:
                raise LostUpdateError(
                    f"Completion {run_id!r} version mismatch: expected {expected_version}, actual {row['version']}"
                )
            # Structural transition guard (mirrors `update_run_state`): once the
            # caller is confirmed to be operating on the current row version,
            # reject an illegal completion-state move (a backward jump or a move
            # out of a terminal state). A same-state / metadata-only update (no
            # `completion_state` in `fields`) is always allowed.
            new_state = fields.get("completion_state")
            if new_state is not None and not completion_domain.is_valid_completion_transition(
                row["completion_state"], new_state
            ):
                raise InvalidCompletionTransitionError(
                    f"Completion {run_id!r} cannot transition {row['completion_state']!r} -> {new_state!r}"
                )
            fields["updated_at"] = iso_now()
            set_clause = ", ".join(f"{key} = :{key}" for key in fields)
            params = dict(fields)
            params["run_id"] = run_id
            params["expected_version"] = expected_version
            cur = conn.execute(
                f"""UPDATE completion SET {set_clause}, version = version + 1
                    WHERE run_id = :run_id AND version = :expected_version""",
                params,
            )
            if cur.rowcount != 1:
                raise LostUpdateError(f"Completion {run_id!r} update affected {cur.rowcount} rows")
            updated = conn.execute("SELECT * FROM completion WHERE run_id = ?", (run_id,)).fetchone()
            return dict(updated)


def append_completion_event(
    db_path: Path,
    run_id: str,
    event_type: str,
    *,
    reason_code: str | None = None,
    message: str | None = None,
    metadata: dict | None = None,
) -> int:
    """Append one completion audit event and return its per-run sequence
    number. `metadata` is JSON-encoded; callers must never place credentials,
    tokens, or environment dumps in it (see `runtime.completion_service`)."""
    metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata is not None else None
    now = iso_now()
    with connect(db_path) as conn:
        with transaction(conn):
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM completion_event WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            seq = row["next_seq"]
            conn.execute(
                """INSERT INTO completion_event
                       (run_id, seq, event_type, reason_code, message, metadata_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (run_id, seq, event_type, reason_code, message, metadata_json, now),
            )
            return seq


def list_completion_events(db_path: Path, run_id: str, *, limit: int = 500) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """SELECT run_id, seq, event_type, reason_code, message, metadata_json, created_at
               FROM completion_event WHERE run_id = ? ORDER BY seq ASC LIMIT ?""",
            (run_id, limit),
        ).fetchall()
        events = []
        for row in rows:
            event = dict(row)
            raw = event.pop("metadata_json")
            event["metadata"] = json.loads(raw) if raw else None
            events.append(event)
        return events


def record_validation_result(
    db_path: Path,
    run_id: str,
    *,
    attempt: int,
    command: str,
    exit_code: int | None,
    started_at: str | None,
    finished_at: str | None,
    stdout_summary: str | None,
    stderr_summary: str | None,
) -> dict:
    """Record one validation-command result. `stdout_summary`/`stderr_summary`
    must already be bounded by the caller (`runtime.validation`) — this table
    never stores unlimited logs."""
    now = iso_now()
    record = {
        "run_id": run_id,
        "attempt": attempt,
        "command": command,
        "exit_code": exit_code,
        "started_at": started_at,
        "finished_at": finished_at,
        "stdout_summary": stdout_summary,
        "stderr_summary": stderr_summary,
        "created_at": now,
    }
    with connect(db_path) as conn:
        with transaction(conn):
            conn.execute(
                """INSERT INTO completion_validation
                       (run_id, attempt, command, exit_code, started_at, finished_at,
                        stdout_summary, stderr_summary, created_at)
                   VALUES (:run_id, :attempt, :command, :exit_code, :started_at, :finished_at,
                           :stdout_summary, :stderr_summary, :created_at)""",
                record,
            )
    return record


def list_validation_results(db_path: Path, run_id: str, *, attempt: int | None = None) -> list[dict]:
    with connect(db_path) as conn:
        if attempt is not None:
            rows = conn.execute(
                "SELECT * FROM completion_validation WHERE run_id = ? AND attempt = ? ORDER BY id ASC",
                (run_id, attempt),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM completion_validation WHERE run_id = ? ORDER BY id ASC",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]


# --------------------------------------------------------------------------
# Autonomy proposals (schema 7) — the pre-execution decision layer.
#
# Mirrors the completion-row idioms: a `create_proposal` write-once insert, a
# compare-and-set `update_proposal` guarded by both a lifecycle-scoped field
# allowlist and the `autonomy.is_valid_proposal_transition` structural guard,
# evidence that is append-only until assessment and frozen afterward, and an
# append-only `append_proposal_event` audit trail. Identity columns
# (id/kind/project/created_at) are write-once and deliberately absent from the
# updatable allowlist below.
# --------------------------------------------------------------------------

_UPDATABLE_PROPOSAL_FIELDS: frozenset[str] = frozenset(
    {
        "task_id",
        "title",
        "rationale",
        "state",
        "risk_level",
        "policy_json",
        "eligibility_json",
        "plan_json",
        "parameters_json",
        "evidence_digest",
        "requires_human",
        "last_reason_code",
        "decided_by",
        "decision_reason",
        "dispatched_run_id",
        "dispatched_task_id",
    }
)


_PROPOSAL_AUTHORITY_FIELDS: frozenset[str] = frozenset(
    {
        "task_id",
        "title",
        "rationale",
        "risk_level",
        "policy_json",
        "eligibility_json",
        "plan_json",
        "parameters_json",
        "evidence_digest",
    }
)

_PROPOSAL_DECISION_FIELDS: frozenset[str] = frozenset(
    {
        "state",
        "requires_human",
        "last_reason_code",
        "decided_by",
        "decision_reason",
    }
)

_PROPOSAL_FIELDS_BY_STATE: dict[str, frozenset[str]] = {
    # Assessment may start from either DRAFT or PROPOSED. These are the only
    # states in which authority-bearing fields may be written.
    autonomy_domain.ProposalState.DRAFT: _PROPOSAL_AUTHORITY_FIELDS | _PROPOSAL_DECISION_FIELDS,
    autonomy_domain.ProposalState.PROPOSED: _PROPOSAL_AUTHORITY_FIELDS | _PROPOSAL_DECISION_FIELDS,
    # From this point onward, policy, evidence digest, action parameters,
    # eligibility, risk, and plan are immutable. Lifecycle decisions may still
    # advance the row and record the responsible actor/reason.
    autonomy_domain.ProposalState.ELIGIBLE: _PROPOSAL_DECISION_FIELDS,
    autonomy_domain.ProposalState.BLOCKED: _PROPOSAL_DECISION_FIELDS,
    autonomy_domain.ProposalState.AWAITING_APPROVAL: _PROPOSAL_DECISION_FIELDS,
    autonomy_domain.ProposalState.APPROVED: _PROPOSAL_DECISION_FIELDS,
    # Result links are legal only while confirming a dispatched action.
    autonomy_domain.ProposalState.DISPATCHED: frozenset(
        {"state", "last_reason_code", "dispatched_run_id", "dispatched_task_id"}
    ),
    # Terminal rows permit only an idempotent same-state CAS; their authority
    # and decision attribution cannot be rewritten.
    autonomy_domain.ProposalState.REJECTED: frozenset({"state"}),
    autonomy_domain.ProposalState.EXECUTED: frozenset({"state"}),
    autonomy_domain.ProposalState.WITHDRAWN: frozenset({"state"}),
}


def _validate_updatable_proposal_fields(fields: dict) -> None:
    unknown = set(fields) - _UPDATABLE_PROPOSAL_FIELDS
    if unknown:
        raise UnknownRunFieldError(f"Not an updatable proposal field: {sorted(unknown)}")


def _validate_proposal_fields_for_state(
    state: str,
    fields: dict,
    *,
    assessment_persisted: bool,
) -> None:
    allowed = _PROPOSAL_FIELDS_BY_STATE.get(state, frozenset())
    # Assessment is normally persisted and routed onward in one transaction,
    # so callers never observe a DRAFT/PROPOSED row with a verdict. Keep the DB
    # boundary safe even if a lower-level caller writes the verdict without its
    # transitions: once the marker exists, authority is frozen immediately.
    if (
        assessment_persisted
        and state
        in {
            autonomy_domain.ProposalState.DRAFT,
            autonomy_domain.ProposalState.PROPOSED,
        }
    ):
        allowed = _PROPOSAL_DECISION_FIELDS
    forbidden = set(fields) - allowed
    if forbidden:
        raise ProposalFieldFrozenError(
            f"Proposal fields {sorted(forbidden)} cannot be updated in state {state!r}"
        )


def _canonical_proposal_parameters_json(raw: str) -> str:
    """Validate and canonicalize the structured action payload.

    A proposal approves a named parameter object, never an arbitrary JSON
    scalar/list. Canonical serialization makes the persisted authority stable
    for hashing, comparison, and later execution binding.
    """
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("proposal.parameters_json must be valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("proposal.parameters_json must encode a JSON object")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


_PROPOSAL_INSERT_COLUMNS: tuple[str, ...] = (
    "id",
    "kind",
    "project",
    "task_id",
    "title",
    "rationale",
    "state",
    "risk_level",
    "policy_json",
    "eligibility_json",
    "plan_json",
    "parameters_json",
    "evidence_digest",
    "requires_human",
    "last_reason_code",
    "decided_by",
    "decision_reason",
    "dispatched_run_id",
    "dispatched_task_id",
    "version",
    "created_at",
    "updated_at",
)


def create_proposal(
    db_path: Path,
    *,
    kind: str,
    project: str,
    title: str,
    rationale: str,
    state: str,
    risk_level: str,
    proposal_id: str | None = None,
    task_id: str | None = None,
    policy_json: str | None = None,
    parameters_json: str = "{}",
    requires_human: bool = True,
) -> dict:
    """Create one `proposal` row in its initial state.

    `rationale` is required and never blank — a proposal that cannot explain why
    it exists is rejected here, enforcing the "all proposals must explain why
    they were created" rule at the persistence boundary."""
    if not rationale or not str(rationale).strip():
        raise ValueError("proposal.rationale must be non-empty — every proposal must explain itself")
    parameters_json = _canonical_proposal_parameters_json(parameters_json)
    now = iso_now()
    record = {name: None for name in _PROPOSAL_INSERT_COLUMNS}
    record.update(
        {
            "id": proposal_id or new_id(),
            "kind": kind,
            "project": project,
            "task_id": task_id,
            "title": title,
            "rationale": rationale,
            "state": state,
            "risk_level": risk_level,
            "policy_json": policy_json,
            "parameters_json": parameters_json,
            "requires_human": 1 if requires_human else 0,
            "version": 0,
            "created_at": now,
            "updated_at": now,
        }
    )
    columns = ", ".join(_PROPOSAL_INSERT_COLUMNS)
    placeholders = ", ".join(f":{name}" for name in _PROPOSAL_INSERT_COLUMNS)
    with connect(db_path) as conn:
        with transaction(conn):
            conn.execute(f"INSERT INTO proposal ({columns}) VALUES ({placeholders})", record)
    return record


def get_proposal(db_path: Path, proposal_id: str) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM proposal WHERE id = ?", (proposal_id,)).fetchone()
        return _row_to_dict(row)


def list_proposals(
    db_path: Path,
    *,
    project: str | None = None,
    states: Iterable[str] | None = None,
    kind: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """List proposal rows, newest first, optionally filtered by project, a set
    of lifecycle `states`, and/or `kind`."""
    clauses: list[str] = []
    params: list[Any] = []
    if project is not None:
        clauses.append("project = ?")
        params.append(project)
    if kind is not None:
        clauses.append("kind = ?")
        params.append(kind)
    states = list(states) if states is not None else None
    if states is not None:
        if not states:
            return []
        placeholders = ", ".join("?" for _ in states)
        clauses.append(f"state IN ({placeholders})")
        params.extend(states)
    if limit < 0:
        raise ValueError(f"limit must be non-negative, got {limit}")
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM proposal{where} ORDER BY created_at DESC, id DESC LIMIT ?", params
        ).fetchall()
        return [dict(row) for row in rows]


# --------------------------------------------------------------------------
# Connection-level primitives (single-transaction building blocks). Each
# operates on an already-open connection inside a caller-managed transaction,
# so several can be composed into ONE atomic unit — see `create_proposal_atomic`
# / `apply_assessment_atomic` / `transition_proposal_atomic`. The public
# wrappers below open their own `connect()`/`transaction()` and delegate to a
# single primitive. Atomicity is NEVER composed by nesting public functions
# (each of which would open and commit its own connection).
# --------------------------------------------------------------------------


def _proposal_next_seq(conn: sqlite3.Connection, table: str, proposal_id: str) -> int:
    row = conn.execute(
        f"SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM {table} WHERE proposal_id = ?",
        (proposal_id,),
    ).fetchone()
    return row["next_seq"]


def _proposal_evidence_insert(
    conn: sqlite3.Connection,
    proposal_id: str,
    *,
    kind: str,
    source: str,
    summary: str | None,
    observed_at: str,
    is_blocker: bool,
    data: dict | None,
    now: str,
) -> int:
    seq = _proposal_next_seq(conn, "proposal_evidence", proposal_id)
    data_json = json.dumps(data, ensure_ascii=False) if data is not None else None
    conn.execute(
        """INSERT INTO proposal_evidence
               (proposal_id, seq, kind, source, summary, observed_at, is_blocker, data_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (proposal_id, seq, kind, source, summary, observed_at, 1 if is_blocker else 0, data_json, now),
    )
    return seq


def _proposal_event_insert(
    conn: sqlite3.Connection,
    proposal_id: str,
    event_type: str,
    *,
    now: str,
    from_state: str | None = None,
    to_state: str | None = None,
    actor: str | None = None,
    reason_code: str | None = None,
    message: str | None = None,
    metadata: dict | None = None,
) -> int:
    seq = _proposal_next_seq(conn, "proposal_event", proposal_id)
    metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata is not None else None
    conn.execute(
        """INSERT INTO proposal_event
               (proposal_id, seq, event_type, from_state, to_state, actor,
                reason_code, message, metadata_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (proposal_id, seq, event_type, from_state, to_state, actor,
         reason_code, message, metadata_json, now),
    )
    return seq


def _proposal_event_from_spec(conn: sqlite3.Connection, proposal_id: str, spec: dict, *, now: str) -> int:
    """Append one audit event from a spec dict (`append_proposal_event` kwargs;
    `event_type` key optional). Used by the atomic composers so callers can pass
    plain dicts."""
    spec = dict(spec)
    return _proposal_event_insert(
        conn,
        proposal_id,
        spec.pop("event_type", "transition"),
        now=now,
        from_state=spec.get("from_state"),
        to_state=spec.get("to_state"),
        actor=spec.get("actor"),
        reason_code=spec.get("reason_code"),
        message=spec.get("message"),
        metadata=spec.get("metadata"),
    )


def _proposal_update(
    conn: sqlite3.Connection,
    proposal_id: str,
    *,
    expected_version: int,
    fields: dict,
    now: str,
) -> tuple[int, str]:
    """CAS + transition-guarded UPDATE inside an open transaction. Returns
    (new_version, resulting_state). Validates `fields` against the allowlist,
    checks the caller's version before interpreting its requested mutation,
    enforces the lifecycle-scoped field allowlist, then refuses an illegal
    `state` transition (via `is_valid_proposal_transition`). Bumps `version`
    and sets `updated_at`. Raises `KeyError`/`LostUpdateError`/
    `ProposalFieldFrozenError`/`InvalidProposalTransitionError` as appropriate."""
    fields = dict(fields)
    fields.pop("version", None)
    fields.pop("created_at", None)
    _validate_updatable_proposal_fields(fields)
    row = conn.execute(
        "SELECT state, version, eligibility_json FROM proposal WHERE id = ?",
        (proposal_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"No such proposal: {proposal_id!r}")
    # CAS is deliberately checked before the state-aware mutation/transition
    # guards. A stale writer must always lose as a stale writer; otherwise a
    # winner's newer state could make the loser's old request look like a hard
    # policy/transition violation and misclassify benign concurrency.
    if row["version"] != expected_version:
        raise LostUpdateError(
            f"Proposal {proposal_id!r} version mismatch: expected {expected_version}, actual {row['version']}"
        )
    _validate_proposal_fields_for_state(
        row["state"],
        fields,
        assessment_persisted=row["eligibility_json"] is not None,
    )
    if "requires_human" in fields and isinstance(fields["requires_human"], bool):
        fields["requires_human"] = 1 if fields["requires_human"] else 0
    if "parameters_json" in fields:
        fields["parameters_json"] = _canonical_proposal_parameters_json(fields["parameters_json"])
    new_state = fields.get("state")
    if new_state is not None and not autonomy_domain.is_valid_proposal_transition(row["state"], new_state):
        raise InvalidProposalTransitionError(
            f"Proposal {proposal_id!r} cannot transition {row['state']!r} -> {new_state!r}"
        )
    fields["updated_at"] = now
    set_clause = ", ".join(f"{key} = :{key}" for key in fields)
    params = dict(fields)
    params["proposal_id"] = proposal_id
    params["expected_version"] = expected_version
    cur = conn.execute(
        f"""UPDATE proposal SET {set_clause}, version = version + 1
            WHERE id = :proposal_id AND version = :expected_version""",
        params,
    )
    if cur.rowcount != 1:
        raise LostUpdateError(f"Proposal {proposal_id!r} update affected {cur.rowcount} rows")
    return expected_version + 1, (new_state if new_state is not None else row["state"])


def update_proposal(db_path: Path, proposal_id: str, *, expected_version: int, fields: dict) -> dict:
    """Compare-and-set update of a `proposal` row, mirroring `update_completion`:
    validates the global field vocabulary, checks CAS first, enforces the
    lifecycle-scoped allowlist, then validates any state transition. Authority
    fields (policy/evidence digest/action parameters/verdict/plan) are mutable
    only before assessment. `requires_human` is coerced to 0/1 if supplied as
    a bool."""
    now = iso_now()
    with connect(db_path) as conn:
        with transaction(conn):
            _proposal_update(conn, proposal_id, expected_version=expected_version, fields=fields, now=now)
            updated = conn.execute("SELECT * FROM proposal WHERE id = ?", (proposal_id,)).fetchone()
            return dict(updated)


def append_proposal_evidence(
    db_path: Path,
    proposal_id: str,
    *,
    kind: str,
    source: str,
    summary: str | None = None,
    observed_at: str,
    is_blocker: bool = False,
    data: dict | None = None,
) -> int:
    """Append one immutable evidence row and return its per-proposal `seq`.
    Evidence may be enriched only before assessment (DRAFT/PROPOSED with no
    persisted verdict). Once assessment has begun, the set is frozen so the
    stored digest and verdict remain reproducible."""
    now = iso_now()
    with connect(db_path) as conn:
        with transaction(conn):
            row = conn.execute(
                "SELECT state, eligibility_json FROM proposal WHERE id = ?",
                (proposal_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"No such proposal: {proposal_id!r}")
            if (
                row["state"] not in {
                    autonomy_domain.ProposalState.DRAFT,
                    autonomy_domain.ProposalState.PROPOSED,
                }
                or row["eligibility_json"] is not None
            ):
                raise ProposalEvidenceFrozenError(
                    f"Proposal {proposal_id!r} evidence is frozen in state {row['state']!r}"
                )
            return _proposal_evidence_insert(
                conn, proposal_id, kind=kind, source=source, summary=summary,
                observed_at=observed_at, is_blocker=is_blocker, data=data, now=now,
            )


def list_proposal_evidence(db_path: Path, proposal_id: str) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM proposal_evidence WHERE proposal_id = ? ORDER BY seq ASC",
            (proposal_id,),
        ).fetchall()
        events = []
        for row in rows:
            item = dict(row)
            raw = item.pop("data_json")
            item["data"] = json.loads(raw) if raw else None
            item["is_blocker"] = bool(item["is_blocker"])
            events.append(item)
        return events


def append_proposal_event(
    db_path: Path,
    proposal_id: str,
    event_type: str,
    *,
    from_state: str | None = None,
    to_state: str | None = None,
    actor: str | None = None,
    reason_code: str | None = None,
    message: str | None = None,
    metadata: dict | None = None,
) -> int:
    """Append one proposal audit event and return its per-proposal `seq`.
    `metadata` is JSON-encoded; callers must never place credentials, tokens, or
    environment dumps in it."""
    now = iso_now()
    with connect(db_path) as conn:
        with transaction(conn):
            return _proposal_event_insert(
                conn, proposal_id, event_type, now=now, from_state=from_state,
                to_state=to_state, actor=actor, reason_code=reason_code,
                message=message, metadata=metadata,
            )


def list_proposal_events(db_path: Path, proposal_id: str, *, limit: int = 500) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """SELECT proposal_id, seq, event_type, from_state, to_state, actor,
                      reason_code, message, metadata_json, created_at
               FROM proposal_event WHERE proposal_id = ? ORDER BY seq ASC LIMIT ?""",
            (proposal_id, limit),
        ).fetchall()
        events = []
        for row in rows:
            event = dict(row)
            raw = event.pop("metadata_json")
            event["metadata"] = json.loads(raw) if raw else None
            events.append(event)
        return events


# --------------------------------------------------------------------------
# Atomic proposal composers — each owns its whole multi-row sequence in ONE
# connection and ONE transaction, so either all rows commit or none do. This is
# the F2 remediation: a crash can never leave a proposal without its required
# evidence/digest/creation-event, nor a verdict without its state transition and
# ASSESSED event, nor a lifecycle move without its audit event.
# --------------------------------------------------------------------------


def create_proposal_atomic(
    db_path: Path,
    *,
    kind: str,
    project: str,
    title: str,
    rationale: str,
    state: str,
    risk_level: str,
    proposal_id: str | None = None,
    task_id: str | None = None,
    policy_json: str | None = None,
    parameters_json: str = "{}",
    requires_human: bool = True,
    evidence_digest: str | None = None,
    evidence: list[dict] | None = None,
    created_event: dict | None = None,
) -> dict:
    """Create a proposal row, its (immutable) evidence rows, its evidence digest,
    and its CREATED audit event in ONE transaction. Either all commit or none do
    — there is never a persisted proposal without its rationale (rejected here if
    blank), evidence, digest, and creation event. `evidence` is a list of dicts
    (kind/source/summary/observed_at/is_blocker/data); `created_event` is a dict
    of `append_proposal_event` kwargs (`event_type` defaults to 'created').
    Returns the persisted proposal row. Idempotency is by `proposal_id`: a retry
    with the same id hits the PRIMARY KEY (IntegrityError) rather than creating a
    duplicate; a retry after a rolled-back attempt succeeds cleanly."""
    if not rationale or not str(rationale).strip():
        raise ValueError("proposal.rationale must be non-empty — every proposal must explain itself")
    parameters_json = _canonical_proposal_parameters_json(parameters_json)
    now = iso_now()
    pid = proposal_id or new_id()
    record = {name: None for name in _PROPOSAL_INSERT_COLUMNS}
    record.update(
        {
            "id": pid,
            "kind": kind,
            "project": project,
            "task_id": task_id,
            "title": title,
            "rationale": rationale,
            "state": state,
            "risk_level": risk_level,
            "policy_json": policy_json,
            "parameters_json": parameters_json,
            "evidence_digest": evidence_digest,
            "requires_human": 1 if requires_human else 0,
            "version": 0,
            "created_at": now,
            "updated_at": now,
        }
    )
    columns = ", ".join(_PROPOSAL_INSERT_COLUMNS)
    placeholders = ", ".join(f":{name}" for name in _PROPOSAL_INSERT_COLUMNS)
    with connect(db_path) as conn:
        with transaction(conn):
            conn.execute(f"INSERT INTO proposal ({columns}) VALUES ({placeholders})", record)
            for e in evidence or []:
                _proposal_evidence_insert(
                    conn, pid, kind=e["kind"], source=e["source"], summary=e.get("summary"),
                    observed_at=e["observed_at"], is_blocker=bool(e.get("is_blocker", False)),
                    data=e.get("data"), now=now,
                )
            if created_event is not None:
                _proposal_event_from_spec(conn, pid, created_event, now=now)
            stored = conn.execute("SELECT * FROM proposal WHERE id = ?", (pid,)).fetchone()
            return dict(stored)


def apply_assessment_atomic(
    db_path: Path,
    proposal_id: str,
    *,
    expected_version: int,
    verdict_fields: dict,
    assessed_event: dict,
    transitions: list[dict],
) -> dict:
    """Persist an assessment verdict, its ASSESSED audit event, and the resulting
    ordered state transitions (each with its own audit event) in ONE transaction.

    A single CAS on `expected_version` guards the whole unit: a stale/concurrent
    assessor loses with `LostUpdateError` and writes nothing. Guarantees: no
    verdict without its transitions/events (and vice versa); the audit sequence
    stays monotonic; and — because the caller only invokes this while the
    proposal is still in a pre-assessment state — exactly one ASSESSED event per
    committed assessment. `transitions` is an ordered list of
    ``{"new_state": str, "event": {<append_proposal_event kwargs>},
    "extra_fields": {<optional proposal columns>}}``; each transition's version
    is chained from the previous update within the same locked transaction."""
    now = iso_now()
    with connect(db_path) as conn:
        with transaction(conn):
            version, _state = _proposal_update(
                conn, proposal_id, expected_version=expected_version,
                fields=dict(verdict_fields), now=now,
            )
            _proposal_event_from_spec(conn, proposal_id, {"event_type": "assessed", **assessed_event}, now=now)
            for t in transitions:
                fields = dict(t.get("extra_fields") or {})
                fields["state"] = t["new_state"]
                event_spec = dict(t["event"])
                reason_code = event_spec.get("reason_code")
                if reason_code is not None and "last_reason_code" not in fields:
                    fields["last_reason_code"] = reason_code
                version, _state = _proposal_update(
                    conn, proposal_id, expected_version=version, fields=fields, now=now,
                )
                _proposal_event_from_spec(conn, proposal_id, event_spec, now=now)
            updated = conn.execute("SELECT * FROM proposal WHERE id = ?", (proposal_id,)).fetchone()
            return dict(updated)


def transition_proposal_atomic(
    db_path: Path,
    proposal_id: str,
    *,
    expected_version: int,
    new_state: str,
    event: dict,
    fields: dict | None = None,
) -> dict:
    """Apply one state transition and its audit event atomically (CAS update +
    event in one transaction), so a lifecycle move can never persist a state
    change without its audit event, or an audit event without the state change."""
    now = iso_now()
    upd_fields = dict(fields or {})
    upd_fields["state"] = new_state
    with connect(db_path) as conn:
        with transaction(conn):
            _proposal_update(conn, proposal_id, expected_version=expected_version, fields=upd_fields, now=now)
            _proposal_event_from_spec(conn, proposal_id, event, now=now)
            updated = conn.execute("SELECT * FROM proposal WHERE id = ?", (proposal_id,)).fetchone()
            return dict(updated)


# --------------------------------------------------------------------------
# Execution queue (ADR 0007) — the SQLite home of `execution_queue.json`
#
# These are storage primitives only. Every rule about *what* a queue entry may
# contain, when it becomes ready, and what launching it means stays in
# `command_center.execution_queue`; this layer just stores and returns rows.
# During the dual-write phases the JSON file remains authoritative, so nothing
# here may raise on data the JSON store would have accepted.
# --------------------------------------------------------------------------

_QUEUE_ENTRY_COLUMNS: tuple[str, ...] = (
    "id",
    "task_id",
    "project",
    "state",
    "reason",
    "run_id",
    "added_at",
    "evaluated_at",
    "launched_at",
)


def replace_queue_entries(db_path: Path, entries: list[dict]) -> None:
    """Persist `entries` as the complete queue, in the given order.

    Whole-list replacement rather than per-entry upsert, deliberately: that is
    exactly the semantics `execution_queue.save_queue` has today, so during
    dual-write the two stores cannot drift through a difference in *how* they
    are written. `position` preserves list order, which the queue's display and
    planning both depend on.

    Unknown keys on an entry are ignored rather than rejected — the JSON store
    accepts them, and a dual-write phase where SQLite is stricter than the
    authoritative store would fail on data that is, by definition, valid."""
    rows = [
        {
            **{column: entry.get(column) for column in _QUEUE_ENTRY_COLUMNS},
            "position": index,
        }
        for index, entry in enumerate(entries)
    ]
    columns = ", ".join((*_QUEUE_ENTRY_COLUMNS, "position"))
    placeholders = ", ".join(f":{name}" for name in (*_QUEUE_ENTRY_COLUMNS, "position"))
    with connect(db_path) as conn:
        with transaction(conn):
            conn.execute("DELETE FROM queue_entry")
            if rows:
                conn.executemany(
                    f"INSERT INTO queue_entry ({columns}) VALUES ({placeholders})", rows
                )


def list_queue_entries(db_path: Path) -> list[dict]:
    """Every queue entry in stored order, shaped exactly like a JSON entry so a
    divergence check can compare the two directly, with no translation step of
    its own to be wrong about."""
    with connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT {', '.join(_QUEUE_ENTRY_COLUMNS)} FROM queue_entry ORDER BY position ASC"
        ).fetchall()
        return [dict(row) for row in rows]
