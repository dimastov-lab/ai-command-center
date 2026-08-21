"""The way back: PostgreSQL -> SQLite, alive only for the write soak (SRV-07g).

Stage one of the cutover moves *reads*. It is reversible for a structural
reason rather than a procedural one: writes still go to SQLite and are mirrored
forward, so SQLite remains a complete system of record the whole time and
rolling back is a flag with nothing to reconcile.

Stage two moves *writes*, and that symmetry ends. A row written natively into
PostgreSQL has no counterpart in SQLite, and nothing in the forward direction
will ever give it one — the forward mirror copies the authority, and after
stage two the authority is the other side. So the honest description of stage
two without this module is **one-way**: the only recovery is a restore from
backup, which loses every row written after the switch. For a task queue that
is not a degraded recovery, it is a wrong one — the rows lost are exactly the
work the system accepted and promised to run.

This module is what makes stage two reversible: the same rows, carried the
other way, for as long as the write soak lasts.

**Built from the same declarations, and not merely in the same style.** The
PostgreSQL side is read through `parity_gate.subjects()` — the forward mirrors
themselves — so the rows arriving here are already in the authority's own shape,
converted by the table's own `ColumnCodec`. The SQLite side is written from
`MirroredTable.columns` and `MirroredTable.key`. There is no second inventory of
tables, no second column list, and no second opinion about what a row is. A table
that gains a column gains it in both directions, and a table added to the
migration is carried back without anybody remembering to add it here.

The carry-back order comes from `MirroredTable.references`, topologically
sorted, and reproduces the five waves `docs/srv01b-schema-map.md` derived from
the foreign-key graph. Derived rather than listed: SQLite is opened with
`PRAGMA foreign_keys=ON`, so a child written before its parent is refused, and
that refusal is the check that the order is right.

**Equality is decided by nobody new.** `reverse_divergence` hands the
PostgreSQL rows and the SQLite sink to the same `divergence_against(spec)` the
forward gate uses, with the roles swapped. Writing a second comparison for the
backward direction would give the migration two answers to the one question
that decides whether a rollback loses data.

**What this proves and what it does not.** `prove()` runs a carry-back and then
demands zero `missing` and zero `fields` in the backward direction: everything
PostgreSQL holds, SQLite also holds, field for field. That is precisely the
rollback-safety claim. It says nothing about a hook installed on some future
native write path, because there is nothing outside this repository's control it
could honestly assert about one — the drain *is* the mechanism, and a hook, if
SRV-09 adds one, is an optimisation of the window between drains.

The window is worth naming exactly. Rolling back with PostgreSQL reachable
drains first and loses nothing. Rolling back with PostgreSQL *gone* loses what
was written since the last drain — which is why the drain runs on a short
interval during the soak, and why the drain interval, not the backup interval,
is the number that bounds a stage-two loss.

**One asymmetry, and it is only about `jsonb`.** Every other column renders back
into exactly the text the authority wrote — that is what `ColumnCodec.to_authority`
is for. `jsonb` has no such rendering: `{"b": 1, "a": 2}` comes back from
PostgreSQL 17.6 as `{"a": 2, "b": 1}` (measured, see `ColumnCodec`), so a row
carried back may hold different *text* in a JSON column than the row SQLite
originally held. It holds the same *value*, and `divergence` compares those
columns as parsed values, so the reconciliation is honest either way. A consumer
that compares JSON columns as text — none does today — would see a difference
that is not one.

## Expand-contract

This is a temporary layer, and it is declared as one. `OBLIGATION` carries the
owner, the date it must be gone by, the task that removes it, and the name of
the negative test that proves what its absence costs. Two tests enforce that:
one fails once the deadline passes, one fails if the named negative test stops
existing. A temporary layer with no expiry is a permanent layer that nobody
decided to keep.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from command_center.db import parity_gate
from command_center.db.table_mirror import MirroredTable, divergence_against

__all__ = [
    "CarryReport",
    "Obligation",
    "OBLIGATION",
    "Proof",
    "QueueFileSink",
    "SqliteSink",
    "TableCarry",
    "carry_back",
    "carry_order",
    "prove",
    "reverse_divergence",
    "sinks",
]


# --- the expand-contract declaration -----------------------------------------


@dataclass(frozen=True)
class Obligation:
    """What a temporary layer owes, so that it can be made to go away.

    Every field is load-bearing and none is decorative:

    * `owner` — who answers for it. A layer whose owner is "the migration" is a
      layer nobody removes.
    * `deadline` — the date by which it must be gone. Enforced by a test that
      turns red on that date rather than by a calendar entry, because the test
      is the thing that runs.
    * `removal_task` — where the removal is tracked. A deadline with no task
      makes the red test a puzzle instead of an instruction.
    * `negative_test` — the test that demonstrates what this layer's *absence*
      costs. Named here so that deleting it breaks the build: an expand-contract
      layer justified only by prose is one that gets removed early by someone
      who reads the prose as caution.
    """

    owner: str
    deadline: date
    removal_task: str
    negative_test: str
    reason: str

    def expired(self, today: date) -> bool:
        return today > self.deadline

    def render(self) -> str:
        return (
            f"reverse mirror (PostgreSQL -> SQLite): owner {self.owner}, "
            f"remove by {self.deadline.isoformat()} under {self.removal_task}; "
            f"its absence is demonstrated by {self.negative_test}"
        )


#: The declaration for this layer.
#:
#: The deadline is 90 days from the day the layer was declared (2026-08-21),
#: which is the outside of SRV-09's own write-cutover-plus-soak window. It is
#: deliberately a date rather than "when the soak ends": a soak that quietly
#: never ends is exactly how a rollback path becomes a permanent second writer,
#: and a second writer into a store nothing reads is a store that drifts unseen.
OBLIGATION = Obligation(
    owner="Release Engineering",
    deadline=date(2026, 11, 19),
    removal_task="VOYN-W0-AICC-SRV-09-REMOVE-REVERSE-MIRROR",
    negative_test=(
        "tests/db/test_two_stage_cutover.py::"
        "test_a_native_postgres_row_is_lost_when_the_reverse_mirror_is_absent"
    ),
    reason=(
        "Stage two of the cutover writes natively into PostgreSQL, and those rows "
        "have no counterpart in SQLite. Without this layer the only rollback is a "
        "restore from backup, which loses every row written after the switch — "
        "for the task queue, the work the system accepted and promised to run."
    ),
)


# --- one table's way back ----------------------------------------------------


class SqliteSink:
    """One mirrored table's rows, written back into the runtime database.

    Reads through `parity_gate.Authority`, which is the same `SELECT` of the
    declared columns the forward gate compares against — so the backward
    comparison and the forward one are looking at the identical rows and there
    is no second stored reader to keep in step.

    Writes with SQLite's own upsert on the declared key. `upsert` rather than
    `insert` for the reason the forward mirror gives: the drain runs repeatedly
    by design, and an insert-only sink would fail its second run on rows it
    wrote itself.
    """

    name = "sqlite"

    def __init__(self, spec: MirroredTable, authority: parity_gate.Authority) -> None:
        self.spec = spec
        self._authority = authority

    def list_records(self) -> list[dict]:
        return self._authority.rows(self.spec)

    def write(self, records: list[dict]) -> int:
        """Upsert every record; return how many statements were executed.

        One transaction for the whole table. A half-written table is a table
        whose parents may be present and whose children are not, and the next
        drain would have to discover that by foreign-key failure rather than by
        starting from a consistent row set.
        """
        spec = self.spec
        if not records:
            return 0
        columns = spec.columns
        keys = spec.key_columns
        assignments = ", ".join(
            f"{name} = excluded.{name}" for name in columns if name not in keys
        )
        # A table that is all key (none today) has nothing to assign; `DO
        # NOTHING` is then the same statement, and writing `SET` with an empty
        # list is a syntax error rather than a no-op.
        conflict = f"DO UPDATE SET {assignments}" if assignments else "DO NOTHING"
        statement = (
            f"INSERT INTO {spec.table} ({', '.join(columns)}) "
            f"VALUES ({', '.join(['?'] * len(columns))}) "
            f"ON CONFLICT ({', '.join(keys)}) {conflict}"
        )
        rows = [tuple(_stored(spec, name, record.get(name)) for name in columns) for record in records]
        with _writable(self._authority.runtime_db) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.executemany(statement, rows)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return len(rows)


class QueueFileSink:
    """The queue, written back into the JSON file that is its authority.

    The queue's contract is whole-list replacement and its order is data, so
    this sink replaces rather than upserts — the same contract
    `command_center/queue_store.py` states for the forward direction, in the
    other direction.

    It writes through `execution_queue.save_queue` under `execution_queue.queue_lock`
    rather than through a JSON writer of its own, and both halves matter. The
    lock is the queue's own read-modify-write mutual exclusion: a rollback that
    replaced the file outside it would race a concurrent enqueue and lose one of
    the two. And `save_queue` writes the JSON *and* its SQLite mirror, which is
    precisely the topology a rollback is restoring — a JSON authority with a
    runtime mirror beside it, which is what the system had before the cutover
    began.

    Imported inside the methods, not at module scope. `mirror_registry` imports
    every module in `command_center/db/` that mentions a mirror in order to
    discover declarations, and a module-level import here would drag the
    application layer into that discovery on a machine that has no reason to
    load it.
    """

    name = "queue-json"

    def __init__(self, spec: MirroredTable, authority: parity_gate.Authority, root: Path) -> None:
        self.spec = spec
        self._authority = authority
        self._root = Path(root)

    def list_records(self) -> list[dict]:
        return self._authority.queue_entries()

    def write(self, records: list[dict]) -> int:
        from command_center import execution_queue

        with execution_queue.queue_lock(self._root):
            execution_queue.save_queue(self._root, list(records))
        return len(records)


def sinks(authority: parity_gate.Authority, *, root: Path | None = None) -> dict[str, Any]:
    """`{table: sink}` for every table the forward gate covers.

    Discovered from `parity_gate.subjects()`, so the two directions cover the
    same 33 tables by construction. A table that is mirrored forward and has no
    way back would be the defect this module exists to prevent, and it cannot be
    expressed here.

    `root` is the application data root the queue's JSON authority lives under.
    It defaults to the queue file's grandparent — `<root>/data/execution_queue.json`
    is the layout `execution_queue.queue_file_path` builds — so a caller that
    already told `Authority` where the queue is does not have to say it twice.
    """
    data_root = Path(root) if root is not None else authority.queue_file.parent.parent
    built: dict[str, Any] = {}
    for table, subject in parity_gate.subjects().items():
        if subject.ordered:
            built[table] = QueueFileSink(subject.spec, authority, data_root)
        else:
            built[table] = SqliteSink(subject.spec, authority)
    return built


# --- order -------------------------------------------------------------------


def carry_order() -> tuple[str, ...]:
    """Table names in an order no foreign key refuses: parents before children.

    A topological sort of `MirroredTable.references`, which is the same graph
    `docs/srv01b-schema-map.md` derived its five migration waves from — the map
    proved it has no cycles and no self-references, so an order exists and needs
    no deferred constraints. Derived here rather than restated: a table that
    gains a parent gains its position in this order at the same moment, and the
    schema map's wave table cannot drift away from the code that walks it.

    A reference to a table outside the mirrored set is ignored rather than
    refused. It constrains nothing about the order *within* the set, and the
    table it names is signed out of scope in `tests/db/test_mirror_coverage.py`,
    which is where that decision belongs.
    """
    specs = {table: subject.spec for table, subject in parity_gate.subjects().items()}
    ordered: list[str] = []
    placed: set[str] = set()

    def place(table: str, walking: tuple[str, ...]) -> None:
        if table in placed:
            return
        if table in walking:
            # Unreachable against the accepted schema, which the map proved
            # acyclic. Raised rather than broken out of, because silently
            # emitting a partial order would produce foreign-key failures a long
            # way from their cause.
            cycle = " -> ".join((*walking[walking.index(table):], table))
            raise ValueError(f"the declared foreign-key graph has a cycle: {cycle}")
        for parent in sorted(set(specs[table].references.values())):
            if parent in specs:
                place(parent, (*walking, table))
        placed.add(table)
        ordered.append(table)

    for table in sorted(specs):
        place(table, ())
    return tuple(ordered)


# --- the drain ---------------------------------------------------------------


@dataclass(frozen=True)
class TableCarry:
    """What one table's carry-back did, or why it did nothing."""

    table: str
    rows: int
    error: str = ""

    @property
    def carried(self) -> bool:
        return not self.error


