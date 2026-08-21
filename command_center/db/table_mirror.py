"""One mirror implementation, declared per table (VOYN-W0-AICC-SRV-01B slice 7).

Six slices established what a mirrored table needs, and by the sixth the same
`_connection` / `upsert` / `list_records` trio existed in five copies with two
different shapes between them — the state
`VOYN-W0-AICC-MIRROR-STORE-BASE-CONSOLIDATION` was opened to prevent and then
watched grow. This module ends it: the behaviour lives here once, and a table
becomes a *declaration* of what makes it different.

The proof that the move is honest is deliberately structural: **not one test
from slices 1–6 was edited**. Every store keeps its public names, so the earlier
suites exercise this code unchanged, and if any behaviour had shifted they would
say so rather than this docstring.

What a table declares:

* `columns` — in the order the accepted schema declares them, pinned by each
  store's own test against `0001_initial.up.sql`;
* `codec` — the conversions the target needs (`ColumnCodec`), which is also what
  makes `jsonb` columns compare as parsed values rather than as text;
* `identity` — whether the primary key is `GENERATED ALWAYS AS IDENTITY`, which
  changes the statement (`OVERRIDING SYSTEM VALUE`) and unlocks
  `resync_identity()`.

What it deliberately does *not* try to cover: `queue_store`. The queue mirrors
by whole-list replacement with a `position` column the JSON authority does not
have, so it has `replace_entries`/`list_entries` and no `upsert` at all. Folding
a second contract in here would produce one class that says something vague
about both, which is the trade `record_mirror`'s docstring already refused.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from command_center.db import mirror_support
from command_center.db.mirror_support import ColumnCodec

__all__ = ["MirroredTable", "PostgresTableMirror"]


@dataclass(frozen=True)
class MirroredTable:
    """Everything about one table that its mirror cannot infer."""

    #: Table name on both sides — the migration keeps them identical.
    table: str
    #: Column order as declared in `command_center/db/sql/0001_initial.up.sql`.
    columns: tuple[str, ...]
    #: Conversions the target's stricter types require.
    codec: ColumnCodec = field(default_factory=ColumnCodec)
    #: True when the primary key is `GENERATED ALWAYS AS IDENTITY`.
    identity: bool = False
    #: The primary key: one column, or several.
    #:
    #: Not always `id` — `council_decision` is keyed by `motion_id` and carries
    #: an `id` column that is *not* unique, so `ON CONFLICT (id)` would name a
    #: constraint the table does not have. And not always one column:
    #: `provider_attempt` is keyed by `(run_id, attempt_number)`, which is the
    #: last shape in this schema the machinery had not met. Both were found by
    #: reading the DDL before writing the mirror, because both fail the same
    #: silent way — the statement raises, the dual-write hook swallows it, and
    #: the table simply never mirrors.
    key: str | tuple[str, ...] = "id"

    @property
    def key_columns(self) -> tuple[str, ...]:
        """The key as a tuple, whatever it was declared as."""
        return (self.key,) if isinstance(self.key, str) else tuple(self.key)
    #: Tables this one references, as `{column: parent table}`.
    #:
    #: Declared rather than inferred, and it earns its place twice. It records
    #: the ordering a dual-write must follow — parent before child, because the
    #: target refuses a child whose parent is absent — which until now lived
    #: only in a hook's comment. And it lets `tests/db/test_mirror_contract.py`
    #: build a valid parent row for any table automatically, so the shared
    #: contract can exercise a foreign-keyed table without knowing anything
    #: about its family.
    references: dict[str, str] = field(default_factory=dict)


class PostgresTableMirror:
    """A keyed table on the accepted PostgreSQL seam."""

    name = "postgres"

    #: Subclasses declare this; it is the only thing that differs between them.
    spec: MirroredTable

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Refuse a subclass that declares no table, at import rather than in use.

        Without this the omission surfaces as `AttributeError: 'X' object has
        no attribute 'spec'` on the first mirrored write — late, swallowed by
        the dual-write hook, and naming the subclass rather than the mistake.
        Slice 7's acceptance called it a footgun for the batch that follows,
        where declarations arrive several at a time; this is that batch.
        """
        super().__init_subclass__(**kwargs)
        if not isinstance(getattr(cls, "spec", None), MirroredTable):
            raise TypeError(f"{cls.__name__} must declare `spec = MirroredTable(...)`")

    def __init__(self, connection_factory: Any = None) -> None:
        # Injectable so tests can supply a connection without a process-wide
        # pool, and so this module never reaches for global state of its own.
        self._factory = connection_factory

    def _connection(self) -> Any:
        """Resolved on use, never at import.

        `command_center.db.__init__` promises that importing the package pulls
        in neither `aios_db` nor `psycopg`, because the desktop and CLI entry
        points must keep working without a PostgreSQL client library. A
        module-level import of the pool would break that promise for every
        importer of every store at once.
        """
        if self._factory is not None:
            return self._factory()
        from command_center.db import pool

        return pool.connection()

    # --- writes ------------------------------------------------------------

    def upsert(self, record: dict) -> None:
        """Write `record`, replacing any existing row with the same key.

        The key is the table's own primary key, which is not always `id` — see
        `MirroredTable.key`, and `test_the_declared_key_is_the_tables_primary_key`
        for the check that the declaration matches the schema.

        `upsert` rather than `insert`, because the backfill runs more than once
        by design — once per operator, once after a rollback and re-advance —
        and an insert-only mirror would fail the second run on rows it wrote
        itself.

        Whole rows, never the changed columns: a caller that updates two fields
        hands over the row, and the mirror has no other source for the rest.

        Two statement shapes come from the declaration rather than from a
        hand-written string per table. `jsonb` columns are cast (`%s::jsonb`)
        because psycopg adapts neither `list` nor `dict` to `jsonb`, and an
        identity table takes `OVERRIDING SYSTEM VALUE` because PostgreSQL
        otherwise refuses the authority's own id — which is the only id that
        makes a row identifiable on both sides.
        """
        spec = self.spec
        values = [spec.codec.to_column(name, record.get(name)) for name in spec.columns]
        placeholders = ", ".join(
            "%s::jsonb" if name in spec.codec.json_values else "%s" for name in spec.columns
        )
        keys = spec.key_columns
        assignments = ", ".join(
            f"{name} = EXCLUDED.{name}" for name in spec.columns if name not in keys
        )
        overriding = "OVERRIDING SYSTEM VALUE " if spec.identity else ""
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO {spec.table} ({', '.join(spec.columns)}) "
                    f"{overriding}VALUES ({placeholders}) "
                    f"ON CONFLICT ({', '.join(keys)}) DO UPDATE SET {assignments}",
                    values,
                )

    def delete_where(self, column: str, value: Any) -> None:
        """Remove every mirrored row matching one column.

        The authority's own predicate, mirrored as itself. `digest_item` is the
        case: its engine rebuilds a day by deleting it and re-inserting, and a
        mirror without this keeps every superseded row, so reconciliation
        reports it permanently ahead of the system of record — true, useless,
        and exactly the standing noise that gets a check switched off.

        Translating the predicate into the ids it removes would mean reading
        the authority before the delete: one more query, and a race with the
        rebuild this is following.
        """
        if column not in self.spec.columns:
            raise ValueError(f"{self.spec.table} has no column {column!r}")
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM {self.spec.table} WHERE {column} = %s", (value,))

    def resync_identity(self) -> int:
        """Advance the identity sequence past the largest mirrored id.

        A cutover step, not a write-path one, and the reason it must exist was
        measured rather than assumed: inserts carrying an explicit id leave the
        sequence untouched, so the first row PostgreSQL generates for itself
        starts at 1 and collides with a mirrored row.

        Nothing in a dual-write triggers this — while SQLite is the authority
        every id arrives from the mirror and the sequence is never consulted —
        so it fails on the first native write after reads switch, the worst
        possible moment. Returns the value the sequence was set to, so an
        operator's cutover log has the number in it.
        """
        spec = self.spec
        if not spec.identity:
            raise TypeError(f"{spec.table} has no identity column to resync")
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_get_serial_sequence(%s, 'id')", (spec.table,))
                sequence = cur.fetchone()[0]
                # The third `setval` argument is `is_called`: false on an empty
                # table, so the sequence still yields 1 next. The two-argument
                # form marks it called and would burn id 1 — harmless for a
                # surrogate key, but it would make this operation something
                # other than what its name says, and "advance past the largest
                # mirrored id" has no meaning when there is no largest. Slice
                # 6's acceptance raised it; the behaviour is pinned by
                # `test_resync_identity_is_a_no_op_on_an_empty_table`.
                cur.execute(
                    f"SELECT setval(%s, (SELECT COALESCE(MAX(id), 1) FROM {spec.table}),"
                    f" (SELECT COUNT(*) > 0 FROM {spec.table}))",
                    (sequence,),
                )
                return int(cur.fetchone()[0])

    # --- reads -------------------------------------------------------------

    def list_records(self, *, key_from: Any = None, key_to: Any = None) -> list[dict]:
        """Mirrored records, shaped like the authority's own row.

        Every row by default. `key_from` and `key_to` narrow the read to the
        half-open key interval `[key_from, key_to)`, `None` meaning unbounded on
        that side — which is what lets `mirror_support.windowed_divergence`
        reconcile a large table a window at a time instead of materialising both
        sides of it. Measured at 200 000 rows: 294.9 MB whole against 28.6 MB
        windowed, for the same answer.

        Optional parameters rather than a second reader, and that is the whole
        design decision. A windowed reader would share this method's entire body
        — the column list, the codec round trip, the key order — and a second
        copy of that body is the defect this migration keeps having: the copy
        drifts, and the one that drifted is the one reconciliation reads
        through.

        A bound is the key's own value: a bare value for a single-column key, a
        tuple for a composite one (`provider_attempt` is keyed by
        `(run_id, attempt_number)`), compared row-wise so a composite window is
        one interval along the key rather than a box around it. A single-column
        key accepts either form, because the caller that builds windows has the
        key as a tuple in every case.

        Text key columns are compared under `COLLATE "C"`; see `_KEY_COLLATION`
        for why the collation is spelled out. Which columns those are is decided
        by the *bound's* own type rather than by asking the catalogue: the value
        came out of the column, so a `str` bound is a text column, and `COLLATE`
        on a `bigint` is an error rather than a no-op.

        `ORDER BY` is unchanged, and is deliberately not what makes the
        windowing correct: which rows a window holds is decided by the
        predicate, and rows inside it are matched by key rather than by
        position. So the cluster's own order stays, and an unbounded call emits
        exactly the statement it always has.
        """
        spec = self.spec
        where, params = _key_window(spec.key_columns, key_from, key_to)
        statement = (
            f"SELECT {', '.join(spec.columns)} FROM {spec.table}{where} "
            f"ORDER BY {', '.join(spec.key_columns)}"
        )
        with self._connection() as conn:
            with conn.cursor() as cur:
                if params:
                    cur.execute(statement, params)
                else:
                    # No parameter sequence at all on the unbounded read, so it
                    # stays the call every store's suite already exercises:
                    # psycopg switches to the extended protocol the moment one
                    # is passed, which is a change this method was not asked to
                    # make.
                    cur.execute(statement)
                rows = cur.fetchall()
        return [
            {
                name: spec.codec.to_authority(name, value)
                for name, value in zip(spec.columns, row, strict=True)
            }
            for row in rows
        ]


