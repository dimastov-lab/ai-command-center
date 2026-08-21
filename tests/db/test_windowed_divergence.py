"""The windowed reconciliation (VOYN-W0-AICC-SRV-07c).

`mirror_support.divergence` materialises both sides, which is a memory ceiling
nobody chose on the tables the migration carries most of: `run_event`,
`provider_attempt`, `audit_finding` are append-only, so their reconciliation
cost grows without bound while the code that pays it never changes.

`windowed_divergence` walks the key instead. What this suite has to hold down is
that walking it changes *nothing about the answer* — including the one shape a
careless walk loses. Both ends of the walk are open, and the test that proves it
is `test_a_mirror_row_above_every_authority_key_is_still_reported`: a prototype
bounded by the authority's own first and last key passed everything else here
and dropped that row silently, which is the `unexpected` shape — a mirror ahead
of the system of record, the one thing a loop over the authority cannot see.

No PostgreSQL. The comparison is pure, and the fake below answers `list_records`
with the same semantics the SQL does — half-open on the key, byte order, which
is what SQLite's `BINARY` and PostgreSQL's `COLLATE "C"` both mean. The
statement itself is checked separately, against the real mirror through a
recording cursor, because a fake agreeing with a fake proves nothing about the
`WHERE` clause that has to agree with the authority.
"""

from __future__ import annotations

import inspect

import pytest

from command_center.db import mirror_support
from command_center.db.mirror_support import (
    MIRROR_UNAVAILABLE,
    WINDOW_ROWS,
    ColumnCodec,
    divergence,
    windowed_divergence,
)
from command_center.db.table_mirror import MirroredTable, divergence_against

COLUMNS = ("id", "title", "done")
CODEC = ColumnCodec(flags=frozenset({"done"}))
SPEC = MirroredTable(table="widget", columns=COLUMNS, codec=CODEC)


def row(identifier: str, title: str = "t", done: int = 0) -> dict:
    return {"id": identifier, "title": title, "done": done}


class FakeMirror:
    """A mirror answering `list_records` the way the SQL one does.

    Half-open on the key and in byte order: Python compares `str` by code point,
    which over UTF-8 is byte order, which is what both sides of the real
    comparison are asked for. Records every window it was asked for and how many
    rows each one returned, so a test can state what was *not* held in memory
    rather than only what was reported.
    """

    def __init__(self, rows: list[dict], key: tuple[str, ...] = ("id",)) -> None:
        self._rows = list(rows)
        self._key = key
        self.windows: list[tuple] = []
        self.returned: list[int] = []

    def _identity(self, record: dict) -> tuple:
        return tuple(record.get(name) for name in self._key)

    def list_records(self, *, key_from=None, key_to=None) -> list[dict]:
        self.windows.append((key_from, key_to))
        selected = [
            record
            for record in self._rows
            if (key_from is None or self._identity(record) >= tuple(key_from))
            and (key_to is None or self._identity(record) < tuple(key_to))
        ]
        self.returned.append(len(selected))
        return selected


class UnwindowableMirror:
    """The shape `windowed_divergence` must refuse rather than misreport."""

    def list_records(self) -> list[dict]:
        return []


class UnreadableMirror:
    def __init__(self, fail_after: int = 0) -> None:
        self.fail_after = fail_after
        self.calls = 0

    def list_records(self, *, key_from=None, key_to=None) -> list[dict]:  # noqa: ARG002
        self.calls += 1
        if self.calls > self.fail_after:
            raise RuntimeError("connection closed")
        return []


def whole(authority: list[dict], mirror_rows: list[dict], key=("id",)) -> list[dict]:
    """The unwindowed answer, which is the only thing a window may report."""
    return divergence(authority, FakeMirror(mirror_rows, key), COLUMNS, CODEC, key=key)


def windowed(authority, mirror_rows, *, window: int, key=("id",), mirror=None):
    return windowed_divergence(
        authority, mirror or FakeMirror(mirror_rows, key), COLUMNS, CODEC, key=key, window=window
    )