@dataclass(frozen=True)
class CarryReport:
    """One drain, table by table, in the order it ran."""

    carries: tuple[TableCarry, ...] = ()

    @property
    def complete(self) -> bool:
        return all(carry.carried for carry in self.carries)

    @property
    def rows(self) -> int:
        return sum(carry.rows for carry in self.carries)

    def failures(self) -> tuple[TableCarry, ...]:
        return tuple(carry for carry in self.carries if not carry.carried)

    def render(self) -> str:
        lines = [
            f"carry-back: {'complete' if self.complete else 'INCOMPLETE'}, "
            f"{self.rows} rows over {len(self.carries)} tables"
        ]
        for carry in self.carries:
            if carry.carried:
                lines.append(f"  {carry.table:26} {carry.rows:>8} rows")
            else:
                lines.append(f"  {carry.table:26} FAILED: {carry.error}")
        return "\n".join(lines)


def carry_back(
    *,
    authority: parity_gate.Authority,
    connection_factory: Callable[[], Any] | None = None,
    root: Path | None = None,
) -> CarryReport:
    """Copy everything PostgreSQL holds back into SQLite, parents first.

    Whole tables rather than a change feed, and that is the deliberate choice:
    PostgreSQL carries no record of *which* rows were written natively — the
    forward mirror's rows and the native ones are the same rows in the same
    tables — so anything narrower would need a second source of truth about what
    changed, invented for the rollback path and exercised only by it. A whole
    copy is idempotent, needs nothing remembered between runs, and is bounded by
    the same table sizes the forward backfill already moves.

    A table that fails is recorded and the drain continues, because stopping
    would leave the tables after it untouched and make a partial drain look like
    a complete one to everything downstream. `CarryReport.complete` is what says
    which happened, and `prove` — not this function — is what decides whether a
    rollback may proceed.
    """
    if connection_factory is None:
        from command_center.db import pool

        with pool.connection() as conn:
            return _carry(conn, authority, root)
    with connection_factory() as conn:
        return _carry(conn, authority, root)


