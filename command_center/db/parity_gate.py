"""The cutover parity gate, as a machine check (VOYN-W0-AICC-SRV-07b).

`docs/srv01b-schema-map.md` closes with a four-step reconciliation procedure and
says so plainly: "план сверки, а не выполненная работа" — a plan, checked by
nothing. This module is that paragraph turned into something that exits non-zero,
because the step it guards is the one that cannot be walked back by rerunning it:
switching reads onto the PostgreSQL seam.

**One definition of equality, and it is not this module's.** Whether two rows
agree is decided by `mirror_support.divergence`, reached through the table's own
`divergence_against(spec)` — the same construction each store exposes and each
per-table suite already exercises. The gate contributes no comparison of its own.
That is a deliberate refusal, not an omission: a neighbouring repository's
`--reconcile` established parity by comparing *row counts* per table, which is a
strictly weaker statement — equal counts with swapped, mangled or silently
truncated values pass it — and shipping a second, weaker definition of equality
beside the real one gives the migration two answers to the only question that
matters. `test_parity_gate.py` pins the difference behaviourally: same count,
one differing field, gate red.

**Zero tolerance, and no arithmetic that can produce a false zero.**
`divergence` reports four shapes, and a count that mixes them would hide the
worst of them in the total, so the gate counts them apart:

* `fields` — both stores have the row and disagree about it;
* `missing` — the authority has a row the mirror does not;
* `unexpected` — the *mirror* has a row the authority never wrote;
* `unreadable` — the mirror could not be read at all (`MIRROR_UNAVAILABLE`).

Only `missing` has a benign reading, and only one: under dual-write with traffic,
a row written to the authority moments ago may not have reached the mirror yet,
and the next write repairs it. Lag explains nothing about the other three. A
field difference means two stores that both hold a row disagree about its
contents; an unexpected row means the mirror holds something the system of record
never wrote, which no amount of catching up will remove; and an unreadable mirror
means *nothing was compared*, which is the answer this whole family of checks
exists to stop being reported as agreement. The gate tolerates none of the four
anyway — the benign reading is why it must be run against a quiesced system, not
a licence to subtract.

`unreadable` gets structural protection beyond that. A table whose mirror could
not be read has `divergences = None`, not `0`, and a report containing one has a
total of `None` — "not established" — so the number an operator pastes into a
cutover record cannot be a zero that was never measured. `MIRROR_UNAVAILABLE`
is FAIL, and it cannot be summed into a pass.

**What else is checked, and why each one is not implied by the divergence.**

* *Columns.* `set(information_schema.columns) == set(spec.columns)`. Reconciliation
  compares the columns the declaration names, so a column present on the target
  and absent from the declaration is invisible to it — the mirror never writes it,
  divergence never reads it, and the cutover moves reads onto a table with a
  column nothing has ever populated.
* *Key.* `spec.key_columns` against the primary key the live database has, read
  from `pg_index` rather than from the DDL text. The DDL check already exists in
  the contract suite; this one asks the server the migration is actually cutting
  over to, which is the only place a hand-applied fix or a half-applied migration
  shows up.
* *Key order.* The authority's key sequence against the target's under
  `ORDER BY key COLLATE "C"`. `divergence` pairs by key and is immune to
  ordering, so this proves something else: that the two sides can be walked
  together. `PostgresTableMirror.list_records()` orders by the key in the
  *database's* collation, and on any cluster whose default is not C that order
  differs from SQLite's `BINARY` for ordinary ids — mixed case, hyphens,
  underscores. Anything that pairs rows positionally rather than by key (an
  operator's two-terminal diff, a future streamed comparison) mis-pairs silently
  there. The gate names `COLLATE "C"` explicitly rather than trusting the
  cluster's default, so the check is satisfiable on every cluster instead of
  being permanently red on most of them.
* *Identity.* For the seven `GENERATED ALWAYS AS IDENTITY` tables,
  `resync_identity()` is run and then *proven by a native insert* — an insert
  with no id, taking the sequence's own next value — before reads switch.
  Reading `last_value` back would prove only that `setval` did what `setval`
  does. The proof runs inside a transaction that is rolled back, so no row
  survives it; the id it consumes is not returned, because sequences are not
  transactional, and burning one surrogate id is the price of proving the thing
  with the operation that actually fails.

**Quiescence.** The gate reads two databases that cannot share a snapshot, so it
must run with writers stopped. That is not a weakness of the gate but the reason
its tolerance can be zero: with traffic, the honest tolerance is unknowable, and
an unknowable tolerance is how "0 divergences" becomes "few enough divergences".

Read-only except for the identity step, which writes a sequence value and a row
it rolls back. Run it as the role that owns the cutover, not as the application.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from command_center.db import mirror_registry, mirror_support
from command_center.db.table_mirror import MirroredTable, divergence_against

__all__ = [
    "Authority",
    "Finding",
    "GateReport",
    "IdentityProof",
    "OWN_CONTRACT",
    "TableResult",
    "run",
    "shape",
    "subjects",
]

# --- what a finding is about -------------------------------------------------

CHECK_AUTHORITY = "authority"
CHECK_COLUMNS = "columns"
CHECK_KEY = "key"
CHECK_KEY_ORDER = "key_order"
CHECK_DIVERGENCE = "divergence"
CHECK_ORDER = "queue_order"
CHECK_IDENTITY = "identity"

#: The four shapes `mirror_support.divergence` reports. See the module docstring
#: for which of them has a benign reading and why the gate subtracts none of them.
SHAPE_FIELDS = "fields"
SHAPE_MISSING = "missing"
SHAPE_UNEXPECTED = "unexpected"
SHAPE_UNREADABLE = "unreadable"
SHAPES = (SHAPE_FIELDS, SHAPE_MISSING, SHAPE_UNEXPECTED, SHAPE_UNREADABLE)

#: Text-ish column types, which are the ones a collation applies to. Everything
#: else (`bigint`, `integer`, `timestamptz`) orders the same on both sides
#: without help, and `COLLATE` on them is an error rather than a no-op.
_COLLATED_TYPES = frozenset({"text", "character varying", "character", "name"})


@dataclass(frozen=True)
class Finding:
    """One reason a table did not pass.

    `records` carries the divergence records themselves for the divergence
    check, because an operator's next question is always *which rows* — and a
    gate that answers it only by telling them to go and run the reconciliation
    by hand is a gate they will stop running.
    """

    table: str
    check: str
    detail: str
    records: tuple[dict, ...] = ()


@dataclass(frozen=True)
class IdentityProof:
    """Evidence that one identity table's sequence is past its mirrored rows.

    `generated` is `None` on an empty table: there is no largest mirrored id for
    a generated one to collide with, `resync_identity` leaves the sequence
    yielding 1 (its own documented no-op), and inserting a row to demonstrate
    that would mean inventing a row the authority never wrote.
    """

    table: str
    sequence_value: int
    highest_mirrored_id: int | None
    generated: int | None

    def render(self) -> str:
        if self.generated is None:
            return f"{self.table}: sequence at {self.sequence_value}, no mirrored rows to collide with"
        return (
            f"{self.table}: sequence at {self.sequence_value}, native insert took "
            f"id {self.generated} > highest mirrored id {self.highest_mirrored_id} (rolled back)"
        )


@dataclass(frozen=True)
class TableResult:
    """One table's verdict and the evidence behind it."""

    table: str
    authority_rows: int | None
    #: Per-shape divergence counts, or `None` when nothing could be compared.
    #: `None` is what keeps an unreadable mirror out of a total of zero.
    divergences: dict[str, int] | None
    identity: IdentityProof | None
    findings: tuple[Finding, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.findings

    @property
    def divergence_total(self) -> int | None:
        if self.divergences is None:
            return None
        return sum(self.divergences.values())


@dataclass(frozen=True)
class GateReport:
    """Every table's verdict, and the one line an operator acts on."""

    results: tuple[TableResult, ...]
    #: The cluster's default collation, recorded rather than relied on — see the
    #: module docstring on why the key-order check names `COLLATE "C"` instead.
    collation: str = ""
    #: Tables the gate was asked to cover but never reached, e.g. because the
    #: authority itself was unreadable. Non-empty is always a failure: a gate
    #: that skipped a table must never round its verdict up to PASS.
    unreached: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.unreached and all(result.passed for result in self.results)

    @property
    def divergence_total(self) -> int | None:
        """The number for the cutover record, or `None` when it was not measured.

        `None` rather than a sum-with-holes. One table whose mirror could not be
        read makes the total unknown, and printing `0` there would be the exact
        false clean this gate was built to refuse.
        """
        totals = [result.divergence_total for result in self.results]
        if any(total is None for total in totals):
            return None
        return sum(totals)  # type: ignore[arg-type]

    @property
    def findings(self) -> tuple[Finding, ...]:
        return tuple(finding for result in self.results for finding in result.findings)

    def shape_totals(self) -> dict[str, int]:
        """Divergences by reported shape, over the tables that could be compared."""
        totals = dict.fromkeys(SHAPES, 0)
        for result in self.results:
            for shape, count in (result.divergences or {}).items():
                totals[shape] += count
        return totals

    def as_dict(self) -> dict:
        """The machine-readable report, shaped for a cutover record.

        `divergence_total: null` is a deliberate JSON shape: a consumer that
        reads it as a number gets `None`/`null` rather than a zero, so the
        distinction between "measured zero" and "not measured" survives being
        written to a file and read back by something that was not there.
        """
        return {
            "passed": self.passed,
            "tables": len(self.results),
            "unreached": list(self.unreached),
            "divergence_total": self.divergence_total,
            "shapes": self.shape_totals(),
            "collation": self.collation,
            "identity_proofs": [
                {
                    "table": result.identity.table,
                    "sequence_value": result.identity.sequence_value,
                    "highest_mirrored_id": result.identity.highest_mirrored_id,
                    "generated": result.identity.generated,
                }
                for result in self.results
                if result.identity is not None
            ],
            "results": [
                {
                    "table": result.table,
                    "passed": result.passed,
                    "authority_rows": result.authority_rows,
                    "divergences": result.divergences,
                    "findings": [
                        {
                            "check": finding.check,
                            "detail": finding.detail,
                            "records": len(finding.records),
                        }
                        for finding in result.findings
                    ],
                }
                for result in self.results
            ],
        }

    def render(self) -> str:
        """The operator-facing report: the verdict first, then what produced it."""
        total = self.divergence_total
        proven = sum(1 for result in self.results if result.identity is not None)
        expected_identity = sum(
            1 for table, subject in subjects().items() if subject.spec.identity
        )
        lines = [
            f"parity gate: {'PASS' if self.passed else 'FAIL'}",
            f"  tables compared   {len(self.results)}",
            "  divergences       "
            + (str(total) if total is not None else "NOT ESTABLISHED (a mirror was unreadable)"),
            "  by shape          "
            + ", ".join(f"{shape}={count}" for shape, count in self.shape_totals().items()),
            f"  identity proven   {proven}/{expected_identity} by native insert",
            f"  cluster collation {self.collation or '(unknown)'}"
            " — key order checked under COLLATE \"C\"",
        ]
        if self.unreached:
            lines.append(f"  NOT REACHED       {', '.join(self.unreached)}")
        for result in sorted(self.results, key=lambda item: item.table):
            rows = "?" if result.authority_rows is None else result.authority_rows
            verdict = "PASS" if result.passed else "FAIL"
            reported = result.divergence_total
            lines.append(
                f"  {verdict:4}  {result.table:24} {rows:>8} authority rows, "
                + ("? divergences" if reported is None else f"{reported} divergences")
            )
            for finding in result.findings:
                lines.append(f"          {finding.check}: {finding.detail}")
        for result in self.results:
            if result.identity is not None:
                lines.append(f"  identity  {result.identity.render()}")
        return "\n".join(lines)


# --- the authority side ------------------------------------------------------


class Authority:
    """The two stores the migration is carrying across, read in their stored shape.

    Rows come out of SQLite with a `SELECT` over the declaration's own column
    list, rather than through the runtime package's readers. That is the whole
    reason this class is three methods long: the public readers decode — three
    tables were shipped with a reconciliation nobody could run because of it, and
    `tests/db/test_stored_reader_fitness.py` exists to catch the fourth — while a
    `SELECT` of the declared columns *is* the stored shape by construction, with
    no per-table knowledge to get wrong and nothing to keep in step as readers
    change.

    The queue is the one table with a different authority: a JSON file, whose
    entries are already dicts in the shape `divergence` compares. Its list order
    is data (`queue_store` says why), so it is returned in file order and the
    gate checks that order separately.
    """

    def __init__(self, runtime_db: Path, queue_file: Path) -> None:
        self.runtime_db = Path(runtime_db)
        self.queue_file = Path(queue_file)

    def missing(self) -> list[str]:
        """Which of the two stores is not where the gate was told to look.

        Checked before anything is compared, because both failures otherwise
        arrive as agreement: `sqlite3.connect` *creates* the file it cannot find,
        and a queue file read with a default of `[]` compares an empty authority
        against an empty mirror and reports a clean table.
        """
        absent = []
        if not self.runtime_db.exists():
            absent.append(f"runtime database {self.runtime_db}")
        if not self.queue_file.exists():
            absent.append(f"queue authority {self.queue_file}")
        return absent

    def rows(self, spec: MirroredTable) -> list[dict]:
        """`spec`'s rows as the runtime store holds them, in key order.

        `ORDER BY` the declared key: SQLite compares `TEXT` with `BINARY`, which
        is byte order, which is what `COLLATE "C"` means on the other side. The
        two sides are therefore asked for the same order rather than for one that
        happens to match on the cluster the gate ran on.
        """
        columns = ", ".join(spec.columns)
        order = ", ".join(spec.key_columns)
        connection = sqlite3.connect(f"file:{self.runtime_db}?mode=ro", uri=True)
        try:
            connection.row_factory = sqlite3.Row
            cursor = connection.execute(f"SELECT {columns} FROM {spec.table} ORDER BY {order}")
            return [dict(row) for row in cursor.fetchall()]
        finally:
            connection.close()

    def queue_entries(self) -> list[dict]:
        """The JSON queue, in file order — which is the queue's order."""
        entries = json.loads(self.queue_file.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            raise ValueError(f"{self.queue_file} does not hold a JSON list of entries")
        return entries


# --- what the gate covers ----------------------------------------------------


@dataclass(frozen=True)
class Subject:
    """One table under the gate: its declaration, its mirror, its authority."""

    spec: MirroredTable
    #: Builds something with `list_records()` from a connection factory. For the
    #: 32 shared-contract tables this is the mirror class itself; the queue gets
    #: an adapter, so that `mirror_support.divergence` — and not a second
    #: comparison written for the queue — is what decides its equality too.
    build_mirror: Callable[[Any], Any]
    read_authority: Callable[[Authority], list[dict]]
    #: Columns the target carries that are not part of a row. One table has any:
    #: `queue_entry.position`, which is how the mirror preserves order. Declared
    #: in `queue_store` rather than here, so the gate holds no allowance of its
    #: own — the column-set check is `spec.columns | this`, and an undeclared
    #: column anywhere is still a failure.
    storage_columns: frozenset[str] = frozenset()
    #: The queue's own contract: whole-list replacement, order is data.
    ordered: bool = False


class _QueueRecords:
    """`list_entries()` under the name `divergence` calls.

    Two contracts, one definition of equality. `queue_entry` mirrors by whole-list
    replacement and has no `upsert`, which is why it is not a
    `PostgresTableMirror` — but "do these two stores hold the same rows" is the
    same question for it as for the other 32, and answering it with a second
    comparison written for the queue is how a migration ends up with two truths.
    Six lines of adapter buy the shared answer.
    """

    def __init__(self, mirror: Any) -> None:
        self._mirror = mirror

    def list_records(self) -> list[dict]:
        return self._mirror.list_entries()


def _queue_subject() -> Subject:
    from command_center.db import queue_store

    return Subject(
        spec=queue_store.QUEUE_ENTRY,
        build_mirror=lambda factory: _QueueRecords(
            queue_store.PostgresQueueMirror(connection_factory=factory)
        ),
        read_authority=lambda authority: authority.queue_entries(),
        storage_columns=frozenset({queue_store.QUEUE_ORDER_COLUMN}),
        ordered=True,
    )


#: Tables mirrored under a contract of their own, and therefore invisible to
#: `mirror_registry`, which finds `PostgresTableMirror` subclasses. Exactly one
#: today, and it is signed for in `tests/db/test_mirror_coverage.py` as well:
#: the gate covering 32 tables while the schema map counts 33 is precisely the
#: off-by-one that hid `queue_entry` from every count until it was written down.
OWN_CONTRACT: dict[str, Callable[[], Subject]] = {"queue_entry": _queue_subject}


def subjects() -> dict[str, Subject]:
    """Every table the gate covers, discovered rather than listed.

    The 32 shared-contract mirrors come from `mirror_registry` — the same rule
    the contract suite and the stored-reader gate use, so a table cannot be
    mirrored and ungated, or gated under a declaration nothing else checks.
    """
    found = {
        table: Subject(
            spec=mirror.spec,
            build_mirror=lambda factory, mirror=mirror: mirror(connection_factory=factory),
            read_authority=lambda authority, mirror=mirror: authority.rows(mirror.spec),
        )
        for table, mirror in mirror_registry.mirror_classes().items()
    }
    for table, build in OWN_CONTRACT.items():
        found[table] = build()
    return dict(sorted(found.items()))


# --- the gate ----------------------------------------------------------------


def run(
    *,
    authority: Authority,
    connection_factory: Callable[[], Any] | None = None,
    prove_identity: bool = True,
) -> GateReport:
    """Run every check over every covered table and report what happened.

    One connection for the whole run, pinned and handed to the mirrors, so the
    gate reads the target through the same seam the mirrors write through rather
    than through a connection of its own with different settings.

    `prove_identity=False` exists for a read-only rehearsal and cannot produce a
    PASS: every identity table is reported as unproven, which is a finding. A
    flag that skipped the step *and* let the gate go green would be the tolerance
    this module refuses to have.
    """
    if connection_factory is None:
        from command_center.db import pool

        with pool.connection() as conn:
            return _run(conn, authority, prove_identity)
    with connection_factory() as conn:
        return _run(conn, authority, prove_identity)


def _run(conn: Any, authority: Authority, prove_identity: bool) -> GateReport:
    covered = subjects()
    absent = authority.missing()
    if absent:
        # Nothing is compared, and the report says so by naming every table as
        # unreached rather than by returning an empty, technically-passing one.
        return GateReport(
            results=(),
            collation=_collation(conn),
            unreached=tuple(covered),
        )

    @contextmanager
    def pinned() -> Iterator[Any]:
        yield conn

    results = []
    for table, subject in covered.items():
        results.append(_check_table(conn, pinned, authority, subject, prove_identity))
    return GateReport(results=tuple(results), collation=_collation(conn))


def _check_table(
    conn: Any,
    pinned: Callable[[], Any],
    authority: Authority,
    subject: Subject,
    prove_identity: bool,
) -> TableResult:
    spec = subject.spec
    findings: list[Finding] = []

    declared = set(spec.columns) | subject.storage_columns
    present = _target_columns(conn, spec.table)
    if present != declared:
        findings.append(
            Finding(
                spec.table,
                CHECK_COLUMNS,
                _column_detail(spec.table, declared, present),
            )
        )

    primary_key = _primary_key(conn, spec.table)
    if primary_key != spec.key_columns:
        findings.append(
            Finding(
                spec.table,
                CHECK_KEY,
                f"declared key {spec.key_columns}, the database's primary key is "
                f"{primary_key or '(none)'} — reconciliation would pair rows on one thing "
                "and `ON CONFLICT` name another",
            )
        )

    try:
        authority_rows = subject.read_authority(authority)
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        # No rows read means nothing compared, and a table with nothing compared
        # must never contribute a zero. `divergences=None` is what carries that
        # into the total.
        findings.append(
            Finding(
                spec.table,
                CHECK_AUTHORITY,
                f"the authority could not be read: {type(exc).__name__}: {exc}",
            )
        )
        return TableResult(spec.table, None, None, None, tuple(findings))

    mirror = subject.build_mirror(pinned)
    records = tuple(divergence_against(spec)(authority_rows, mirror))
    shapes = _shape_counts(records)
    unreadable = shapes[SHAPE_UNREADABLE] > 0

    if unreadable:
        detail = records[0].get("detail", "")
        findings.append(
            Finding(
                spec.table,
                CHECK_DIVERGENCE,
                f"MIRROR_UNAVAILABLE — the mirror could not be read ({detail}). "
                "Nothing was compared, so this table has no divergence count, and the "
                "gate's total is not established.",
                records,
            )
        )
        # Everything below reads the target. There is nothing to read.
        return TableResult(spec.table, len(authority_rows), None, None, tuple(findings))

    if records:
        findings.append(
            Finding(
                spec.table,
                CHECK_DIVERGENCE,
                ", ".join(f"{shape}={shapes[shape]}" for shape in SHAPES if shapes[shape])
                + f" — first: {records[0]['id']!r} on {records[0]['fields']}",
                records,
            )
        )

    key_order = _key_order_finding(conn, spec, authority_rows)
    if key_order is not None:
        findings.append(key_order)

    if subject.ordered:
        order_finding = _queue_order_finding(spec, authority_rows, mirror)
        if order_finding is not None:
            findings.append(order_finding)

    proof = None
    if spec.identity:
        if not prove_identity:
            findings.append(
                Finding(
                    spec.table,
                    CHECK_IDENTITY,
                    "the identity sequence was not resynced or proven (rehearsal run). "
                    "The first native write after the cutover collides with a mirrored id.",
                )
            )
        else:
            proof, identity_finding = _prove_identity(conn, subject, pinned)
            if identity_finding is not None:
                findings.append(identity_finding)

    return TableResult(spec.table, len(authority_rows), shapes, proof, tuple(findings))


def _column_detail(table: str, declared: set[str], present: set[str]) -> str:
    if not present:
        return f"{table} does not exist in the target's current schema"
    undeclared = sorted(present - declared)
    absent = sorted(declared - present)
    parts = []
    if undeclared:
        parts.append(
            f"columns on the target that no declaration names: {undeclared} — the mirror "
            "never writes them and reconciliation never reads them"
        )
    if absent:
        parts.append(f"declared columns the target does not have: {absent}")
    return "; ".join(parts)


def _shape_counts(records: tuple[dict, ...]) -> dict[str, int]:
    counts = dict.fromkeys(SHAPES, 0)
    for record in records:
        counts[shape(record)] += 1
    return counts


def shape(record: dict) -> str:
    """Which of the four reported shapes a divergence record is.

    Public because the shape rule has a second reader. `reverse_mirror` asks the
    same question of the same records in the opposite direction, and the two
    directions can only be compared to each other if "this record means the
    mirror is missing a row" is one rule rather than two.

    The sentinel is recognised by *both* halves of what makes it one — the
    reserved id and the `detail` key no ordinary record carries — so a real row
    whose key happened to be that string could not be read as an unreadable
    mirror.
    """
    if record.get("id") == mirror_support.MIRROR_UNAVAILABLE and "detail" in record:
        return SHAPE_UNREADABLE
    if record.get("authority") is None:
        return SHAPE_UNEXPECTED
    if record.get("mirror") is None:
        return SHAPE_MISSING
    return SHAPE_FIELDS


# --- the target's catalogue --------------------------------------------------


def _collation(conn: Any) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT datcollate FROM pg_database WHERE datname = current_database()")
        row = cur.fetchone()
    return str(row[0]) if row else ""


def _target_columns(conn: Any, table: str) -> set[str]:
    """The columns the live database has, in the schema the mirrors write to.

    `current_schema()` rather than a configured name: the mirrors emit unqualified
    statements, so the table they write to is whatever the search path resolves,
    and asking a different question here would check a table nobody uses.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = %s",
            (table,),
        )
        return {row[0] for row in cur.fetchall()}


def _column_types(conn: Any, table: str) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = %s",
            (table,),
        )
        return {row[0]: row[1] for row in cur.fetchall()}


def _primary_key(conn: Any, table: str) -> tuple[str, ...]:
    """The primary key the server has, in index order.

    `pg_index`, not the DDL file. The contract suite already compares the
    declaration against `0001_initial.up.sql`, and that check answers a question
    about the repository; this one answers the question about the database the
    cutover is aimed at, where a hand-applied fix or a half-applied migration is
    the difference between the two.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT a.attname FROM pg_index i "
            "JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord) ON true "
            "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum "
            "WHERE i.indrelid = to_regclass(%s) AND i.indisprimary ORDER BY k.ord",
            (table,),
        )
        return tuple(row[0] for row in cur.fetchall())


def _order_by_c(conn: Any, table: str, key_columns: tuple[str, ...]) -> str:
    """`ORDER BY` naming `COLLATE "C"` on every text-ish key column.

    Spelled out rather than left to the cluster: the default collation of a
    typical install orders `Task-1` and `task_1` differently from byte order, and
    a gate whose ordering check depended on that would be red on most clusters
    and green on the CI one.
    """
    types = _column_types(conn, table)
    terms = []
    for column in key_columns:
        collated = types.get(column, "") in _COLLATED_TYPES
        terms.append(f'{column} COLLATE "C"' if collated else column)
    return ", ".join(terms)


def _key_order_finding(conn: Any, spec: MirroredTable, authority_rows: list[dict]) -> Finding | None:
    keys = spec.key_columns
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {', '.join(keys)} FROM {spec.table} "
            f"ORDER BY {_order_by_c(conn, spec.table, keys)}"
        )
        target = [tuple(row) for row in cur.fetchall()]
    expected = [tuple(row.get(column) for column in keys) for row in authority_rows]
    if target == expected:
        return None
    first = next(
        (index for index, pair in enumerate(zip(target, expected)) if pair[0] != pair[1]),
        min(len(target), len(expected)),
    )
    return Finding(
        spec.table,
        CHECK_KEY_ORDER,
        f"the target's keys under ORDER BY {keys} COLLATE \"C\" are not the authority's "
        f"key sequence: they first differ at position {first} "
        f"({target[first:first + 1] or '(end)'} vs {expected[first:first + 1] or '(end)'}). "
        "Anything pairing the two sides positionally rather than by key pairs the wrong rows.",
    )


