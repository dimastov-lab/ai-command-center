"""Wave 1 "new engine" table-families (W1-DATA-EVENTS): the persistence layer
behind the Советник (advisor_proposal), «Мой день» (owner_item) and Дайджест
(digest_item) surfaces.

This module is the **repository tier** of the Wave-1 stack (routes → service →
repository → db). It owns three additive tables that are wholly separate from
the autonomy `proposal` family in ``proposal.py`` — the names never collide and
the two carry different lifecycles. Every write goes through the shared
``connect()``/``transaction()`` primitives (WAL, ``BEGIN IMMEDIATE`` write lock,
per-row ``version`` compare-and-set), so the single-writer discipline the rest
of ``runtime.db`` follows holds here too.

Every cross-reference to another db name goes through the package facade
(``import command_center.runtime.db as db``) so tests and callers that
monkeypatch facade attributes keep intercepting internal calls exactly as they
do for the other table-family modules.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

import command_center.runtime.db as db  # facade (late-bound; see docstring)

# --------------------------------------------------------------------------
# advisor_proposal — Советник
# --------------------------------------------------------------------------

#: The advisor-proposal kinds (mirrors ``api.models.ProposalKind``). Validated at
#: the persistence boundary so a malformed kind can never reach a stored row.
ADVISOR_PROPOSAL_KINDS: frozenset[str] = frozenset(
    {"trend", "ux", "optimization", "competitor", "feedback"}
)

#: The advisor-proposal lifecycle. ``converted`` is the terminal state a
#: proposal reaches when it is promoted into a task; ``dismissed`` is the other
#: terminal state. Anything not listed here is refused before it reaches SQL.
ADVISOR_PROPOSAL_STATUSES: frozenset[str] = frozenset(
    {"new", "accepted", "dismissed", "converted"}
)

ADVISOR_PROPOSAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "new": frozenset({"accepted", "dismissed", "converted"}),
    "accepted": frozenset({"converted", "dismissed"}),
    "dismissed": frozenset(),
    "converted": frozenset(),
}


class InvalidAdvisorProposalTransitionError(Exception):
    """Raised when an advisor-proposal status change is not an allowed edge in
    ``ADVISOR_PROPOSAL_TRANSITIONS`` (a backward jump, or any move out of a
    terminal state)."""


_ADVISOR_PROPOSAL_COLUMNS: tuple[str, ...] = (
    "id",
    "kind",
    "title",
    "body",
    "expected_gain",
    "effort",
    "project_ref",
    "status",
    "promoted_task_id",
    "version",
    "created_at",
    "updated_at",
)


def create_advisor_proposal(
    db_path: Path,
    *,
    kind: str,
    title: str,
    project_ref: str,
    body: str = "",
    expected_gain: str | None = None,
    effort: str | None = None,
    proposal_id: str | None = None,
    status: str = "new",
) -> dict:
    """Insert one ``advisor_proposal`` row in its initial state and return it.

    ``kind`` must be one of :data:`ADVISOR_PROPOSAL_KINDS` and ``project_ref``
    must be non-empty — a proposal always belongs to a project so it can be
    routed to the right board (and redacted when that project is sensitive)."""
    if kind not in ADVISOR_PROPOSAL_KINDS:
        raise ValueError(f"advisor_proposal.kind must be one of {sorted(ADVISOR_PROPOSAL_KINDS)}, got {kind!r}")
    if not project_ref or not str(project_ref).strip():
        raise ValueError("advisor_proposal.project_ref must be non-empty")
    if status not in ADVISOR_PROPOSAL_STATUSES:
        raise ValueError(f"advisor_proposal.status must be one of {sorted(ADVISOR_PROPOSAL_STATUSES)}, got {status!r}")
    now = db.iso_now()
    record = {name: None for name in _ADVISOR_PROPOSAL_COLUMNS}
    record.update(
        {
            "id": proposal_id or db.new_id(),
            "kind": kind,
            "title": title,
            "body": body,
            "expected_gain": expected_gain,
            "effort": effort,
            "project_ref": project_ref,
            "status": status,
            "promoted_task_id": None,
            "version": 0,
            "created_at": now,
            "updated_at": now,
        }
    )
    columns = ", ".join(_ADVISOR_PROPOSAL_COLUMNS)
    placeholders = ", ".join(f":{name}" for name in _ADVISOR_PROPOSAL_COLUMNS)
    with db.connect(db_path) as conn:
        with db.transaction(conn):
            conn.execute(
                f"INSERT INTO advisor_proposal ({columns}) VALUES ({placeholders})",
                record,
            )
    return record


def get_advisor_proposal(db_path: Path, proposal_id: str) -> dict | None:
    with db.connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM advisor_proposal WHERE id = ?", (proposal_id,)
        ).fetchone()
        return db._row_to_dict(row)


def list_advisor_proposals(
    db_path: Path,
    *,
    project: str | None = None,
    statuses: Iterable[str] | None = None,
    kind: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """List advisor proposals, newest first, optionally filtered by project, a
    set of ``statuses`` and/or a ``kind``. ``limit``/``offset`` page the result
    (stable order: ``created_at DESC, id DESC``)."""
    if limit < 0:
        raise ValueError(f"limit must be non-negative, got {limit}")
    if offset < 0:
        raise ValueError(f"offset must be non-negative, got {offset}")
    clauses: list[str] = []
    params: list[Any] = []
    if project is not None:
        clauses.append("project_ref = ?")
        params.append(project)
    if kind is not None:
        clauses.append("kind = ?")
        params.append(kind)
    statuses = list(statuses) if statuses is not None else None
    if statuses is not None:
        if not statuses:
            return []
        placeholders = ", ".join("?" for _ in statuses)
        clauses.append(f"status IN ({placeholders})")
        params.extend(statuses)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend([limit, offset])
    with db.connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM advisor_proposal{where} "
            "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [dict(row) for row in rows]


def _advisor_proposal_transition(
    conn: sqlite3.Connection,
    proposal_id: str,
    *,
    expected_version: int,
    new_status: str,
    extra_fields: dict | None,
    now: str,
) -> dict:
    """CAS + transition-guarded status update inside an open transaction.

    Checks the caller's ``version`` before interpreting the request (a stale
    writer always loses as a stale writer), then refuses an illegal status edge,
    bumps ``version`` and sets ``updated_at``. Returns the updated row dict."""
    row = conn.execute(
        "SELECT status, version FROM advisor_proposal WHERE id = ?",
        (proposal_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"No such advisor_proposal: {proposal_id!r}")
    if row["version"] != expected_version:
        raise db.LostUpdateError(
            f"advisor_proposal {proposal_id!r} version mismatch: "
            f"expected {expected_version}, actual {row['version']}"
        )
    if new_status not in ADVISOR_PROPOSAL_STATUSES:
        raise ValueError(f"unknown advisor_proposal status {new_status!r}")
    if new_status not in ADVISOR_PROPOSAL_TRANSITIONS.get(row["status"], frozenset()):
        raise InvalidAdvisorProposalTransitionError(
            f"advisor_proposal {proposal_id!r} cannot transition "
            f"{row['status']!r} -> {new_status!r}"
        )
    fields = dict(extra_fields or {})
    fields["status"] = new_status
    fields["updated_at"] = now
    set_clause = ", ".join(f"{key} = :{key}" for key in fields)
    params = dict(fields)
    params["proposal_id"] = proposal_id
    params["expected_version"] = expected_version
    cur = conn.execute(
        f"UPDATE advisor_proposal SET {set_clause}, version = version + 1 "
        "WHERE id = :proposal_id AND version = :expected_version",
        params,
    )
    if cur.rowcount != 1:
        raise db.LostUpdateError(
            f"advisor_proposal {proposal_id!r} update affected {cur.rowcount} rows"
        )
    updated = conn.execute(
        "SELECT * FROM advisor_proposal WHERE id = ?", (proposal_id,)
    ).fetchone()
    return dict(updated)


def set_advisor_proposal_status(
    db_path: Path, proposal_id: str, *, expected_version: int, status: str
) -> dict:
    """Compare-and-set status update guarded by the transition allowlist."""
    now = db.iso_now()
    with db.connect(db_path) as conn:
        with db.transaction(conn):
            return db._advisor_proposal_transition(
                conn, proposal_id, expected_version=expected_version,
                new_status=status, extra_fields=None, now=now,
            )


def promote_advisor_proposal(
    db_path: Path, proposal_id: str, *, expected_version: int, task_id: str
) -> dict:
    """Record that ``proposal_id`` was promoted into ``task_id``: move it to the
    terminal ``converted`` status and stamp ``promoted_task_id``, atomically.

    The caller creates the task through the existing tasks path *first*; this
    only records the link — the persistence layer never launches or creates a
    task itself. Idempotency is the caller's: a second promote of an already-
    ``converted`` proposal is refused by the transition guard, so a retry cannot
    silently create a second task-link."""
    if not task_id:
        raise ValueError("promote_advisor_proposal requires a task_id")
    now = db.iso_now()
    with db.connect(db_path) as conn:
        with db.transaction(conn):
            return db._advisor_proposal_transition(
                conn, proposal_id, expected_version=expected_version,
                new_status="converted", extra_fields={"promoted_task_id": task_id},
                now=now,
            )


# --------------------------------------------------------------------------
# owner_item — «Мой день»
# --------------------------------------------------------------------------

_OWNER_ITEM_COLUMNS: tuple[str, ...] = (
    "id",
    "title",
    "detail",
    "due",
    "done",
    "source_ref",
    "version",
    "created_at",
    "updated_at",
)


def create_owner_item(
    db_path: Path,
    *,
    title: str,
    detail: str | None = None,
    due: str | None = None,
    source_ref: str | None = None,
    item_id: str | None = None,
    done: bool = False,
) -> dict:
    """Insert one ``owner_item`` row and return it. ``title`` must be non-empty."""
    if not title or not str(title).strip():
        raise ValueError("owner_item.title must be non-empty")
    now = db.iso_now()
    record = {
        "id": item_id or db.new_id(),
        "title": title,
        "detail": detail,
        "due": due,
        "done": 1 if done else 0,
        "source_ref": source_ref,
        "version": 0,
        "created_at": now,
        "updated_at": now,
    }
    columns = ", ".join(_OWNER_ITEM_COLUMNS)
    placeholders = ", ".join(f":{name}" for name in _OWNER_ITEM_COLUMNS)
    with db.connect(db_path) as conn:
        with db.transaction(conn):
            conn.execute(
                f"INSERT INTO owner_item ({columns}) VALUES ({placeholders})",
                record,
            )
    return record


def get_owner_item(db_path: Path, item_id: str) -> dict | None:
    with db.connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM owner_item WHERE id = ?", (item_id,)
        ).fetchone()
        return db._row_to_dict(row)


def list_owner_items(
    db_path: Path,
    *,
    done: bool | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """List owner items, newest first, optionally filtered by ``done`` state,
    paged by ``limit``/``offset``."""
    if limit < 0:
        raise ValueError(f"limit must be non-negative, got {limit}")
    if offset < 0:
        raise ValueError(f"offset must be non-negative, got {offset}")
    clauses: list[str] = []
    params: list[Any] = []
    if done is not None:
        clauses.append("done = ?")
        params.append(1 if done else 0)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend([limit, offset])
    with db.connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM owner_item{where} "
            "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [dict(row) for row in rows]


def set_owner_item_done(
    db_path: Path, item_id: str, *, expected_version: int, done: bool
) -> dict:
    """Compare-and-set toggle of an owner item's ``done`` flag."""
    now = db.iso_now()
    with db.connect(db_path) as conn:
        with db.transaction(conn):
            row = conn.execute(
                "SELECT version FROM owner_item WHERE id = ?", (item_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"No such owner_item: {item_id!r}")
            if row["version"] != expected_version:
                raise db.LostUpdateError(
                    f"owner_item {item_id!r} version mismatch: "
                    f"expected {expected_version}, actual {row['version']}"
                )
            cur = conn.execute(
                "UPDATE owner_item SET done = :done, updated_at = :now, "
                "version = version + 1 WHERE id = :id AND version = :expected",
                {"done": 1 if done else 0, "now": now, "id": item_id,
                 "expected": expected_version},
            )
            if cur.rowcount != 1:
                raise db.LostUpdateError(
                    f"owner_item {item_id!r} update affected {cur.rowcount} rows"
                )
            updated = conn.execute(
                "SELECT * FROM owner_item WHERE id = ?", (item_id,)
            ).fetchone()
            return dict(updated)


# --------------------------------------------------------------------------
# digest_item — Дайджест
# --------------------------------------------------------------------------

_DIGEST_ITEM_COLUMNS: tuple[str, ...] = (
    "id",
    "title",
    "body",
    "category",
    "refs_json",
    "created_at",
)


def create_digest_item(
    db_path: Path,
    *,
    title: str,
    body: str = "",
    category: str | None = None,
    refs: list[str] | None = None,
    item_id: str | None = None,
) -> dict:
    """Insert one write-once ``digest_item`` row and return it (with ``refs``
    decoded back to a list). ``refs`` is a list of opaque string references."""
    if not title or not str(title).strip():
        raise ValueError("digest_item.title must be non-empty")
    refs = list(refs or [])
    if not all(isinstance(ref, str) for ref in refs):
        raise ValueError("digest_item.refs must be a list of strings")
    now = db.iso_now()
    record = {
        "id": item_id or db.new_id(),
        "title": title,
        "body": body,
        "category": category,
        "refs_json": json.dumps(refs, ensure_ascii=False),
        "created_at": now,
    }
    columns = ", ".join(_DIGEST_ITEM_COLUMNS)
    placeholders = ", ".join(f":{name}" for name in _DIGEST_ITEM_COLUMNS)
    with db.connect(db_path) as conn:
        with db.transaction(conn):
            conn.execute(
                f"INSERT INTO digest_item ({columns}) VALUES ({placeholders})",
                record,
            )
    return _decode_digest_row(record)


def get_digest_item(db_path: Path, item_id: str) -> dict | None:
    with db.connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM digest_item WHERE id = ?", (item_id,)
        ).fetchone()
        return _decode_digest_row(dict(row)) if row is not None else None


def list_digest_items(
    db_path: Path,
    *,
    category: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """List digest items, newest first, optionally filtered by ``category``,
    paged by ``limit``/``offset``."""
    if limit < 0:
        raise ValueError(f"limit must be non-negative, got {limit}")
    if offset < 0:
        raise ValueError(f"offset must be non-negative, got {offset}")
    clauses: list[str] = []
    params: list[Any] = []
    if category is not None:
        clauses.append("category = ?")
        params.append(category)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend([limit, offset])
    with db.connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM digest_item{where} "
            "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [_decode_digest_row(dict(row)) for row in rows]


def _decode_digest_row(row: dict) -> dict:
    """Return a copy of a digest row with ``refs_json`` decoded to a ``refs``
    list (the JSON column stays internal to the repository)."""
    out = dict(row)
    raw = out.pop("refs_json", "[]")
    out["refs"] = json.loads(raw) if raw else []
    return out