def _carry(conn: Any, authority: parity_gate.Authority, root: Path | None) -> CarryReport:
    subjects = parity_gate.subjects()
    destinations = sinks(authority, root=root)

    @contextmanager
    def pinned() -> Iterator[Any]:
        yield conn

    carries: list[TableCarry] = []
    for table in carry_order():
        subject = subjects[table]
        try:
            source = subject.build_mirror(pinned)
            rows = list(source.list_records())
            written = destinations[table].write(rows)
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            carries.append(TableCarry(table, 0, f"{type(exc).__name__}: {exc}"))
            continue
        carries.append(TableCarry(table, written))
    return CarryReport(tuple(carries))


# --- the backward reconciliation ---------------------------------------------

#: Divergence shapes that mean a rollback would lose something, and the two that
#: do not.
#:
#: Backward, `missing` means PostgreSQL holds a row SQLite does not — a row that
#: a rollback would drop on the floor — and `fields` means both hold it and
#: disagree, so a rollback would keep the wrong version. Those two are the
#: rollback-safety claim, and both must be zero.
#:
#: `unexpected` backward means *SQLite* holds a row PostgreSQL does not. That is
#: not a loss on rollback — the row survives it — so it does not block one. It is
#: still a real question about the forward mirror, and the forward parity gate is
#: where it is asked and answered; reporting it here without blocking on it is
#: the difference between two checks with distinct claims and one check with a
#: tolerance.
LOSS_SHAPES = (parity_gate.SHAPE_MISSING, parity_gate.SHAPE_FIELDS)


