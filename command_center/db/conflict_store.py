"""The `conflict` table's PostgreSQL mirror (VOYN-W0-AICC-SRV-01B, slice 3).

Third table, and the first written against `mirror_support` rather than
carrying its own copy of the conversion. That is the point of doing it now: the
timestamp conversion has already been wrong once, and a third hand-written copy
is a third place to fix it the next time — two of which would be found late.

Shape follows slice 2. SQLite is the authority: `runtime/db/conflict.py`
creates, updates and transitions these rows directly and no JSON store stands
behind it, so this is a dual-write, SQLite stays the system of record, reads are
not switched, and the cutover waits on reconciliation plus the rollback and
backup/restore drills.

What is new here is `resolved_at`: a **nullable** `timestamptz`, and the first
one mirrored. It is not decoration — the row alternates between a timestamp and
`NULL` as a conflict resolves and reopens (`_conflict_transition` clears it on a
reopen), so a mirror that only ever wrote timestamps forward would keep a stale
resolved date on a reopened conflict and report a resolution that was undone.
The upsert therefore writes every column on every write rather than the ones
that changed, and the reconciliation compares `None` on both sides. Nullable
timestamps are the common case in what remains: of the 75 `TEXT` -> `timestamptz`
columns in the accepted map, most are optional lifecycle stamps like this one.

Deliberately *not* mirrored: `conflict` has no `jsonb` column, and the first
table that does needs its own slice. `text` -> `jsonb` -> `text` does not
preserve the source bytes — key order and separators are the database's choice,
not the writer's — so it needs a comparison rule of its own rather than another
entry in `ColumnCodec`. Reusing this module's shape there without that rule
produces the permanently-red gate this migration keeps almost building.
"""

from __future__ import annotations

from typing import Any

from command_center.db import mirror_support
from command_center.db.mirror_support import MIRROR_UNAVAILABLE, ColumnCodec

__all__ = ["CONFLICT_COLUMNS", "MIRROR_UNAVAILABLE", "PostgresConflictMirror", "divergence"]

#: Column order as declared in `command_center/db/sql/0001_initial.up.sql`,
#: pinned against that file by a test rather than trusted to stay in step.
CONFLICT_COLUMNS: tuple[str, ...] = (
    "id",
    "kind",
    "source_ref",
    "severity",
    "status",
    "owner",
    "mitigation",
    "project_ref",
    "opened_at",
    "resolved_at",
    "version",
    "created_at",
    "updated_at",
)

#: Four timestamps, one of them nullable; no boolean and no `jsonb` column in
#: this table, so the codec carries no flags.
_CODEC = ColumnCodec(
    timestamps=frozenset({"opened_at", "resolved_at", "created_at", "updated_at"})
)


class PostgresConflictMirror:
    """The `conflict` table on the accepted PostgreSQL seam."""

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

    def upsert(self, record: dict) -> None:
        """Write `record`, replacing any existing row with the same id.

        Every column is written, including the ones the caller did not change.
        A partial update would leave `resolved_at` set on a conflict that has
        been reopened — the mirror would then report a resolution the authority
        has withdrawn, and reconciliation would be the only thing that noticed.
        """
        values = [_CODEC.to_column(name, record.get(name)) for name in CONFLICT_COLUMNS]
        assignments = ", ".join(
            f"{name} = EXCLUDED.{name}" for name in CONFLICT_COLUMNS if name != "id"
        )
        placeholders = ", ".join(["%s"] * len(CONFLICT_COLUMNS))
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO conflict ({', '.join(CONFLICT_COLUMNS)}) "
                    f"VALUES ({placeholders}) "
                    f"ON CONFLICT (id) DO UPDATE SET {assignments}",
                    values,
                )

    def list_records(self) -> list[dict]:
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT {', '.join(CONFLICT_COLUMNS)} FROM conflict ORDER BY id")
                rows = cur.fetchall()
        return [
            {
                name: _CODEC.to_authority(name, value)
                for name, value in zip(CONFLICT_COLUMNS, row, strict=True)
            }
            for row in rows
        ]


def divergence(authority_rows: list[dict], mirror: Any) -> list[dict]:
    """Rows where the SQLite authority and `mirror` disagree — see
    `mirror_support.divergence` for what each reported shape means."""
    return mirror_support.divergence(authority_rows, mirror, CONFLICT_COLUMNS)