def _queue_order_finding(spec: MirroredTable, entries: list[dict], mirror: Any) -> Finding | None:
    """The queue's own contract: the mirror's stored order is the file's order.

    Not implied by the divergence, which pairs by id and would report a perfectly
    reordered queue as clean. The queue is displayed and planned in insertion
    order, so a mirror that holds the right entries in the wrong order is a
    mirror that would run the wrong task next.
    """
    stored = [entry.get("id") for entry in mirror.list_records()]
    expected = [entry.get("id") for entry in entries]
    if stored == expected:
        return None
    return Finding(
        spec.table,
        CHECK_ORDER,
        f"the mirror's stored order is not the queue's: {stored} vs {expected}. "
        "Order is data here — the queue is planned in insertion order.",
    )


# --- identity ----------------------------------------------------------------


class _Rollback(Exception):
    """Ends the proof's transaction without leaving the row it inserted."""


def _prove_identity(
    conn: Any, subject: Subject, pinned: Callable[[], Any]
) -> tuple[IdentityProof | None, Finding | None]:
    """Resync one table's sequence and prove it with an insert that takes it.

    The proof is a real `INSERT` with no id, built from a row the table already
    has so that every `NOT NULL`, foreign key and `CHECK` on the table is
    satisfied by construction rather than by this module guessing values for
    columns it knows nothing about. The one thing copying breaks is uniqueness,
    so each unique index gets its integer column bumped past the table's current
    maximum — which makes the copy unique on every constraint the table declares
    without naming a single column here.
    """
    spec = subject.spec
    mirror = subject.build_mirror(pinned)
    sequence_value = int(mirror.resync_identity())

    with conn.cursor() as cur:
        cur.execute(f"SELECT MAX(id) FROM {spec.table}")
        highest = cur.fetchone()[0]
    if highest is None:
        return IdentityProof(spec.table, sequence_value, None, None), None

    columns = [column for column in spec.columns if column != "id"]
    perturbed, unprovable = _perturbations(conn, spec.table, columns)
    if unprovable:
        return None, Finding(
            spec.table,
            CHECK_IDENTITY,
            f"the sequence was resynced to {sequence_value}, but no native insert could be "
            f"constructed to prove it: {unprovable}. Prove it by hand before switching reads.",
        )

    expressions = ", ".join(perturbed.get(column, column) for column in columns)
    generated: int | None = None
    try:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO {spec.table} ({', '.join(columns)}) "
                    f"SELECT {expressions} FROM {spec.table} WHERE id = %s RETURNING id",
                    (highest,),
                )
                generated = int(cur.fetchone()[0])
            raise _Rollback
    except _Rollback:
        pass
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        return None, Finding(
            spec.table,
            CHECK_IDENTITY,
            f"the sequence was resynced to {sequence_value}, but the native insert that "
            f"proves it failed: {type(exc).__name__}: {exc}",
        )

    if generated is None or generated <= highest:
        return None, Finding(
            spec.table,
            CHECK_IDENTITY,
            f"a native insert took id {generated}, which collides with mirrored rows up to "
            f"{highest}. The first write after the cutover fails on a duplicate key.",
        )
    return IdentityProof(spec.table, sequence_value, int(highest), generated), None