@dataclass(frozen=True)
class Proof:
    """Whether a rollback out of stage two would lose anything, and the evidence."""

    carry: CarryReport
    #: Per-table divergence shapes in the backward direction, `None` where the
    #: SQLite side could not be read at all — which is "not established", never
    #: a zero.
    shapes: dict[str, dict[str, int] | None] = field(default_factory=dict)
    #: The first few records behind each loss, for an operator's next question.
    examples: tuple[dict, ...] = ()
    obligation_expired: bool = False

    @property
    def losses(self) -> int | None:
        """Rows a rollback would lose, or `None` when that was not established."""
        if any(shape is None for shape in self.shapes.values()):
            return None
        return sum(
            counts[shape] for counts in self.shapes.values() if counts for shape in LOSS_SHAPES
        )

    @property
    def safe(self) -> bool:
        return self.carry.complete and self.losses == 0 and not self.obligation_expired

    def render(self) -> str:
        losses = self.losses
        lines = [
            f"reverse mirror: {'ROLLBACK-SAFE' if self.safe else 'NOT ROLLBACK-SAFE'}",
            "  rows a rollback would lose  "
            + (str(losses) if losses is not None else "NOT ESTABLISHED (a side was unreadable)"),
            f"  {OBLIGATION.render()}",
        ]
        if self.obligation_expired:
            lines.append(
                f"  the layer is past its {OBLIGATION.deadline.isoformat()} deadline — "
                f"close {OBLIGATION.removal_task} or move the date deliberately"
            )
        if not self.carry.complete:
            for failure in self.carry.failures():
                lines.append(f"  carry-back failed  {failure.table}: {failure.error}")
        for table, counts in sorted(self.shapes.items()):
            if counts is None:
                lines.append(f"  {table:26} NOT COMPARED")
                continue
            loss = sum(counts[shape] for shape in LOSS_SHAPES)
            if loss or counts[parity_gate.SHAPE_UNEXPECTED]:
                lines.append(
                    f"  {table:26} would lose {loss}"
                    + (
                        f" (and SQLite holds {counts[parity_gate.SHAPE_UNEXPECTED]} rows "
                        "PostgreSQL does not, which a rollback keeps)"
                        if counts[parity_gate.SHAPE_UNEXPECTED]
                        else ""
                    )
                )
        return "\n".join(lines)