#: The collation a key window is compared under, spelled out rather than left to
#: the cluster's default.
#:
#: The authority is SQLite, which compares `TEXT` with `BINARY` — byte order —
#: and `COLLATE "C"` is what that means on this side. A window compared under a
#: typical install's default would cut text keys somewhere the authority does not
#: agree with: `Task-1` and `task_1` order one way in `en_US.UTF-8` and the other
#: in byte order, so a boundary taken from the authority would land between
#: different rows on the two sides. What follows is overlapping windows and rows
#: compared twice rather than rows silently skipped — the intervals still cover
#: the whole key space whatever order their boundaries arrive in — but a gate
#: reporting differences that are not there is one nobody can pass.
#:
#: `parity_gate` names the same collation for the same reason, and the two are
#: the same claim about what "in order" means between these two databases.
_KEY_COLLATION = 'COLLATE "C"'


def _key_window(key_columns: tuple[str, ...], key_from: Any, key_to: Any) -> tuple[str, tuple]:
    """The `WHERE` clause for `[key_from, key_to)` and its parameters.

    `("", ())` when neither bound is given, which is what keeps the unbounded
    read byte-identical to the statement this mirror has always emitted.

    Half-open, and not by preference: consecutive windows share a boundary
    value, so `>=` on one side and `<` on the other is what makes their union
    the entire key space with no row landing in two of them.
    """
    terms: list[str] = []
    params: list[Any] = []
    for bound, operator in ((key_from, ">="), (key_to, "<")):
        if bound is None:
            continue
        values = _key_values(key_columns, bound)
        comparable = _comparable_key(key_columns, values)
        terms.append(f"{comparable} {operator} {_key_placeholders(key_columns)}")
        params.extend(values)
    if not terms:
        return "", ()
    return " WHERE " + " AND ".join(terms), tuple(params)