def _perturbations(conn: Any, table: str, columns: list[str]) -> tuple[dict[str, str], str]:
    """`{column: expression}` making a copied row unique, or why it cannot be made so.

    One integer column per unique index, set past the table's current maximum for
    that column. That is enough for a multi-column index — a pair is distinct as
    soon as one member is — and it needs no knowledge of what the columns mean.
    Partial and expression indexes are refused rather than guessed at: the gate
    saying "prove this one by hand" is honest, and a proof that quietly skipped
    the constraint it could not satisfy would not be a proof.
    """
    types = _column_types(conn, table)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT i.indexrelid::regclass::text, i.indpred IS NOT NULL, "
            "i.indexprs IS NOT NULL, "
            "array_agg(a.attname ORDER BY k.ord) "
            "FROM pg_index i "
            "JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord) ON true "
            "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum "
            "WHERE i.indrelid = to_regclass(%s) AND i.indisunique AND NOT i.indisprimary "
            "GROUP BY i.indexrelid, i.indpred, i.indexprs",
            (table,),
        )
        indexes = cur.fetchall()

    perturbed: dict[str, str] = {}
    for name, partial, expression, members in indexes:
        if partial or expression:
            return {}, f"{name} is a partial or expression index"
        candidates = [
            column
            for column in members
            if column in columns and types.get(column) in {"integer", "bigint", "smallint"}
        ]
        if not candidates:
            return {}, f"{name} has no integer column to move a copied row past"
        column = candidates[0]
        perturbed[column] = f"(SELECT COALESCE(MAX({column}), 0) + 1 FROM {table})"
    return perturbed, ""