# --- the answer does not change ---------------------------------------------


@pytest.mark.parametrize("window", [1, 2, 3, 7, 1000])
def test_every_window_size_reports_exactly_what_one_comparison_reports(window: int) -> None:
    """The property the whole slice rests on, stated over all four shapes.

    Agreement, a field difference, a row the mirror never received, and a row
    the mirror holds that the authority never wrote — all present at once, and
    the window size varied from "one row at a time" to "larger than the table".
    A walk that reported the same total while attributing it to different rows
    would pass a count and fail this.
    """
    authority = [row(f"id-{n:02d}") for n in range(10)]
    mirrored = [dict(record) for record in authority]
    mirrored[3]["title"] = "drifted"
    del mirrored[6]
    mirrored.append(row("id-99", "ghost"))
    mirrored.sort(key=lambda record: record["id"])

    assert windowed(authority, mirrored, window=window) == whole(authority, mirrored)


@pytest.mark.parametrize("window", [1, 2, 5])
def test_an_agreeing_table_reports_nothing_however_it_is_cut(window: int) -> None:
    """A window boundary falls *on* a row's key, so an off-by-one at the seam
    reports that row as both missing and unexpected. Clean has to stay clean at
    every cut, which is what varying the window over an exact multiple, a
    remainder and a single row buys."""
    authority = [row(f"id-{n:02d}", done=n % 2) for n in range(6)]

    assert windowed(authority, [dict(record) for record in authority], window=window) == []


def test_the_boolean_codec_still_decides_agreement_inside_a_window() -> None:
    """The window changes what is read, never how two rows are compared. SQLite
    stores `1` where PostgreSQL stores `True`, and a windowed walk that lost the
    codec would call every flagged row different — the permanently-red gate this
    migration keeps almost building."""
    authority = [row(f"id-{n}", done=1) for n in range(5)]
    mirrored = [row(f"id-{n}", done=True) for n in range(5)]

    assert windowed(authority, mirrored, window=2) == []


# --- the open ends ----------------------------------------------------------


def test_a_mirror_row_above_every_authority_key_is_still_reported() -> None:
    """The ghost, and the reason the last window is unbounded above.

    A prototype cut the walk at the authority's own first and last key. Every
    other test here passed; this row — present in the mirror, absent from the
    authority, keyed above all of them — fell outside every window and was
    reported by nothing. It is the `unexpected` shape: a mirror *ahead* of the
    system of record, which no amount of dual-write lag explains and which a
    loop over the authority alone cannot see.
    """
    authority = [row("id-1"), row("id-2")]
    mirrored = [row("id-1"), row("id-2"), row("zzz-ghost")]

    reported = windowed(authority, mirrored, window=1)

    assert [(record["id"], record["fields"]) for record in reported] == [("zzz-ghost", ["*"])]
    assert reported[0]["authority"] is None
    assert reported == whole(authority, mirrored)


def test_a_mirror_row_below_every_authority_key_is_still_reported() -> None:
    """The same argument at the other end: the first window is unbounded below.

    Bounding it at the authority's smallest key is the more tempting mistake of
    the two, because that bound reads like the obvious one — the walk starts
    where the rows start.
    """
    authority = [row("id-5"), row("id-6")]
    mirrored = [row("aaa-ghost"), row("id-5"), row("id-6")]

    reported = windowed(authority, mirrored, window=1)

    assert [record["id"] for record in reported] == ["aaa-ghost"]
    assert reported == whole(authority, mirrored)


def test_an_empty_authority_still_compares_the_whole_mirror() -> None:
    """The rule at its limit: no rows means no boundaries, and a walk that
    derived its windows from the authority's keys would take no window at all —
    reporting a clean table for a mirror full of rows nobody wrote. One window
    over everything is the same open-ended rule, not a special case."""
    mirrored = [row("id-1"), row("id-2")]

    reported = windowed([], mirrored, window=5)

    assert sorted(record["id"] for record in reported) == ["id-1", "id-2"]
    assert reported == whole([], mirrored)