def _key_values(key_columns: tuple[str, ...], bound: Any) -> tuple:
    """One bound as a value per key column.

    A bare value is accepted for a single-column key, because that is what an
    operator at a REPL has; a tuple is accepted for every key, because that is
    what a caller walking windows has. A wrong arity is refused here rather than
    reaching the database, where it would arrive as a driver error about a
    statement instead of about the bound.
    """
    values = tuple(bound) if isinstance(bound, (tuple, list)) else (bound,)
    if len(values) != len(key_columns):
        raise ValueError(
            f"a bound on {key_columns} needs {len(key_columns)} value(s), not {values!r}"
        )
    return values


def _comparable_key(key_columns: tuple[str, ...], values: tuple) -> str:
    """The key as one comparable expression, collated where a collation applies.

    Several columns become a row constructor, so the comparison is the standard
    row-wise one — `(a, b) >= (x, y)` means "at or after that key", which is the
    interval the walk cuts, and not the `a >= x AND b >= y` box a per-column
    reading would produce.
    """
    terms = [
        f"{column} {_KEY_COLLATION}" if isinstance(value, str) else column
        for column, value in zip(key_columns, values, strict=True)
    ]
    return terms[0] if len(terms) == 1 else "(" + ", ".join(terms) + ")"


def _key_placeholders(key_columns: tuple[str, ...]) -> str:
    if len(key_columns) == 1:
        return "%s"
    return "(" + ", ".join(["%s"] * len(key_columns)) + ")"


