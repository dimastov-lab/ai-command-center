"""The execution queue's PostgreSQL mirror (VOYN-W0-AICC-SRV-01B, slice 1).

`queue_entry` is the narrowest table in wave 1 of the accepted correspondence
map — no incoming foreign keys, three SQL statements, three modules — which is
why the migration starts here: it establishes the shape of a slice at the
lowest possible blast radius rather than proving it on `task`, which 120
modules touch.

This is a *mirror*, not the authority. `execution_queue.json` keeps that role
until reconciliation shows a clean session and the rollback and backup/restore
drills have run; nothing here decides otherwise, and `test_queue_store.py`
asserts as much rather than leaving it to the docstring.

Two properties are carried over from the SQLite mirror because they are what
make the migration gate meaningful:

*Whole-list replacement.* `replace_entries` deletes and reinserts inside one
transaction, so it is idempotent and so a reader never observes a partially
rebuilt queue. Appending would manufacture the divergence the backfill exists
to remove.

*Order is data.* The queue is displayed and planned in insertion order, so the
position is stored explicitly and read back with an `ORDER BY`. A set-shaped
table would reorder the queue silently, and silently is the problem.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

__all__ = ["PostgresQueueMirror", "QUEUE_ENTRY_COLUMNS"]

#: The queue entry's own columns, in schema order. `position` is deliberately
#: not here: it is how the mirror preserves order, not part of an entry, and a
#: divergence check comparing it against JSON would report a difference that
#: only exists because the mirror had to store something JSON gets for free.
QUEUE_ENTRY_COLUMNS: tuple[str, ...] = (
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


class PostgresQueueMirror:
    """The `queue_entry` table on the accepted PostgreSQL seam."""

    name = "postgres"

    def __init__(self, connection_factory: Any = None) -> None:
        # Injectable so tests can supply a connection without a process-wide
        # pool, and so this module never reaches for global state of its own.
        self._factory = connection_factory

    def _connection(self) -> Any:
        """The pool is resolved on use, never at import.

        `command_center.db.__init__` promises that importing the package pulls
        in neither `aios_db` nor `psycopg`, because the desktop and CLI entry
        points have to keep working on a machine with no PostgreSQL client
        library. A module-level `from command_center.db import pool` here would
        break that promise for every future importer of the queue store — and
        break it at import time, which is the hardest kind of failure to
        attribute to the module that caused it.
        """
        if self._factory is not None:
            return self._factory()
        from command_center.db import pool

        return pool.connection()

    def replace_entries(self, entries: list[dict]) -> None:
        columns = (*QUEUE_ENTRY_COLUMNS, "position")
        placeholders = ", ".join(["%s"] * len(columns))
        rows = [
            tuple(_to_column_value(column, entry.get(column)) for column in QUEUE_ENTRY_COLUMNS)
            + (index,)
            for index, entry in enumerate(entries)
        ]
        with self._connection() as conn:
            # One transaction: a reader must never see the queue half-rebuilt.
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM queue_entry")
                    if rows:
                        cur.executemany(
                            f"INSERT INTO queue_entry ({', '.join(columns)}) "
                            f"VALUES ({placeholders})",
                            rows,
                        )

    def list_entries(self) -> list[dict]:
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {', '.join(QUEUE_ENTRY_COLUMNS)} FROM queue_entry "
                    "ORDER BY position ASC"
                )
                rows = cur.fetchall()
        return [
            {
                column: _as_json_value(value)
                for column, value in zip(QUEUE_ENTRY_COLUMNS, row, strict=True)
            }
            for row in rows
        ]


#: Queue columns PostgreSQL stores as `timestamptz` while the JSON store holds
#: naive local-time strings.
_TIMESTAMP_COLUMNS = frozenset({"added_at", "evaluated_at", "launched_at"})


def _to_column_value(name: str, value: Any) -> Any:
    """Attach the writer's zone to a naive timestamp before it reaches `timestamptz`.

    Without this PostgreSQL stamps a naive string with the *session* time zone,
    silently shifting every mirrored row by the gap between the writing machine
    and the server — no error, just wrong data discovered when reads switch.
    The mirror runs in the writer's own process, so the local zone is the one
    that produced the string.

    The limit is inherent to the naive format rather than to this code: a
    mirror running in a different zone than the writer would attach the wrong
    one. Making the authority timezone-aware is a migration of every existing
    record and is recorded as its own task.
    """
    if name in _TIMESTAMP_COLUMNS and isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo is not None else parsed.astimezone()
    return value


def _as_json_value(value: Any) -> Any:
    """Render a column back into the shape the JSON queue holds.

    The timestamp columns are `timestamptz`; the JSON store holds ISO-8601
    strings. Without this the mirror returns `datetime` objects and the
    divergence check reports every timestamped entry as different — a cutover
    gate that is permanently red, which someone eventually "fixes" by loosening
    the comparison instead of the conversion.

    Rendered back into exactly what `models.iso_now()` produces: naive local
    time at second precision, no offset.

    The first version of this function normalised to UTC with a `Z` suffix,
    "matching what the application writes". It does not: `iso_now`'s own
    docstring says every timestamp here is "local time on the machine that
    wrote them, never assumed to be UTC". So the mirror rendered a shape no
    writer emits, and `queue_divergence` would have reported **every**
    timestamped entry as different — the permanently-red cutover gate this
    docstring warns about, produced by the code that warns about it. The tests
    missed it because they fabricated `Z`-suffixed timestamps, proving the
    conversion against invented data rather than against `iso_now`.
    """
    if isinstance(value, datetime):
        return value.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")
    return value