def test_the_windows_partition_the_key_space_with_open_ends() -> None:
    """Stated structurally, not just by its consequences.

    The bounds handed to the mirror must start at `None`, end at `None`, and
    join end-to-end in between — that is what makes their union the whole key
    space with no row in two of them. A gap here is a silently unchecked row; an
    overlap is a row reported twice.
    """
    authority = [row(f"id-{n}") for n in range(6)]
    mirror = FakeMirror([dict(record) for record in authority])

    windowed(authority, [], window=2, mirror=mirror)

    assert mirror.windows[0][0] is None
    assert mirror.windows[-1][1] is None
    assert [upper for _lower, upper in mirror.windows[:-1]] == [
        lower for lower, _upper in mirror.windows[1:]
    ]


# --- what is not held in memory ---------------------------------------------


def test_neither_side_is_ever_materialised_whole() -> None:
    """The point of the slice, asserted rather than described.

    The authority is a generator that records how far it has been drawn, so the
    check is on residency rather than on the report. Two bounds, one per side:
    the walk never runs more than two windows ahead of the comparison it is
    doing — one batch being compared, one already read for its boundary — and no
    single mirror read returns more rows than one window holds. Measured end to
    end at 200 000 rows this is 294.9 MB against 28.6 MB, 10.3x, and these two
    bounds are what make it true.

    A whole-materialising implementation fails the first assertion outright: it
    draws every authority row before the first mirror read.
    """
    total, window = 60, 5
    drawn = {"rows": 0}

    def authority():
        for n in range(total):
            drawn["rows"] += 1
            yield row(f"id-{n:03d}")

    mirror = FakeMirror([row(f"id-{n:03d}") for n in range(total)])
    resident: list[int] = []

    original = mirror.list_records

    def watched(**bounds):
        resident.append(drawn["rows"])
        return original(**bounds)

    mirror.list_records = watched  # type: ignore[method-assign]

    assert windowed_divergence(authority(), mirror, COLUMNS, CODEC, window=window) == []
    assert resident[0] <= 2 * window
    assert all(drawn_by <= (index + 2) * window for index, drawn_by in enumerate(resident))
    assert max(mirror.returned) <= window
    assert len(mirror.windows) == total // window


def test_the_authority_may_be_an_iterator_rather_than_a_list() -> None:
    """A caller streaming rows off a cursor is the case the memory number was
    measured on; a caller handing over a list gets the mirror side's saving
    only. Both have to work, and only one of them can be tested by passing a
    list."""
    authority = [row(f"id-{n}") for n in range(4)]

    assert windowed(iter(authority), [dict(r) for r in authority], window=2) == []


# --- composite keys ---------------------------------------------------------


def test_a_composite_key_is_walked_as_one_interval() -> None:
    """`provider_attempt` is keyed by `(run_id, attempt_number)`, and its window
    is an interval along that pair rather than a box around each column. Row 3
    of run `r-1` and row 1 of run `r-2` are adjacent in the walk; a per-column
    bound would exclude one of them from a window that contains the other."""
    key = ("run_id", "attempt_number")
    columns = ("run_id", "attempt_number", "outcome")
    authority = [
        {"run_id": "r-1", "attempt_number": 1, "outcome": "ok"},
        {"run_id": "r-1", "attempt_number": 3, "outcome": "ok"},
        {"run_id": "r-2", "attempt_number": 1, "outcome": "ok"},
        {"run_id": "r-2", "attempt_number": 2, "outcome": "ok"},
    ]
    mirrored = [dict(record) for record in authority]
    mirrored[2]["outcome"] = "drifted"
    mirrored.append({"run_id": "r-9", "attempt_number": 1, "outcome": "ghost"})

    reported = windowed_divergence(
        authority, FakeMirror(mirrored, key), columns, key=key, window=1
    )

    assert [record["id"] for record in reported] == [("r-2", 1), ("r-9", 1)]
    assert reported == divergence(authority, FakeMirror(mirrored, key), columns, key=key)


