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
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

__all__ = ["OWNER_ITEM_COLUMNS", "PostgresOwnerItemMirror", "divergence"]

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

#: Columns PostgreSQL stores as `timestamptz` while SQLite stores them as text.
_TIMESTAMP_COLUMNS = frozenset({"created_at", "updated_at"})


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
        values = [_to_column_value(name, record.get(name)) for name in OWNER_ITEM_COLUMNS]
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
                name: _to_sqlite_shape(name, value)
                for name, value in zip(OWNER_ITEM_COLUMNS, row, strict=True)
            }
            for row in rows
        ]


def _to_column_value(name: str, value: Any) -> Any:
    """SQLite's shape -> PostgreSQL's column type."""
    if name == "done":
        # SQLite stores 0/1; the column is boolean and psycopg will not coerce.
        return None if value is None else bool(value)
    return value


def _to_sqlite_shape(name: str, value: Any) -> Any:
    """PostgreSQL's column type -> the shape the authority's row has.

    Rendered back rather than compared loosely, so `divergence` can compare
    rows directly and has no translation step of its own to be wrong about.
    """
    if name == "done":
        return None if value is None else int(bool(value))
    if name in _TIMESTAMP_COLUMNS and isinstance(value, datetime):
        # `timestamptz` keeps the instant, not the spelling: a row written as
        # `+00:00` comes back as `Z`. This is a normalisation, so divergence
        # over timestamp columns is an instant comparison, not a string one.
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return value


def divergence(authority_rows: list[dict], mirror: Any) -> list[dict]:
    """Rows where the SQLite authority and `mirror` disagree.

    Returns one record per differing row, or `[]` when they match.

    An unreadable mirror reports a single `MIRROR_UNAVAILABLE` record rather
    than an empty list, and that asymmetry is the whole point: the cutover is
    gated on a session with no divergence, and a missing mirror would satisfy
    that gate by having nothing to disagree with — the migration would advance
    on the strength of a store nobody wrote. Never raises: this runs on a
    read path during dual-write, and a check that can break what it checks is
    worse than no check.
    """
    try:
        mirror_rows = mirror.list_records()
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        return [
            {
                "id": MIRROR_UNAVAILABLE,
                "fields": ["*"],
                "authority": None,
                "mirror": None,
                "detail": f"{type(exc).__name__}: {exc}",
            }
        ]

    mirrored = {row.get("id"): row for row in mirror_rows}
    differences: list[dict] = []
    for row in authority_rows:
        counterpart = mirrored.pop(row.get("id"), None)
        if counterpart is None:
            differences.append(
                {"id": row.get("id"), "fields": ["*"], "authority": row, "mirror": None}
            )
            continue
        fields = sorted(
            name
            for name in OWNER_ITEM_COLUMNS
            if _normalise(row.get(name)) != _normalise(counterpart.get(name))
        )
        if fields:
            differences.append(
                {"id": row.get("id"), "fields": fields, "authority": row, "mirror": counterpart}
            )
    # Rows the mirror has and the authority does not are divergence too — a
    # mirror ahead of the system of record is the state no check would flag if
    # this loop only walked the authority.
    for leftover_id, leftover in mirrored.items():
        differences.append(
            {"id": leftover_id, "fields": ["*"], "authority": None, "mirror": leftover}
        )
    return differences


#: Sentinel id used when the mirror itself could not be read.
MIRROR_UNAVAILABLE = "__mirror_unavailable__"


def _normalise(value: Any) -> Any:
    """Compare 0/1 and False/True as equal; everything else by value.

    SQLite hands back the integers it stores, so a mirror that correctly
    round-tripped a boolean would otherwise read as a difference on every row.
    """
    if isinstance(value, bool):
        return int(value)
    return value