def divergence_against(spec: MirroredTable, doc: str | None = None) -> Any:
    """Build the reconciliation for one declared table.

    Bound to the table's own codec, which is what makes a `jsonb` column
    compare as a parsed value rather than as text — see
    `mirror_support.divergence` for what each reported shape means.

    `doc` becomes the returned function's `__doc__`, and that is not cosmetic.
    Slice 7 replaced five module-level functions with closures and the runtime
    docstrings went with them: the text survived in the source as `#:`
    comments, but `help(event_divergence)` went silent — and that particular
    docstring is what closed slice 4's rejection, because it warns the operator
    that reconciliation takes the *stored* reader. The warning has to be
    readable where the mistake is made, which is a REPL at cutover time, not a
    source file.

    The returned function takes an optional `window=<rows>`, which routes to
    `mirror_support.windowed_divergence` and compares a key-window at a time
    instead of materialising both sides. Off by default: the answer is identical
    either way, but windowing needs a mirror whose `list_records` accepts bounds
    and an authority already in key order, and those are the caller's to
    guarantee rather than this function's to assume.
    """

    def divergence(
        authority_rows: Iterable[dict], mirror: Any, *, window: int | None = None
    ) -> list[dict]:
        if window is None:
            return mirror_support.divergence(
                authority_rows, mirror, spec.columns, spec.codec, key=spec.key_columns
            )
        return mirror_support.windowed_divergence(
            authority_rows,
            mirror,
            spec.columns,
            spec.codec,
            key=spec.key_columns,
            window=window,
        )

    divergence.__name__ = f"{spec.table}_divergence"
    divergence.__qualname__ = divergence.__name__
    divergence.__doc__ = doc or (
        f"Rows where the SQLite authority and a mirror disagree on `{spec.table}` — "
        "see `mirror_support.divergence` for what each reported shape means."
    )
    return divergence