# --- refusals ---------------------------------------------------------------


def test_an_authority_out_of_key_order_is_refused_rather_than_misreported() -> None:
    """Windows are cut at the authority's own keys, so unordered input compares
    rows against an interval they are not in. That fails safely — the intervals
    still cover the key space, so rows are reported twice rather than skipped —
    but "differences that are not there" is a gate nobody can pass, and the
    caller error should not arrive dressed as a finding about the data."""
    authority = [row("id-3"), row("id-1")]

    with pytest.raises(ValueError, match="ascending key order"):
        windowed(authority, [], window=1)


def test_a_repeated_key_is_refused_too() -> None:
    """A duplicate key means the column being walked is not the unique thing it
    was declared to be. `divergence` pairs rows through a dict, so the second
    row would silently replace the first and the comparison would report
    agreement about a row it never looked at."""
    authority = [row("id-1"), row("id-1", "again")]

    with pytest.raises(ValueError, match="ascending key order"):
        windowed(authority, [], window=5)


def test_keys_of_mixed_types_are_named_rather_than_crashing_on_a_comparison() -> None:
    """SQLite is dynamically typed, so a `TEXT` key column can hold an integer.
    An interval has no meaning across the two, and the raw `TypeError` from
    comparing them names neither the column nor the table."""
    authority = [row("id-1"), {"id": 7, "title": "t", "done": 0}]

    with pytest.raises(ValueError, match="not mutually comparable"):
        windowed(authority, [], window=1)


def test_a_mirror_that_takes_no_bounds_is_refused_before_anything_is_read() -> None:
    """Otherwise the `TypeError` is raised inside `divergence`, which catches
    everything and reports `MIRROR_UNAVAILABLE` — a FAIL claiming the store is
    unreachable when what is wrong is the call, and a cutover total declared
    "not established" on the strength of a typo."""
    with pytest.raises(TypeError, match="key_from"):
        windowed_divergence([row("id-1")], UnwindowableMirror(), COLUMNS, CODEC)


@pytest.mark.parametrize("size", [0, -1])
def test_a_window_must_hold_at_least_one_row(size: int) -> None:
    """`window=0` would loop forever drawing empty batches; a negative one is
    the same mistake with a sign."""
    with pytest.raises(ValueError, match="at least one row"):
        windowed([row("id-1")], [], window=size)


# --- an unreadable mirror ---------------------------------------------------


@pytest.mark.parametrize("fail_after", [0, 2])
def test_the_walk_stops_at_the_first_window_it_cannot_read(fail_after: int) -> None:
    """The sentinel is reported alone, and no later window is attempted.

    Nothing after the failure was compared, so a list mixing real records with
    an incomplete walk has a length that means neither "this many differences"
    nor "not established". `parity_gate` reads this sentinel as the second and
    reports the table's count as `None`; that reading is only true if nothing is
    stapled to it. Failing on the first window and failing part-way through are
    both exercised, because only the second can leave records behind.
    """
    authority = [row(f"id-{n}") for n in range(10)]
    mirror = UnreadableMirror(fail_after=fail_after)

    reported = windowed(authority, [], window=1, mirror=mirror)

    assert len(reported) == 1
    assert reported[0]["id"] == MIRROR_UNAVAILABLE
    assert "RuntimeError: connection closed" in reported[0]["detail"]
    assert mirror.calls == fail_after + 1


def test_the_sentinel_names_the_window_that_could_not_be_read() -> None:
    """An operator's next question is where it died, and the interval is the
    only thing that answers it — the window number would be meaningless without
    knowing the batch size the run used."""
    authority = [row("id-1"), row("id-2"), row("id-3")]

    reported = windowed(authority, [], window=1, mirror=UnreadableMirror(fail_after=1))

    assert "window [('id-2',), ('id-3',))" in reported[0]["detail"]


