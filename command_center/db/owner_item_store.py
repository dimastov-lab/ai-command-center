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

from datetime import datetime
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
    if name in _TIMESTAMP_COLUMNS and isinstance(value, str) and value:
        return _to_instant(value)
    return value


def _to_instant(value: str) -> Any:
    """Attach the writer's zone to a naive timestamp before it reaches `timestamptz`.

    This is the conversion the first version of this module got wrong, and the
    failure was silent in both directions. `models.iso_now()` returns *naive
    local time* — its own docstring says every timestamp in this application is
    "local time on the machine that wrote them, never assumed to be UTC". A
    naive string handed to `timestamptz` does not error: PostgreSQL stamps it
    with the *session* time zone, so every mirrored row is offset by the gap
    between the writing machine and the server. On a UTC+3 laptop against a UTC
    server that is a three-hour error in every row, discovered only when reads
    switch.

    Interpreting the string in the local zone is the only reading consistent
    with what the authority means by it. The mirror runs in the writer's own
    process, so "local" is the same zone that produced the string.

    The limit is inherent to the naive format, not to this code, and is stated
    rather than hidden: a mirror running in a different zone than the writer
    would attach the wrong one. Fixing that properly means making the authority
    timezone-aware, which is a migration of `data/tasks.json` and every existing
    record — tracked as `VOYN-W0-AICC-TZ-AWARE-TIMESTAMPS`.

    And the reconciliation cannot catch a violation of it: the outbound render
    converts back through the same zone, so a mirror running in the wrong one
    reproduces the original wall clock and `divergence` reports agreement it
    never verified. Independent review demonstrated this — the same row
    mirrored from an MSK and a UTC process stored instants three hours apart
    and both reconciled clean. Until the authority is timezone-aware
    (VOYN-W0-AICC-TZ-AWARE-TIMESTAMPS), the mirror must run in the writer's
    process, and that is an operational constraint rather than something the
    gate enforces.
    """
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.astimezone()


def _to_sqlite_shape(name: str, value: Any) -> Any:
    """PostgreSQL's column type -> the shape the authority's row has.

    Rendered back rather than compared loosely, so `divergence` can compare
    rows directly and has no translation step of its own to be wrong about.
    """
    if name == "done":
        return None if value is None else int(bool(value))
    if name in _TIMESTAMP_COLUMNS and isinstance(value, datetime):
        # Rendered back into exactly what `models.iso_now()` produces: naive
        # local time, second precision, no offset. An earlier version rendered
        # UTC with a `Z` suffix, which no writer in this application emits — so
        # `divergence` reported every single row as different, which is the
        # permanently-red cutover gate this module's docstring warns about.
        # The tests did not catch it because they fabricated `Z`-suffixed
        # timestamps: the conversion was proved against invented data.
        return value.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")
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
