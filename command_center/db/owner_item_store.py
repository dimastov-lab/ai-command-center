"""The `owner_item` table's PostgreSQL mirror (VOYN-W0-AICC-SRV-01B, slice 2).

Slice 2 differs from slice 1 in the way that matters most. `queue_entry`'s
authority was already `execution_queue.json`, with SQLite as a mirror, so
adding PostgreSQL added a third mirror and moved nothing. Here **SQLite is the
authority**: `command_center/runtime/db/wave1.py` creates, reads and updates
these rows directly, and no JSON store stands behind it. So this slice is a
dual-write, SQLite stays the system of record, reads are not switched, and the
cutover waits on reconciliation plus the rollback and backup/restore drills.

Two type gaps make the conversion load-bearing rather than incidental, and the
accepted correspondence map predicted exactly this by warning that the target
is stricter:

* `done` is `INTEGER` (0/1) in SQLite and `boolean` in PostgreSQL;
* `created_at`/`updated_at` are `TEXT` in SQLite and `timestamptz` in
  PostgreSQL.

Both are converted on the way in and rendered back on the way out, so a
reconciliation compares like with like. Skipping that would not produce a
visible error: it would produce a divergence check that reports every row as
different, which is a cutover gate permanently red — and a red gate nobody can
satisfy is one someone eventually satisfies by loosening the comparison.

Both conversions now live in `mirror_support`, moved there at slice 3 when a
third table would have made a third copy. The history is worth keeping in view:
the version of the timestamp conversion this module first shipped was wrong in
both directions, and the reason the extraction waited until three callers
existed is written down in that module.
"""

from __future__ import annotations

from typing import Any

from command_center.db import mirror_support
from command_center.db.mirror_support import MIRROR_UNAVAILABLE, ColumnCodec

__all__ = ["MIRROR_UNAVAILABLE", "OWNER_ITEM_COLUMNS", "PostgresOwnerItemMirror", "divergence"]

#: Column order as declared in `command_center/db/sql/0001_initial.up.sql`.
OWNER_ITEM_COLUMNS: tuple[str, ...] = (
    "id",
    "title",
    "detail",
    "due",
    "done",
    "source_ref",
    "version",
    "created_at",
    "updated_at",
    "project_ref",
)

#: `due` is deliberately absent from `timestamps`: the map keeps it `text` on
#: both sides because it is free user input rather than a date.
_CODEC = ColumnCodec(
    timestamps=frozenset({"created_at", "updated_at"}),
    flags=frozenset({"done"}),
)


class PostgresOwnerItemMirror:
    """The `owner_item` table on the accepted PostgreSQL seam."""

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
        values = [_CODEC.to_column(name, record.get(name)) for name in OWNER_ITEM_COLUMNS]
        assignments = ", ".join(
            f"{name} = EXCLUDED.{name}" for name in OWNER_ITEM_COLUMNS if name != "id"
        )
        placeholders = ", ".join(["%s"] * len(OWNER_ITEM_COLUMNS))
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO owner_item ({', '.join(OWNER_ITEM_COLUMNS)}) "
                    f"VALUES ({placeholders}) "
                    f"ON CONFLICT (id) DO UPDATE SET {assignments}",
                    values,
                )

    def list_records(self) -> list[dict]:
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {', '.join(OWNER_ITEM_COLUMNS)} FROM owner_item ORDER BY id"
                )
                rows = cur.fetchall()
        return [
            {
                name: _CODEC.to_authority(name, value)
                for name, value in zip(OWNER_ITEM_COLUMNS, row, strict=True)
            }
            for row in rows
        ]


def divergence(authority_rows: list[dict], mirror: Any) -> list[dict]:
    """Rows where the SQLite authority and `mirror` disagree — see
    `mirror_support.divergence` for what each reported shape means."""
    return mirror_support.divergence(authority_rows, mirror, OWNER_ITEM_COLUMNS)
