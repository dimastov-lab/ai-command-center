"""The `digest_item` table's PostgreSQL mirror (VOYN-W0-AICC-SRV-01B, slice 4).

Fourth table, chosen for the two things it adds rather than for its size, and
both were declared blockers before this slice started rather than discovered
inside it.

**`jsonb`.** `refs_json` is the first mirrored column PostgreSQL stores as
`jsonb` while the authority stores JSON text, and 22 columns in the accepted map
follow it. The hazard is not the conversion but the *comparison*: a text ->
`jsonb` -> text round trip does not preserve the source bytes. Measured on
PostgreSQL 17.6 rather than assumed — `{"b": 1, "a": 2}` comes back
`{"a": 2, "b": 1}` — so key order and separators belong to the database, and
comparing the column as text would report every object-valued row as different.
That is the permanently-red cutover gate this migration keeps almost building,
and the one somebody eventually satisfies by loosening the comparison. These
columns are compared as parsed values instead (`ColumnCodec.comparable`), and
the map's other requirement is met on the way in: unparseable text raises
rather than reaching the column. Closes `VOYN-W0-AICC-MIRROR-JSONB-COMPARE`.

**Deletes.** `delete_digest_items_for_day` is the first authority operation in
this migration that *removes* rows: the digest engine rebuilds a day by deleting
it and re-inserting. A mirror that only ever upserts would keep every superseded
row forever, and reconciliation would report a mirror permanently ahead of the
system of record — true, useless, and exactly the noise that gets a check
switched off. So the deletion is mirrored, and it is mirrored as *the same
predicate* rather than as the list of ids it removed: translating it to ids
would mean reading the authority before the delete, which is one more query and
a race against the very rebuild it is following.

Shape otherwise follows slices 2 and 3. SQLite is the authority, this is a
dual-write, reads are not switched, and the cutover waits on reconciliation
plus the rollback and backup/restore drills.
"""

from __future__ import annotations

from typing import Any

from command_center.db import mirror_support
from command_center.db.mirror_support import MIRROR_UNAVAILABLE, ColumnCodec

__all__ = [
    "DIGEST_ITEM_COLUMNS",
    "MIRROR_UNAVAILABLE",
    "PostgresDigestItemMirror",
    "divergence",
]

#: Column order as declared in `command_center/db/sql/0001_initial.up.sql`,
#: pinned against that file by a test rather than trusted to stay in step.
DIGEST_ITEM_COLUMNS: tuple[str, ...] = (
    "id",
    "title",
    "body",
    "category",
    "refs_json",
    "created_at",
    "day",
    "position",
    "project_ref",
)

#: `day` is deliberately not a timestamp: the map keeps it `text` on both sides
#: because it is a build label the caller supplies, not a date the application
#: computed. `position` is an ordinary integer here — unlike the queue's, it is
#: the authority's own column rather than something the mirror added to
#: preserve order, so it is compared like any other value.
_CODEC = ColumnCodec(
    timestamps=frozenset({"created_at"}),
    json_values=frozenset({"refs_json"}),
)


class PostgresDigestItemMirror:
    """The `digest_item` table on the accepted PostgreSQL seam."""

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

        `refs_json` is cast rather than adapted. psycopg adapts no Python
        object to `jsonb` on its own — a `list` goes over as a PostgreSQL array
        and fails to parse, a `dict` raises `cannot adapt type 'dict'`, both
        verified against 17.6 — so the column's own text is sent and cast with
        `::jsonb`. The value PostgreSQL parses is then the one the authority
        stored, not a re-serialisation of it, and no driver type reaches
        `mirror_support`.
        """
        values = [_CODEC.to_column(name, record.get(name)) for name in DIGEST_ITEM_COLUMNS]
        placeholders = ", ".join(
            "%s::jsonb" if name in _CODEC.json_values else "%s" for name in DIGEST_ITEM_COLUMNS
        )
        assignments = ", ".join(
            f"{name} = EXCLUDED.{name}" for name in DIGEST_ITEM_COLUMNS if name != "id"
        )
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO digest_item ({', '.join(DIGEST_ITEM_COLUMNS)}) "
                    f"VALUES ({placeholders}) "
                    f"ON CONFLICT (id) DO UPDATE SET {assignments}",
                    values,
                )

    def delete_day(self, day: str) -> None:
        """Remove every mirrored row built for `day`.

        The authority's rebuild deletes a day and re-inserts it, so a mirror
        without this accumulates every superseded row and reconciliation
        reports it permanently ahead of the system of record.
        """
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM digest_item WHERE day = %s", (day,))

    def list_records(self) -> list[dict]:
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {', '.join(DIGEST_ITEM_COLUMNS)} FROM digest_item ORDER BY id"
                )
                rows = cur.fetchall()
        return [
            {
                name: _CODEC.to_authority(name, value)
                for name, value in zip(DIGEST_ITEM_COLUMNS, row, strict=True)
            }
            for row in rows
        ]


def divergence(authority_rows: list[dict], mirror: Any) -> list[dict]:
    """Rows where the SQLite authority and `mirror` disagree.

    **Takes rows in the shape SQLite stores** — `runtime/db/wave1.py`'s
    `list_digest_items_stored`, not its other readers. Worth saying here
    because for this table alone that is not the shape the repository hands
    out: every public reader returns `_decode_digest_row` output, which pops
    `refs_json` and substitutes a decoded `refs`. Fed one of those, this
    reports every row divergent on the one column the slice exists to migrate,
    and the failure looks like a broken mirror rather than a wrong question.

    Passes the codec, which is what makes `refs_json` compare as a parsed value
    rather than as text — see `mirror_support.divergence` for the rest.
    """
    return mirror_support.divergence(authority_rows, mirror, DIGEST_ITEM_COLUMNS, _CODEC)