def test_the_first_window_is_named_as_unbounded_below() -> None:
    reported = windowed([row("id-1")], [], window=1, mirror=UnreadableMirror())

    assert "window [-inf, +inf)" in reported[0]["detail"]


# --- the seam every table already speaks ------------------------------------


def test_divergence_against_routes_a_window_to_the_windowed_comparison() -> None:
    """The declaration's own reconciliation gains `window=`, so a caller opts in
    per table in the vocabulary it already uses rather than reaching past it."""
    authority = [row(f"id-{n}") for n in range(6)]
    mirrored = [dict(record) for record in authority]
    mirrored[1]["title"] = "drifted"
    mirror = FakeMirror(mirrored)

    reported = divergence_against(SPEC)(authority, mirror, window=2)

    assert [record["id"] for record in reported] == ["id-1"]
    assert len(mirror.windows) == 3


def test_divergence_against_still_compares_whole_by_default() -> None:
    """Off unless asked for. Windowing needs a mirror whose `list_records` takes
    bounds and an authority already in key order — the queue's adapter has
    neither — so the default cannot be the one that assumes both."""
    mirror = UnwindowableMirror()

    assert divergence_against(SPEC)([], mirror) == []


def test_the_default_window_is_the_measured_one() -> None:
    """Three docstrings quote 28.6 MB against 294.9 MB "at `window=5000`". A
    default that drifted from the number the measurement was taken at would
    leave those figures describing a run nobody performs, which is the quiet way
    a measured claim turns into a remembered one."""
    assert WINDOW_ROWS == 5000
    assert inspect.signature(mirror_support.windowed_divergence).parameters[
        "window"
    ].default == WINDOW_ROWS


# --- the statement the window becomes ----------------------------------------
#
# The fake above answers a window the way the SQL is *supposed* to, so on its
# own it proves the walk and not the clause. These check the clause itself,
# against the real mirrors, through a cursor that records instead of executing.


_NO_PARAMS = object()


class RecordingConnection:
    """Enough of psycopg's shape to capture a statement, and nothing more.

    The same object serves as connection and cursor because the mirror asks for
    both with `with`, and a second class would only restate that. `execute` keeps
    a sentinel for "called with no parameter sequence at all", which is a
    distinction the unbounded read depends on and `()` would erase.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def __enter__(self) -> RecordingConnection:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def cursor(self) -> RecordingConnection:
        return self

    def execute(self, statement: str, params: object = _NO_PARAMS) -> None:
        self.calls.append((statement, params))

    def fetchall(self) -> list:
        return []

    @property
    def statement(self) -> str:
        return self.calls[-1][0]

    @property
    def params(self) -> object:
        return self.calls[-1][1]


def recorded(mirror_class, **bounds) -> RecordingConnection:
    connection = RecordingConnection()
    mirror_class(connection_factory=lambda: connection).list_records(**bounds)
    return connection


def test_a_text_key_window_is_half_open_and_compared_under_the_c_collation() -> None:
    """`>=` on one side, `<` on the other — consecutive windows share a boundary
    value, and that is what makes their union the whole key space rather than a
    cover with a hole at every seam.

    `COLLATE "C"` is byte order, which is what SQLite's `BINARY` means. Left to
    the cluster's default, `Task-1` and `task_1` would be cut apart in an order
    the authority does not share, and a boundary taken from the authority would
    land between different rows on the two sides.
    """
    from command_center.db.owner_item_store import PostgresOwnerItemMirror

    connection = recorded(PostgresOwnerItemMirror, key_from=("a-1",), key_to=("z-9",))

    assert 'WHERE id COLLATE "C" >= %s AND id COLLATE "C" < %s' in connection.statement
    assert connection.params == ("a-1", "z-9")
    assert connection.statement.endswith("ORDER BY id")


def test_an_unbounded_read_emits_the_statement_it_always_has() -> None:
    """No `WHERE`, and no parameter sequence at all — psycopg switches to the
    extended protocol the moment one is passed, and every store's suite already
    exercises this call. The window is an addition, not a rewrite."""
    from command_center.db.owner_item_store import PostgresOwnerItemMirror

    connection = recorded(PostgresOwnerItemMirror)

    assert "WHERE" not in connection.statement
    assert connection.params is _NO_PARAMS


def test_one_bound_emits_one_term() -> None:
    """The first window has no lower bound and the last no upper one, so the
    one-sided clause is not an edge case here — it is two of every walk."""
    from command_center.db.owner_item_store import PostgresOwnerItemMirror

    lower = recorded(PostgresOwnerItemMirror, key_from=("a-1",))
    upper = recorded(PostgresOwnerItemMirror, key_to=("z-9",))

    assert 'WHERE id COLLATE "C" >= %s ORDER BY' in lower.statement
    assert lower.params == ("a-1",)
    assert 'WHERE id COLLATE "C" < %s ORDER BY' in upper.statement
    assert upper.params == ("z-9",)


def test_an_integer_key_is_not_collated() -> None:
    """`COLLATE` on a `bigint` is an error rather than a no-op, so the seven
    identity tables would fail on their first window if the collation were
    applied by column name instead of by the bound's own type."""
    from command_center.db.run_children_store import PostgresRunEventMirror

    connection = recorded(PostgresRunEventMirror, key_from=(41,), key_to=(99,))

    assert "COLLATE" not in connection.statement
    assert "WHERE id >= %s AND id < %s" in connection.statement
    assert connection.params == (41, 99)