def reverse_divergence(
    *,
    authority: parity_gate.Authority,
    connection_factory: Callable[[], Any] | None = None,
    root: Path | None = None,
) -> tuple[dict[str, dict[str, int] | None], tuple[dict, ...]]:
    """Compare PostgreSQL against SQLite, table by table, the other way round.

    The same `divergence_against(spec)` the forward gate uses, with PostgreSQL
    supplying the rows and the SQLite sink playing the part of the mirror. One
    definition of equality, read in both directions — which is the only way the
    two directions can be compared to each other at all.

    Returns `{table: shape counts}` and a sample of the records behind them.
    A table whose SQLite side could not be read is `None` rather than a count,
    for the same reason `parity_gate` refuses to sum an unreadable mirror into a
    total: a zero that was never measured is the one number an operator must
    never be handed.
    """
    if connection_factory is None:
        from command_center.db import pool

        with pool.connection() as conn:
            return _reverse(conn, authority, root)
    with connection_factory() as conn:
        return _reverse(conn, authority, root)


#: Divergence records kept as examples. Enough to name the table and the first
#: rows in it; the full set is what the reconciliation itself prints.
_EXAMPLES = 5


def _reverse(
    conn: Any, authority: parity_gate.Authority, root: Path | None
) -> tuple[dict[str, dict[str, int] | None], tuple[dict, ...]]:
    subjects = parity_gate.subjects()
    destinations = sinks(authority, root=root)

    @contextmanager
    def pinned() -> Iterator[Any]:
        yield conn

    shapes: dict[str, dict[str, int] | None] = {}
    examples: list[dict] = []
    for table, subject in subjects.items():
        try:
            source_rows = list(subject.build_mirror(pinned).list_records())
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            # PostgreSQL unreadable. Nothing was compared, so this table has no
            # count — and a rollback whose safety was never measured is not a
            # safe rollback.
            shapes[table] = None
            examples.append({"table": table, "detail": f"{type(exc).__name__}: {exc}"})
            continue
        records = tuple(divergence_against(subject.spec)(source_rows, destinations[table]))
        counts = dict.fromkeys(parity_gate.SHAPES, 0)
        for record in records:
            counts[parity_gate.shape(record)] += 1
        if counts[parity_gate.SHAPE_UNREADABLE]:
            shapes[table] = None
        else:
            shapes[table] = counts
        for record in records[:_EXAMPLES]:
            if parity_gate.shape(record) in (*LOSS_SHAPES, parity_gate.SHAPE_UNREADABLE):
                examples.append({"table": table, **record})
    return shapes, tuple(examples)


