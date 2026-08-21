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

import inspect
import json
from dataclasses import dataclass, field
from datetime import datetime
from itertools import islice
from typing import Any, Iterable

__all__ = [
    "MIRROR_UNAVAILABLE",
    "WINDOW_ROWS",
    "ColumnCodec",
    "divergence",
    "render_authority_timestamp",
    "to_instant",
    "windowed_divergence",
]

#: Sentinel id used when the mirror itself could not be read.
MIRROR_UNAVAILABLE = "__mirror_unavailable__"

#: Authority rows a windowed comparison holds at once, and therefore roughly the
#: number of mirrored rows fetched beside them.
#:
#: Measured rather than picked. Over a 200 000-row table the whole-table
#: comparison peaked at 294.9 MB and this window at 28.6 MB — 10.3x — for the
#: identical divergence records. The saving is a ratio rather than a constant:
#: it is what the two sides stop holding, so it grows with the table, which is
#: exactly why an append-only table is what needed this.
WINDOW_ROWS = 5000


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
    remaining class the mirrored tables contain — including the two the map
    calls out as deliberately `text` on both sides (`owner_item.due`,
    `digest_item.day`: free user input, not dates).

    `json_values` is the exception that proves why the other two are simple.
    A `timestamptz` renders back to the authority's exact string; `jsonb` does
    **not**, and this was measured rather than assumed: `{"b": 1, "a": 2}` sent
    through PostgreSQL 17.6 comes back `{"a": 2, "b": 1}`. Key order and
    separators are the database's, so a text comparison would report every
    object-valued row as different — the permanently-red gate this migration
    keeps almost building. Those columns are therefore compared as *parsed
    values* (see `comparable`), which is the only comparison that means
    anything for them.
    """

    #: Columns PostgreSQL declares `timestamptz` while the authority stores text.
    timestamps: frozenset[str] = field(default_factory=frozenset)
    #: Columns PostgreSQL declares `boolean` while the authority stores 0/1.
    flags: frozenset[str] = field(default_factory=frozenset)
    #: Columns PostgreSQL declares `jsonb` while the authority stores JSON text.
    json_values: frozenset[str] = field(default_factory=frozenset)

    def to_column(self, name: str, value: Any) -> Any:
        """The authority's shape -> the PostgreSQL column's type.

        JSON columns keep their **text**, deliberately. psycopg adapts neither
        `dict` nor `list` to `jsonb` — a `list` is sent as a PostgreSQL array
        and `dict` raises outright, both verified against 17.6 — so the store
        casts the text with `%s::jsonb` instead. That keeps this module free of
        a driver import, which `command_center.db.__init__` promises to every
        importer, and it means the value PostgreSQL parses is the value the
        authority stored rather than a re-serialisation of it.

        The parse still happens here, and its only purpose is to fail loudly:
        the accepted map requires unparseable text to break the insert rather
        than reach `jsonb`, and a `ValueError` raised here names the column,
        while the same rejection from PostgreSQL arrives as a driver error
        about a statement.
        """
        if name in self.flags:
            # psycopg will not coerce SQLite's 0/1 into a boolean column.
            return None if value is None else bool(value)
        if name in self.timestamps and isinstance(value, str) and value:
            return to_instant(value)
        if name in self.json_values and isinstance(value, str) and value:
            try:
                json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{name} is not valid JSON: {exc}") from exc
            return value
        return value

    def to_authority(self, name: str, value: Any) -> Any:
        """The PostgreSQL column's type -> the shape the authority's row has.

        Rendered back rather than compared loosely, so `divergence` compares
        rows directly and has no translation step of its own to be wrong about
        — with `jsonb` the one exception, because there is no such shape to
        render back to. It is returned parsed, exactly as the driver hands it
        over, and `comparable` parses the authority's side to meet it.
        """
        if name in self.flags:
            return None if value is None else int(bool(value))
        if name in self.timestamps and isinstance(value, datetime):
            return render_authority_timestamp(value)
        return value

    def comparable(self, name: str, value: Any) -> Any:
        """One column's value reduced to what a comparison should look at.

        Two reductions, and they are not symmetric by accident. Booleans are
        normalised unconditionally — `True` and `1` mean the same thing in
        every table this reconciles. JSON is normalised only for the columns a
        table declared as `jsonb`, because parsing a string that merely looks
        like JSON would make two rows agree on a column the target stores as
        text, which is a false clean rather than a false difference.

        Unparseable text compares as itself, and the guarantee that follows is
        narrower than it first appears. `to_column` refuses such text, so no
        *unparseable* value reaches the mirror; but a `jsonb` column may hold a
        JSON **string scalar**, which the driver returns as a plain `str`, and
        this method cannot tell which side it is looking at. Mirror `"not
        json"` — a perfectly valid `jsonb` string — therefore compares equal to
        authority text `not json`, which is a false clean.

        Unreachable for every column mirrored today: all of them are written by
        `json.dumps`, which never emits a bare scalar here. Stated rather than
        fixed because the fix belongs where the sides are distinguishable, not
        in a method that sees two values and no provenance —
        `VOYN-W0-AICC-MIRROR-JSON-SCALAR-AMBIGUITY`. Found by independent
        review, which produced the counterexample against the flat claim this
        docstring used to make.
        """
        if isinstance(value, bool):
            return int(value)
        if name in self.json_values and isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        return value


def divergence(
    authority_rows: Iterable[dict],
    mirror: Any,
    columns: Iterable[str],
    codec: ColumnCodec | None = None,
    *,
    key: str | tuple[str, ...] = "id",
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

    `codec` supplies the per-column comparison. Omitting it compares by value
    with booleans normalised, which is right for every table whose columns the
    target stores in a shape the authority can be rendered back into; a table
    with a `jsonb` column must pass its codec, because there is no such shape
    for `jsonb` and a text comparison would report every object-valued row as
    different.

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
    compare = codec.comparable if codec is not None else _comparable
    # Rows are matched by the table's own primary key, which is neither always
    # `id` nor always a single column: `council_decision` is keyed by
    # `motion_id` whose `id` column is not unique, and `provider_attempt` by
    # `(run_id, attempt_number)`. Pairing on the wrong thing compares unrelated
    # rows or collapses several into one — and reports agreement either way.
    keys = (key,) if isinstance(key, str) else tuple(key)

    def identity(row: dict) -> tuple:
        return tuple(row.get(name) for name in keys)

    mirrored = {identity(row): row for row in mirror_rows}
    differences: list[dict] = []
    for row in authority_rows:
        row_key = identity(row)
        counterpart = mirrored.pop(row_key, None)
        if counterpart is None:
            differences.append(
                {"id": _reported(row_key), "fields": ["*"], "authority": row, "mirror": None}
            )
            continue
        fields = sorted(
            name
            for name in names
            if compare(name, row.get(name)) != compare(name, counterpart.get(name))
        )
        if fields:
            differences.append(
                {"id": _reported(row_key), "fields": fields, "authority": row, "mirror": counterpart}
            )
    for leftover_id, leftover in mirrored.items():
        differences.append(
            {"id": _reported(leftover_id), "fields": ["*"], "authority": None, "mirror": leftover}
        )
    return differences


def windowed_divergence(
    authority_rows: Iterable[dict],
    mirror: Any,
    columns: Iterable[str],
    codec: ColumnCodec | None = None,
    *,
    key: str | tuple[str, ...] = "id",
    window: int = WINDOW_ROWS,
) -> list[dict]:
    """`divergence`, asked a key-window at a time instead of all at once.

    Same four shapes, same records, and deliberately the same comparison: each
    window is handed to `divergence` above as an object carrying exactly
    `list_records()`. Nothing here decides whether two rows agree. A second,
    window-aware definition of equality is the trade this migration keeps being
    offered and keeps refusing.

    What changes is what is resident. `divergence` materialises both sides,
    which for an append-only table — `run_event`, `provider_attempt`,
    `audit_finding` — is a ceiling nobody chose and one that grows with the
    table. Measured over 200 000 rows: 294.9 MB whole against 28.6 MB at
    `window=5000`, reporting the identical records. Peak here is two windows of
    the authority (one being compared, one already read for its boundary) plus
    the mirrored rows of one interval, plus the report — which is as large as
    the disagreement is, and is the answer rather than an overhead.

    `authority_rows` is consumed lazily and **must arrive in key order**: byte
    order, which is what SQLite's `BINARY` means and what the target is asked
    for under `COLLATE "C"` (see `PostgresTableMirror.list_records`). A caller
    handing over a list gets the mirror side's saving only; one handing over a
    cursor gets both.

    **Both ends of the walk are open, and that is the property to keep.** The
    windows are `(-inf, k1), [k1, k2), ... , [kn, +inf)`, cut at the authority's
    own keys, and consecutive ones share a boundary — so their union is the
    entire key space and every mirrored row falls in exactly one. A prototype
    that bounded the walk by the authority's own first and last key instead lost
    a row the mirror held with a key above all of them: the `unexpected` shape,
    a mirror *ahead* of the system of record, which is the one thing a loop over
    the authority cannot see and the reason `divergence` reports four shapes and
    not three. An empty authority is the same rule at its limit — one window
    over everything, so a mirror holding rows nobody wrote is still reported
    rather than compared against nothing.

    Raises rather than reports on two caller errors: an authority out of key
    order, and a mirror whose `list_records` takes no bounds. `divergence`
    promises never to raise because it runs on the dual-write read path; this
    runs in a backfill and a cutover gate, where a wrong call must not be dressed
    up as a finding about the data. Out-of-order input fails in the *safe*
    direction anyway — overlapping windows report rows twice rather than skipping
    them, because that union covers the key space whatever order the boundaries
    arrive in — so the refusal buys a legible failure, not a safe one.
    """
    if window < 1:
        raise ValueError(f"a window holds at least one row, not {window}")
    _refuse_a_mirror_that_cannot_be_windowed(mirror)

    names = tuple(columns)
    keys = (key,) if isinstance(key, str) else tuple(key)

    def identity(row: dict) -> tuple:
        return tuple(row.get(name) for name in keys)

    def compare(rows: list[dict], key_from: tuple | None, key_to: tuple | None) -> list[dict]:
        return divergence(rows, _KeyWindow(mirror, key_from, key_to), names, codec, key=keys)

    reported: list[dict] = []
    rows = iter(authority_rows)
    pending: list[dict] | None = None  # the window still awaiting its upper bound
    lower: tuple | None = None  # its lower bound; `None` on the first, which is the point
    highest: tuple | None = None  # the order witness, carried across batches

    while True:
        batch = list(islice(rows, window))
        if not batch:
            break
        highest = _in_key_order(batch, identity, highest)
        boundary = identity(batch[0])
        if pending is not None:
            found = compare(pending, lower, boundary)
            unreadable = _unreadable(found, lower, boundary)
            if unreadable is not None:
                return unreadable
            reported.extend(found)
            lower = boundary
        pending = batch

    found = compare(pending or [], lower, None)
    unreadable = _unreadable(found, lower, None)
    if unreadable is not None:
        return unreadable
    reported.extend(found)
    return reported


class _KeyWindow:
    """The mirror narrowed to one half-open key interval, under the one name
    `divergence` knows.

    An adapter rather than a `divergence` that understands windows: the
    comparison keeps taking "something with `list_records()`", so a window and a
    whole table travel the same code path and there is nothing to keep in step
    between them. It is also what lets a caller window a store this module has
    never heard of — an adapter, a test's fake — without it being a
    `PostgresTableMirror`.

    Bounds are always the key as a tuple, including the single-column case, so
    this class has no shape decision to make; `list_records` accepts either.
    """

    __slots__ = ("_key_from", "_key_to", "_mirror")

    def __init__(self, mirror: Any, key_from: tuple | None, key_to: tuple | None) -> None:
        self._mirror = mirror
        self._key_from = key_from
        self._key_to = key_to

    def list_records(self) -> list[dict]:
        return self._mirror.list_records(key_from=self._key_from, key_to=self._key_to)


def _refuse_a_mirror_that_cannot_be_windowed(mirror: Any) -> None:
    """Refuse a mirror whose `list_records` takes no bounds, before any read.

    Without this the first window's call raises `TypeError` *inside*
    `divergence`, which catches everything and reports `MIRROR_UNAVAILABLE` —
    "the mirror could not be read". That is a FAIL claiming the store is
    unreachable when what is actually wrong is the call, and a gate would then
    declare its divergence total not established on the strength of a typo.
    """
    try:
        signature = inspect.signature(mirror.list_records)
    except (TypeError, ValueError):
        return  # Not introspectable; let the call itself answer.
    try:
        signature.bind(key_from=None, key_to=None)
    except TypeError:
        raise TypeError(
            f"{type(mirror).__name__}.list_records takes no `key_from`/`key_to`, so it "
            "cannot answer a window. See `PostgresTableMirror.list_records`, which takes "
            "them as optional parameters rather than as a second reader."
        ) from None


def _in_key_order(batch: list[dict], identity: Any, highest: tuple | None) -> tuple:
    """Check one batch continues the ascending key walk; return its last key.

    Equal keys are refused along with descending ones. A repeated key means the
    column being walked is not the unique thing it was declared to be, and
    `divergence` pairs rows through a dict — so the second row would silently
    replace the first and the comparison would report agreement about a row it
    never looked at.
    """
    for row in batch:
        current = identity(row)
        try:
            ordered = highest is None or highest < current
        except TypeError as exc:
            raise ValueError(
                f"the authority's keys are not mutually comparable: {highest!r} then "
                f"{current!r} ({exc}). A window is an interval, so a key column holding "
                "more than one type has no order to cut it at."
            ) from exc
        if not ordered:
            raise ValueError(
                f"the authority's rows are not in ascending key order: {current!r} follows "
                f"{highest!r}. Windows are cut at the authority's own keys, so unordered "
                "input compares rows against an interval they are not in and reports "
                "differences that are not there."
            )
        highest = current
    return highest


def _unreadable(records: list[dict], key_from: tuple | None, key_to: tuple | None):
    """The sentinel, told which window died — or `None` when the window was read.

    Returned *alone*, discarding whatever earlier windows found. The walk stops
    at the first unreadable window, so the windows after it were never compared,
    and a list mixing real records with an incomplete walk has a length that
    means neither "this many differences" nor "not established". A consumer
    already reads the sentinel as the second (`parity_gate` reports the table's
    divergence count as `None`), and that reading is only true if nothing is
    stapled to it.
    """
    if not records:
        return None
    first = records[0]
    if first.get("id") != MIRROR_UNAVAILABLE or "detail" not in first:
        return None
    low = "-inf" if key_from is None else repr(key_from)
    high = "+inf" if key_to is None else repr(key_to)
    return [{**first, "detail": f"{first['detail']} (window [{low}, {high}))"}]

def _reported(row_key: tuple) -> Any:
    """What a divergence record shows as the row's identity.

    A single-column key reports its bare value, as every earlier slice's report
    did and as the operator-facing tests assert; a composite one reports the
    tuple. Flattening the composite case to its first column would name two
    different rows the same thing in the one report meant to tell them apart.
    """
    return row_key[0] if len(row_key) == 1 else row_key


def _comparable(_name: str, value: Any) -> Any:
    """The comparison for a table that passed no codec: booleans only.

    SQLite hands back the integers it stores, so a correctly round-tripped
    boolean would otherwise read as a difference on every row. The column name
    is accepted and ignored so this and `ColumnCodec.comparable` are the same
    shape — the caller should not have two ways to compare.
    """
    if isinstance(value, bool):
        return int(value)
    return value
