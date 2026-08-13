"""Shared machinery for the PostgreSQL mirrors (VOYN-W0-AICC-SRV-01B).

Extracted at the third slice, which is when the duplication stopped being
hypothetical: `queue_store` and `owner_item_store` each carried their own copy
of the timestamp conversion, and `owner_item_store` its own `divergence`. The
conversion is the one piece of this migration that has already been wrong once
and cost a review round — three copies of it is three places to fix it the next
time, and two of them would be found late.

Deliberately *not* extracted earlier. A helper designed against one caller
would have baked in that caller's assumptions — including the one that made the
conversion wrong (see `to_instant`); at two callers there is a single example to
generalise from. Three is the first point where the shape is evidence rather
than a guess, and the third table was written against this module rather than
copied and then reconciled with it.

Pure functions over `datetime` and dicts: no driver import, so importing this
costs nothing on a machine with no PostgreSQL client library, which is the
promise `command_center.db.__init__` makes to the desktop and CLI entry points.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

__all__ = [
    "MIRROR_UNAVAILABLE",
    "ColumnCodec",
    "divergence",
    "render_authority_timestamp",
    "to_instant",
]

#: Sentinel id used when the mirror itself could not be read.
MIRROR_UNAVAILABLE = "__mirror_unavailable__"


def to_instant(value: str) -> datetime:
    """Attach the writer's zone to a naive timestamp bound for `timestamptz`.

    `models.iso_now()` returns *naive local time* — its docstring says every
    timestamp in this application is "local time on the machine that wrote
    them, never assumed to be UTC". Handing that to `timestamptz` does not
    error: PostgreSQL stamps it with the *session* time zone, so every mirrored
    row is silently offset by the gap between the writing machine and the
    server. Interpreting it in the local zone is the only reading consistent
    with what the authority means by it, and the mirror runs in the writer's
    own process, so "local" is the zone that produced the string.

    The limit is inherent to the naive format rather than to this code, and
    reconciliation is **blind** to a violation of it: the render below converts
    back through the same zone, so a mirror running in the wrong one reproduces
    the original wall clock and `divergence` reports agreement it never
    verified. Independent review demonstrated this — the same row mirrored from
    an MSK and a UTC process stored instants three hours apart and both
    reconciled clean. Until `VOYN-W0-AICC-TZ-AWARE-TIMESTAMPS` closes — by
    making the authority timezone-aware *or* by giving reconciliation its own
    zone check — the mirror must run in the writer's process, and that is an
    operational constraint rather than something any gate enforces.
    """
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.astimezone()


def render_authority_timestamp(value: datetime) -> str:
    """Render `timestamptz` back into exactly what `models.iso_now()` emits.

    Naive local, second precision, no offset. An earlier version rendered UTC
    with a `Z` suffix "matching what the application writes" — it does not, and
    the result was a divergence check that called every row different, which is
    a cutover gate permanently red. A red gate nobody can satisfy is one
    somebody eventually satisfies by loosening the comparison.

    `timespec="seconds"` matches `iso_now` and is therefore lossless for every
    column mirrored today. It is *not* the right renderer for a store that
    writes `datetime.now(UTC).isoformat()` — eight of those are queued for
    later waves, and copying this onto them reproduces the same class of defect
    in reverse. Tracked as `VOYN-W0-AICC-MIRROR-RENDER-SHARED`, a declared
    blocker on the first such table.
    """
    return value.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")


@dataclass(frozen=True)
class ColumnCodec:
    """Per-column conversion between the authority's shape and the target's.

    The accepted correspondence map counts 105 columns whose *values* need
    converting, and the two classes mirrored so far account for most of them:
    75 `TEXT` -> `timestamptz` and 8 `INTEGER 0/1` -> `boolean`. Naming the
    columns per table and sharing the conversion is the opposite of the failure
    mode this migration keeps hitting, where each table restates the conversion
    and one restatement is subtly different from the others.

    Columns not named here pass through untouched, which is correct for every
    remaining class the three mirrored tables contain — including the two the
    map calls out as deliberately `text` on both sides (`owner_item.due`,
    `digest_item.day`: free user input, not dates). `jsonb` is *not* handled and
    must not be added by pattern-matching on this class: the map requires a
    parse check on the way in, and a text -> `jsonb` -> text round trip does not
    preserve the source bytes, so it needs its own comparison rule rather than
    another entry in a frozenset.
    """

    #: Columns PostgreSQL declares `timestamptz` while the authority stores text.
    timestamps: frozenset[str] = field(default_factory=frozenset)
    #: Columns PostgreSQL declares `boolean` while the authority stores 0/1.
    flags: frozenset[str] = field(default_factory=frozenset)

    def to_column(self, name: str, value: Any) -> Any:
        """The authority's shape -> the PostgreSQL column's type."""
        if name in self.flags:
            # psycopg will not coerce SQLite's 0/1 into a boolean column.
            return None if value is None else bool(value)
        if name in self.timestamps and isinstance(value, str) and value:
            return to_instant(value)
        return value

    def to_authority(self, name: str, value: Any) -> Any:
        """The PostgreSQL column's type -> the shape the authority's row has.

        Rendered back rather than compared loosely, so `divergence` compares
        rows directly and has no translation step of its own to be wrong about.
        """
        if name in self.flags:
            return None if value is None else int(bool(value))
        if name in self.timestamps and isinstance(value, datetime):
            return render_authority_timestamp(value)
        return value


def divergence(
    authority_rows: Iterable[dict],
    mirror: Any,
    columns: Iterable[str],
) -> list[dict]:
    """Rows where the authority and `mirror` disagree.

    One record per differing row, `[]` when they match, and four shapes are
    reported rather than three: a field difference, a row missing from the
    mirror, an unreadable mirror, and — the one a loop over the authority alone
    would miss — a row the mirror has and the authority does not. A mirror
    *ahead* of the system of record is the state nothing else would flag.

    An unreachable mirror reports `MIRROR_UNAVAILABLE` rather than `[]`,
    because the cutover is gated on a session with no divergence and an absent
    store has nothing to disagree with: returning `[]` would let the migration
    advance on the strength of a store nobody wrote.

    Never raises. This runs on a read path during dual-write, and a check that
    can break what it checks is worse than no check.
    """
    try:
        mirror_rows = list(mirror.list_records())
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

    names = tuple(columns)
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
            for name in names
            if _comparable(row.get(name)) != _comparable(counterpart.get(name))
        )
        if fields:
            differences.append(
                {"id": row.get("id"), "fields": fields, "authority": row, "mirror": counterpart}
            )
    for leftover_id, leftover in mirrored.items():
        differences.append(
            {"id": leftover_id, "fields": ["*"], "authority": None, "mirror": leftover}
        )
    return differences


def _comparable(value: Any) -> Any:
    """Compare 0/1 and False/True as equal; everything else by value.

    SQLite hands back the integers it stores, so a correctly round-tripped
    boolean would otherwise read as a difference on every row. Unconditional
    rather than a per-table hook: `True` and `1` mean the same thing in every
    table this reconciles, and a hook is a place for one table to disagree.
    """
    if isinstance(value, bool):
        return int(value)
    return value
