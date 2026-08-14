"""`model_entry` / `model_event` PostgreSQL mirrors (VOYN-W0-AICC-SRV-01B, slice 6).

Sixth slice, and the first **identity column**: `model_event.id` is
`INTEGER PRIMARY KEY AUTOINCREMENT` in SQLite and
`bigint GENERATED ALWAYS AS IDENTITY` in PostgreSQL. Seven columns in the
accepted map are declared that way, and all seven of the others live in the
15-table family this migration has not reached yet — so the class is worked out
here, on two tables, rather than on fifteen.

Everything below was measured against PostgreSQL 17.6, not read off the manual:

* An explicit `id` is **rejected**: `GeneratedAlways: cannot insert a
  non-DEFAULT value into column "id"`. So the mirror writes
  `OVERRIDING SYSTEM VALUE`, because the authority's id is the only id that
  makes a row identifiable on both sides — a mirror that let PostgreSQL mint
  its own would reconcile nothing, since `divergence` matches rows by id.
* `OVERRIDING SYSTEM VALUE` composes with `ON CONFLICT (id) DO UPDATE`, so the
  upsert contract the other mirrors have survives here.
* The identity sequence is **not advanced** by those inserts: after mirroring
  ids 1..N it still reads `last_value = 1, is_called = false`.
* Therefore the first row PostgreSQL generates after a cutover starts at 1 and
  collides — `UniqueViolation: duplicate key value violates unique constraint
  "…_pkey"`, reproduced. `resync_identity()` exists for exactly that, and the
  cutover has to call it. This is the hazard the accepted map flagged as "two
  separate importer steps"; the first (`OVERRIDING SYSTEM VALUE`) is inherent
  to every write here, the second is a one-time operation nothing in a
  dual-write would otherwise trigger.

The other two properties are already-solved shapes, deliberately: `metadata_json`
is `jsonb` (slice 4) and `model_event.model_id` is a foreign key to
`model_entry` (slice 5). They are here because the table has them, not because
the slice needed them, which is what keeps the new class isolated.

`cost`/`quality` are `REAL` -> `double precision` and need no conversion: the
map records both as IEEE-754 binary64 already, bit-for-bit identical.

One form decision worth naming rather than defaulting: these two mirrors
hand-roll `_connection`/`upsert`/`list_records` like slices 2-4 instead of
reusing slice 5's `_TableMirror`, which lives in `networking_store` and would
make the model family depend on the networking one. That is the third
form-choice made under `VOYN-W0-AICC-MIRROR-STORE-BASE-CONSOLIDATION`, and the
cost that task predicted is now real rather than theoretical.
"""

from __future__ import annotations

from typing import Any

from command_center.db import mirror_support
from command_center.db.mirror_support import MIRROR_UNAVAILABLE, ColumnCodec

__all__ = [
    "MIRROR_UNAVAILABLE",
    "MODEL_ENTRY_COLUMNS",
    "MODEL_EVENT_COLUMNS",
    "PostgresModelEntryMirror",
    "PostgresModelEventMirror",
    "entry_divergence",
    "event_divergence",
]

#: Column order as declared in `command_center/db/sql/0001_initial.up.sql`,
#: pinned against that file by a test rather than trusted to stay in step.
MODEL_ENTRY_COLUMNS: tuple[str, ...] = (
    "id",
    "name",
    "kind",
    "provider",
    "status",
    "cost",
    "quality",
    "latency_ms",
    "provenance",
    "download_progress",
    "version",
    "created_at",
    "updated_at",
)

MODEL_EVENT_COLUMNS: tuple[str, ...] = (
    "id",
    "model_id",
    "seq",
    "action",
    "actor",
    "target_ref",
    "provenance",
    "metadata_json",
    "created_at",
)

_ENTRY_CODEC = ColumnCodec(timestamps=frozenset({"created_at", "updated_at"}))
#: A model event is write-once: `created_at` and no `updated_at`.
_EVENT_CODEC = ColumnCodec(
    timestamps=frozenset({"created_at"}),
    json_values=frozenset({"metadata_json"}),
)


class _Mirror:
    """Connection handling shared by the two mirrors in this module."""

    name = "postgres"

    def __init__(self, connection_factory: Any = None) -> None:
        self._factory = connection_factory

    def _connection(self) -> Any:
        """Resolved on use, never at import.

        `command_center.db.__init__` promises that importing the package pulls
        in neither `aios_db` nor `psycopg`, because the desktop and CLI entry
        points must keep working without a PostgreSQL client library.
        """
        if self._factory is not None:
            return self._factory()
        from command_center.db import pool

        return pool.connection()