def test_a_composite_key_is_compared_row_wise_and_collated_per_column() -> None:
    """`provider_attempt` is keyed by `(run_id, attempt_number)` — text and
    integer together, which is both shapes in one statement. Row-wise, because
    `run_id >= %s AND attempt_number >= %s` is a box around the key rather than
    an interval along it, and a box excludes rows a window must contain."""
    from command_center.db.provenance_store import PostgresProviderAttemptMirror

    connection = recorded(
        PostgresProviderAttemptMirror, key_from=("r-1", 3), key_to=("r-2", 2)
    )

    assert (
        'WHERE (run_id COLLATE "C", attempt_number) >= (%s, %s) '
        'AND (run_id COLLATE "C", attempt_number) < (%s, %s)'
    ) in connection.statement
    assert connection.params == ("r-1", 3, "r-2", 2)


def test_a_single_column_key_also_accepts_a_bare_value() -> None:
    """The walk always has the key as a tuple; an operator at a REPL has the
    value. Both are the same bound, and refusing one of them would make the
    windowed read something only the walk can call."""
    from command_center.db.owner_item_store import PostgresOwnerItemMirror

    assert recorded(PostgresOwnerItemMirror, key_from="a-1").params == ("a-1",)


def test_a_bound_of_the_wrong_arity_is_refused_before_the_database_sees_it() -> None:
    """Otherwise it arrives as a driver error about a statement, which names
    neither the bound nor the key it was meant for."""
    from command_center.db.provenance_store import PostgresProviderAttemptMirror

    with pytest.raises(ValueError, match="needs 2 value"):
        recorded(PostgresProviderAttemptMirror, key_from=("r-1",))


# --- against a real server ---------------------------------------------------
#
# Skipped without `AICC_TEST_PG_ADMIN_DSN`; CI supplies one. What only a server
# can answer is whether the clause above is *valid* — a row constructor with a
# collation on one member is the part no fake can vouch for — and whether
# `COLLATE "C"` really orders the way the authority does.