def prove(
    *,
    authority: parity_gate.Authority,
    connection_factory: Callable[[], Any] | None = None,
    root: Path | None = None,
    today: date | None = None,
) -> Proof:
    """Drain, then prove the drain left nothing behind.

    Carry-back and check in one call, in that order, because the claim being
    made is about the state *after* a drain: "if reads and writes went back to
    SQLite right now, nothing written in PostgreSQL would be lost". Checking
    without draining first would answer a question about the last drain instead
    of about this rollback.

    An expired obligation makes the proof unsafe even when the numbers are
    clean. The layer is allowed to exist because a dated removal is part of the
    deal; past that date the deal has to be renewed by a person, not extended by
    a passing test.
    """
    report = carry_back(authority=authority, connection_factory=connection_factory, root=root)
    shapes, examples = reverse_divergence(
        authority=authority, connection_factory=connection_factory, root=root
    )
    return Proof(
        carry=report,
        shapes=shapes,
        examples=examples,
        obligation_expired=OBLIGATION.expired(today or date.today()),
    )


# --- the SQLite side ---------------------------------------------------------


def _stored(spec: MirroredTable, name: str, value: Any) -> Any:
    """One column's value in the shape SQLite stores it.

    The rows arriving here already passed through `ColumnCodec.to_authority` on
    the way out of the forward mirror, so timestamps are the naive local strings
    `models.iso_now()` writes and booleans are back to 0/1. One class is left,
    and it is left deliberately: `to_authority` hands `jsonb` back as the parsed
    value, because there is no text form to render it into that the authority
    would recognise as its own — key order and separators belong to the
    database. SQLite cannot store a `dict`, so it is serialised here.

    That makes the round trip value-preserving rather than byte-preserving for
    JSON columns, which is the whole of the asymmetry this module documents.
    """
    if name in spec.codec.json_values and not isinstance(value, (str, bytes, type(None))):
        return json.dumps(value)
    return value


@contextmanager
def _writable(runtime_db: Path) -> Iterator[sqlite3.Connection]:
    """A write connection on the runtime database, with the settings it expects.

    The same three PRAGMAs `command_center.runtime.db.core.connect` sets, spelled
    out rather than imported: `command_center/db/` reads and writes SQLite
    through plain `sqlite3` throughout (see `parity_gate.Authority`), and reaching
    into the runtime package for a connection would make this module depend on
    the store it is a rollback path *for*.

    `foreign_keys=ON` is the one that earns its place here: it is what turns
    `carry_order()` from a claim about the graph into a checked one — a child
    written before its parent is refused by the database rather than noticed
    later by a reconciliation.
    """
    connection = sqlite3.connect(str(runtime_db), timeout=30, isolation_level=None)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        yield connection
    finally:
        connection.close()