class PostgresModelEntryMirror(_Mirror):
    """The `model_entry` table — an ordinary keyed row, `text` primary key."""

    def upsert(self, record: dict) -> None:
        values = [_ENTRY_CODEC.to_column(name, record.get(name)) for name in MODEL_ENTRY_COLUMNS]
        assignments = ", ".join(
            f"{name} = EXCLUDED.{name}" for name in MODEL_ENTRY_COLUMNS if name != "id"
        )
        placeholders = ", ".join(["%s"] * len(MODEL_ENTRY_COLUMNS))
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO model_entry ({', '.join(MODEL_ENTRY_COLUMNS)}) "
                    f"VALUES ({placeholders}) "
                    f"ON CONFLICT (id) DO UPDATE SET {assignments}",
                    values,
                )

    def list_records(self) -> list[dict]:
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {', '.join(MODEL_ENTRY_COLUMNS)} FROM model_entry ORDER BY id"
                )
                rows = cur.fetchall()
        return [
            {
                name: _ENTRY_CODEC.to_authority(name, value)
                for name, value in zip(MODEL_ENTRY_COLUMNS, row, strict=True)
            }
            for row in rows
        ]


class PostgresModelEventMirror(_Mirror):
    """The `model_event` table — the identity column this slice exists for."""

    def upsert(self, record: dict) -> None:
        """Write `record`, carrying **the authority's own id**.

        `OVERRIDING SYSTEM VALUE` is not a stylistic choice: without it
        PostgreSQL rejects the statement outright, and with the id left to
        PostgreSQL the two stores would disagree on every row's identity —
        `divergence` matches by id, so a mirror with its own ids reconciles
        nothing and reports the whole table twice, once missing and once ahead.
        """
        values = [_EVENT_CODEC.to_column(name, record.get(name)) for name in MODEL_EVENT_COLUMNS]
        placeholders = ", ".join(
            "%s::jsonb" if name in _EVENT_CODEC.json_values else "%s"
            for name in MODEL_EVENT_COLUMNS
        )
        assignments = ", ".join(
            f"{name} = EXCLUDED.{name}" for name in MODEL_EVENT_COLUMNS if name != "id"
        )
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO model_event ({', '.join(MODEL_EVENT_COLUMNS)}) "
                    f"OVERRIDING SYSTEM VALUE VALUES ({placeholders}) "
                    f"ON CONFLICT (id) DO UPDATE SET {assignments}",
                    values,
                )

    def list_records(self) -> list[dict]:
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {', '.join(MODEL_EVENT_COLUMNS)} FROM model_event ORDER BY id"
                )
                rows = cur.fetchall()
        return [
            {
                name: _EVENT_CODEC.to_authority(name, value)
                for name, value in zip(MODEL_EVENT_COLUMNS, row, strict=True)
            }
            for row in rows
        ]

    def resync_identity(self) -> int:
        """Advance the identity sequence past the largest mirrored id.

        A cutover step, not a write-path one — and the reason it must exist is
        measured rather than assumed: inserts carrying an explicit id leave the
        sequence untouched (`last_value = 1, is_called = false` after mirroring
        ids 1..N), so the first row PostgreSQL generates for itself starts at 1
        and collides with a mirrored row. Reproduced against 17.6:
        `UniqueViolation: duplicate key value violates unique constraint
        "model_event_pkey"`.

        Nothing in a dual-write triggers this: while SQLite is the authority,
        every id arrives from the mirror and the sequence is never consulted.
        It fails on the *first* native write after reads switch, which is the
        worst possible moment to discover it. Returns the value the sequence
        was set to, so an operator's cutover log has the number in it.
        """
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_get_serial_sequence('model_event', 'id')")
                sequence = cur.fetchone()[0]
                # The third `setval` argument is `is_called`: false on an empty
                # table, so the sequence still yields 1 next. The two-argument
                # form marks it called and would burn id 1 — harmless for a
                # surrogate key, but it would make this operation something
                # other than what its name says, and independent review pointed
                # out that "advance past the largest mirrored id" has no
                # meaning when there is no largest.
                cur.execute(
                    "SELECT setval(%s, (SELECT COALESCE(MAX(id), 1) FROM model_event),"
                    " (SELECT COUNT(*) > 0 FROM model_event))",
                    (sequence,),
                )
                return int(cur.fetchone()[0])


def entry_divergence(authority_rows: list[dict], mirror: Any) -> list[dict]:
    """Rows where the SQLite authority and `mirror` disagree on `model_entry`."""
    return mirror_support.divergence(authority_rows, mirror, MODEL_ENTRY_COLUMNS, _ENTRY_CODEC)


def event_divergence(authority_rows: list[dict], mirror: Any) -> list[dict]:
    """Rows where the SQLite authority and `mirror` disagree on `model_event`.

    Takes rows in the shape SQLite stores — `runtime/db/model_registry.py`'s
    `list_model_events_stored`, not `list_model_events`, which pops
    `metadata_json` in favour of a decoded `metadata`. Slice 4 shipped without a
    runnable reconciliation for exactly that reason; this table has the same
    shape and says so here rather than leaving it to be rediscovered.
    """
    return mirror_support.divergence(authority_rows, mirror, MODEL_EVENT_COLUMNS, _EVENT_CODEC)