def test_a_window_returns_exactly_the_interval_on_a_real_server(pg_connection_factory) -> None:
    """Byte order, on rows chosen so that a cluster with a natural-language
    default collation would disagree: `A-2` precedes `a-1` by byte and follows
    it in `en_US.UTF-8`. On a C-collated cluster the two orders coincide and this
    proves less — which is the reason the collation is named in the statement
    rather than inherited from whatever the cluster happens to be.
    """
    from command_center.db.owner_item_store import PostgresOwnerItemMirror
    from tests.db.test_mirror_contract import sample_row

    mirror = PostgresOwnerItemMirror(connection_factory=pg_connection_factory)
    for identifier in ("A-2", "a-1", "b-3"):
        mirror.upsert(sample_row(mirror.spec, identifier))

    assert [record["id"] for record in mirror.list_records()] == ["A-2", "a-1", "b-3"]
    assert [record["id"] for record in mirror.list_records(key_to=("a-1",))] == ["A-2"]
    assert [record["id"] for record in mirror.list_records(key_from=("a-1",))] == ["a-1", "b-3"]
    assert [
        record["id"]
        for record in mirror.list_records(key_from=("a-1",), key_to=("b-3",))
    ] == ["a-1"]


def test_a_composite_window_is_an_interval_on_a_real_server(pg_connection_factory) -> None:
    """The statement the fake cannot vouch for: a row constructor whose first
    member carries a collation and whose second is an integer.

    `("r-1", 3)` to `("r-2", 2)` spans two runs, and the rows it must contain are
    the last attempt of one and the first of the next. The `run_id >= 'r-1' AND
    attempt_number >= 3 AND run_id < 'r-2' AND attempt_number < 2` box that a
    per-column reading produces contains neither of them.
    """
    from command_center.db import mirror_registry
    from command_center.db.provenance_store import PostgresProviderAttemptMirror
    from tests.db.test_mirror_contract import _ensure_parents, sample_row

    run_mirror = mirror_registry.mirror_classes()["run"]
    _ensure_parents(run_mirror.spec, pg_connection_factory)
    for run_id in ("r-1", "r-2"):
        run_mirror(connection_factory=pg_connection_factory).upsert(
            sample_row(run_mirror.spec, run_id)
        )

    mirror = PostgresProviderAttemptMirror(connection_factory=pg_connection_factory)
    attempts = [("r-1", 1), ("r-1", 3), ("r-2", 1), ("r-2", 2)]
    for run_id, attempt in attempts:
        record = sample_row(mirror.spec)
        record["run_id"], record["attempt_number"] = run_id, attempt
        mirror.upsert(record)

    windowed_rows = mirror.list_records(key_from=("r-1", 3), key_to=("r-2", 2))

    assert [(r["run_id"], r["attempt_number"]) for r in windowed_rows] == [
        ("r-1", 3),
        ("r-2", 1),
    ]


def test_the_windowed_walk_agrees_with_the_whole_comparison_on_a_real_server(
    pg_connection_factory,
) -> None:
    """End to end on the seam the cutover actually reads through, over all four
    shapes at once — including the ghost keyed above every authority row, which
    is the shape a bounded walk loses and the only one that needs a real
    `key_to=None` to be reached."""
    from command_center.db.owner_item_store import PostgresOwnerItemMirror
    from tests.db.test_mirror_contract import sample_row

    mirror = PostgresOwnerItemMirror(connection_factory=pg_connection_factory)
    authority = [sample_row(mirror.spec, f"id-{n:02d}") for n in range(8)]
    for record in authority:
        mirror.upsert(record)
    drifted = dict(authority[2])
    drifted["title"] = "drifted"
    mirror.upsert(drifted)
    mirror.upsert(sample_row(mirror.spec, "zzz-ghost"))
    absent = authority.pop(5)
    mirror.delete_where("id", absent["id"])
    authority.insert(5, sample_row(mirror.spec, "id-05"))

    reconcile = divergence_against(mirror.spec)

    assert reconcile(authority, mirror, window=2) == reconcile(authority, mirror)
    assert sorted(
        record["id"] for record in reconcile(authority, mirror, window=2)
    ) == ["id-02", "id-05", "zzz-ghost"]
