"""`contact` and `message` PostgreSQL mirrors (VOYN-W0-AICC-SRV-01B, slice 5).

Fifth slice, and the first **foreign key**: `message.contact_id` references
`contact(id)` on both sides. That is the property this slice worked out, and it
is why two tables share one module while the four earlier tables each got their
own — the child's mirror cannot be reasoned about without the parent's, and
splitting them would put the ordering rule in neither file.

**What the foreign key changes.** Nothing about a row, and everything about
what a *lost* row means. The four mirrored tables before this one are
independent: a mirror write that fails leaves one row missing, reconciliation
reports it, and every later write still succeeds. Here a failed `contact` write
makes every subsequent `message` write for that contact fail too — the target
refuses the child because the parent it references is not there — and both
failures are swallowed by the dual-write hooks, exactly as designed. So one
dropped parent silently becomes a growing hole, and the only thing that shows
it is the reconciliation nobody has run yet. That is not a defect introduced
here; it is `VOYN-W0-AICC-MIRROR-SILENT-DROP` acquiring a multiplier, and it is
measured rather than asserted — `tests/db/test_networking_store.py` reproduces
the cascade against a real PostgreSQL.

**Ordering.** The authority writes the parent first because its own foreign key
requires it, and the mirror hooks run in that same order, so the mirror needs
no ordering logic of its own. This module deliberately does **not** try to
create a missing parent on the fly: inventing a `contact` row to make a
`message` land would put a row in the mirror that the authority never wrote,
which is the "mirror ahead of the system of record" state no reconciliation
flags as wrong. A refused child is the honest outcome, and it is visible in
reconciliation as the missing row it is.

Slice 5 introduced a private base class here for the two tables; slice 7 moved
that behaviour to `command_center/db/table_mirror.py` and folded the earlier
stores onto it, which is what
`VOYN-W0-AICC-MIRROR-STORE-BASE-CONSOLIDATION` was opened for. Unlike
`digest_item`, both tables' public readers already return the stored shape
(`dict(row)`), so reconciliation needs no reader of its own — a test pins that
rather than leaving it to be rediscovered.
"""

from __future__ import annotations

from command_center.db.mirror_support import MIRROR_UNAVAILABLE, ColumnCodec
from command_center.db.table_mirror import MirroredTable, PostgresTableMirror, divergence_against

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

CONTACT = MirroredTable(
    table="contact",
    columns=CONTACT_COLUMNS,
    codec=ColumnCodec(timestamps=frozenset({"created_at", "updated_at"})),
)

#: A message is write-once: it carries `created_at` and no `updated_at`.
MESSAGE = MirroredTable(
    table="message",
    columns=MESSAGE_COLUMNS,
    codec=ColumnCodec(timestamps=frozenset({"created_at"})),
)


class PostgresContactMirror(PostgresTableMirror):
    """The `contact` table on the accepted PostgreSQL seam — the FK's parent."""

    spec = CONTACT


class PostgresMessageMirror(PostgresTableMirror):
    """The `message` table — the FK's child.

    An upsert here fails when the referenced `contact` is absent from the
    mirror, and that failure is swallowed by the dual-write hook. Deliberate:
    the alternative is to invent the parent, which puts a row in the mirror the
    authority never wrote.
    """

    spec = MESSAGE


#: Rows where the SQLite authority and a mirror disagree on `contact`.
contact_divergence = divergence_against(CONTACT)

#: Rows where the SQLite authority and a mirror disagree on `message`.
#:
#: Reported per table, not per relationship: a message missing because its
#: contact never reached the mirror shows up here as a missing row, and the
#: contact shows up in `contact_divergence` as a missing row too. Two true
#: reports of one cause beats one report that guesses at the cause.
message_divergence = divergence_against(MESSAGE)
