"""`contact` and `message` PostgreSQL mirrors (VOYN-W0-AICC-SRV-01B, slice 5).

Fifth slice, and the first **foreign key**: `message.contact_id` references
`contact(id)` on both sides. That is the property this slice exists to work
out, and it is why two tables share one module while the four earlier tables
each got their own — the child's mirror cannot be reasoned about without the
parent's, and splitting them would put the ordering rule in neither file.

**What the foreign key changes.** Nothing about a row, and everything about
what a *lost* row means. The four mirrored tables so far are independent: a
mirror write that fails leaves one row missing, reconciliation reports it, and
every later write still succeeds. Here a failed `contact` write makes every
subsequent `message` write for that contact fail too — the target refuses the
child because the parent it references is not there — and both failures are
swallowed by the dual-write hooks, exactly as designed. So one dropped parent
silently becomes a growing hole, and the only thing that shows it is the
reconciliation nobody has run yet. That is not a defect introduced here; it is
`VOYN-W0-AICC-MIRROR-SILENT-DROP` acquiring a multiplier, and it is measured
rather than asserted — `tests/db/test_networking_store.py` reproduces the
cascade against a real PostgreSQL.

**Ordering.** The authority writes the parent first because its own foreign key
requires it, and the mirror hooks run in that same order, so the mirror needs
no ordering logic of its own. This module deliberately does **not** try to
create a missing parent on the fly: inventing a `contact` row to make a
`message` land would put a row in the mirror that the authority never wrote,
which is the "mirror ahead of the system of record" state no reconciliation
flags as wrong. A refused child is the honest outcome, and it is visible in
reconciliation as the missing row it is.

Shape otherwise follows slices 2–4. SQLite is the authority, this is a
dual-write, reads are not switched, and the cutover waits on reconciliation
plus the rollback and backup/restore drills. Unlike `digest_item`, both tables'
public readers already return the stored shape (`dict(row)`), so reconciliation
needs no reader of its own — a test pins that rather than leaving it to be
rediscovered.
"""

from __future__ import annotations

from typing import Any

from command_center.db import mirror_support
from command_center.db.mirror_support import MIRROR_UNAVAILABLE, ColumnCodec

__all__ = [
    "CONTACT_COLUMNS",
    "MESSAGE_COLUMNS",
    "MIRROR_UNAVAILABLE",
    "PostgresContactMirror",
    "PostgresMessageMirror",
    "contact_divergence",
    "message_divergence",
]

#: Column order as declared in `command_center/db/sql/0001_initial.up.sql`,
#: pinned against that file by a test rather than trusted to stay in step.
CONTACT_COLUMNS: tuple[str, ...] = (
    "id",
    "display_name",
    "handle",
    "org",
    "note",
    "project_ref",
    "version",
    "created_at",
    "updated_at",
)

MESSAGE_COLUMNS: tuple[str, ...] = (
    "id",
    "contact_id",
    "direction",
    "kind",
    "body",
    "project_ref",
    "created_at",
)

_CONTACT_CODEC = ColumnCodec(timestamps=frozenset({"created_at", "updated_at"}))
#: A message is write-once: it carries `created_at` and no `updated_at`.
_MESSAGE_CODEC = ColumnCodec(timestamps=frozenset({"created_at"}))


class _TableMirror:
    """The upsert/list half both tables share.

    A **new base for the two tables added here**, not an extraction: three of
    the four earlier mirrors — `owner_item_store`, `conflict_store`,
    `digest_item_store` — still hand-roll their own `_connection`, `upsert` and
    `list_records`, and this commit moves none of them. `queue_store` is not a
    fourth: it has `replace_entries` and `list_entries` instead, because the
    queue mirrors by bulk replace with a `position` column the JSON authority
    does not have, and it is deliberately not a candidate for this base. An earlier version of
    this docstring claimed the `mirror_support` precedent, and independent
    review pointed out that the precedent runs the other way — `mirror_support`
    moved its existing callers onto it in the same slice, which is why the
    migration ended up with one shape for the conversion instead of two.

    It stands because the two tables here are written together and would
    otherwise carry a fourth and fifth copy between them. Folding the four
    earlier stores onto it is `VOYN-W0-AICC-MIRROR-STORE-BASE-CONSOLIDATION`,
    and until that closes there are two shapes for the same thing — recorded
    rather than left for someone to notice.

    The table name and its columns are the only difference between these two
    mirrors, so they are the only things a subclass supplies.
    """

    name = "postgres"
    table: str = ""
    columns: tuple[str, ...] = ()
    codec: ColumnCodec = ColumnCodec()

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
        values = [self.codec.to_column(name, record.get(name)) for name in self.columns]
        assignments = ", ".join(f"{name} = EXCLUDED.{name}" for name in self.columns if name != "id")
        placeholders = ", ".join(["%s"] * len(self.columns))
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO {self.table} ({', '.join(self.columns)}) "
                    f"VALUES ({placeholders}) "
                    f"ON CONFLICT (id) DO UPDATE SET {assignments}",
                    values,
                )

    def list_records(self) -> list[dict]:
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT {', '.join(self.columns)} FROM {self.table} ORDER BY id")
                rows = cur.fetchall()
        return [
            {
                name: self.codec.to_authority(name, value)
                for name, value in zip(self.columns, row, strict=True)
            }
            for row in rows
        ]


class PostgresContactMirror(_TableMirror):
    """The `contact` table on the accepted PostgreSQL seam — the FK's parent."""

    table = "contact"
    columns = CONTACT_COLUMNS
    codec = _CONTACT_CODEC


class PostgresMessageMirror(_TableMirror):
    """The `message` table — the FK's child.

    An upsert here fails when the referenced `contact` is absent from the
    mirror, and that failure is swallowed by the dual-write hook. Deliberate:
    the alternative is to invent the parent, which puts a row in the mirror the
    authority never wrote.
    """

    table = "message"
    columns = MESSAGE_COLUMNS
    codec = _MESSAGE_CODEC


def contact_divergence(authority_rows: list[dict], mirror: Any) -> list[dict]:
    """Rows where the SQLite authority and `mirror` disagree on `contact`."""
    return mirror_support.divergence(authority_rows, mirror, CONTACT_COLUMNS)


def message_divergence(authority_rows: list[dict], mirror: Any) -> list[dict]:
    """Rows where the SQLite authority and `mirror` disagree on `message`.

    Reported per table, not per relationship: a message missing because its
    contact never reached the mirror shows up here as a missing row, and the
    contact shows up in `contact_divergence` as a missing row too. Two true
    reports of one cause beats one report that guesses at the cause.
    """
    return mirror_support.divergence(authority_rows, mirror, MESSAGE_COLUMNS)
