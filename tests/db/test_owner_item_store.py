"""Slice 2 of the runtime migration: `owner_item`'s PostgreSQL mirror.

Slice 2 is the harder shape. `queue_entry`'s authority was already JSON, so
slice 1 added a mirror beside an existing one and moved nothing. Here SQLite
*is* the authority, so the claim under test is that a dual-write leaves it that
way — and that the reconciliation which will eventually gate the cutover can
actually compare the two stores.

**What this module was missing until VOYN-W0-AICC-OWNER-ITEM-MIRROR-UNCOVERED.**
Every other mirrored family acquired a *staged* reconciliation — the real
writer driven end to end, with `divergence` run after each write — and this
table, from the second slice, never got one. The checks below `mirror.upsert`
by hand and then reconcile, which proves the conversion and proves nothing
about the hook. Measured rather than argued: deleting `_mirror_owner_item` from
`set_owner_item_done` left the whole `tests/db` run on its exact baseline, all
104 `owner_item` tests included, and the contract's static "every declared
mirror has a reachable caller" gate stayed green because `create_owner_item`
still calls it. A table with one write path could not hide that; a table with
two can, and this one has two.

The staged reconciliation added at the end of this file closes it, and the
deletion is *demonstrated* rather than asserted to be impossible: the suite
recompiles `wave1` with the call removed from each write path in turn and
requires the reconciliation to report the loss.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import ModuleType
from typing import Callable

import pytest

from command_center import record_mirror
from command_center.db.owner_item_store import (
    MIRROR_UNAVAILABLE,
    OWNER_ITEM_COLUMNS,
    PostgresOwnerItemMirror,
    divergence,
)
from command_center.runtime.db import wave1
from tests.db.mirror_probe import each_lost_write_is_noticed

ROOT = Path(__file__).resolve().parents[2]


def _row(item_id: str, **overrides: object) -> dict:
    row = {
        "id": item_id,
        "title": f"item {item_id}",
        "detail": None,
        "due": None,
        "done": 0,
        "source_ref": None,
        "version": 0,
        "created_at": "2026-08-13T00:00:00",  # naive local, exactly what `models.iso_now()` emits
        "updated_at": "2026-08-13T00:00:00",
        "project_ref": None,
    }
    row.update(overrides)  # type: ignore[arg-type]
    return row


@pytest.fixture
def mirror(pg_connection_factory) -> PostgresOwnerItemMirror:
    return PostgresOwnerItemMirror(connection_factory=pg_connection_factory)


# --- contract and authority -------------------------------------------------


def test_the_mirror_satisfies_the_row_oriented_contract() -> None:
    assert isinstance(
        PostgresOwnerItemMirror(connection_factory=lambda: None), record_mirror.RecordMirror
    )
    assert PostgresOwnerItemMirror.name == "postgres"


def test_sqlite_remains_the_authority_for_owner_items() -> None:
    """The dangerous outcome of this slice is a second system of record.

    `create_owner_item` and `set_owner_item_done` must still write SQLite, and
    must not read PostgreSQL to decide anything. Read paths are switched only
    after reconciliation and the rollback and backup/restore drills — not as a
    side effect of a mirror landing.
    """
    for function in (wave1.create_owner_item, wave1.get_owner_item, wave1.list_owner_items):
        source = inspect.getsource(function)
        for marker in ("postgres", "owner_item_store", "list_records"):
            assert marker not in source.lower(), f"{function.__name__}: {marker}"

    assert "INSERT INTO owner_item" in inspect.getsource(wave1.create_owner_item)


def test_the_column_list_matches_the_accepted_postgresql_schema() -> None:
    """The map is the contract; drifting from it silently is how a mirror ends
    up writing a column the target does not have."""
    ddl = (ROOT / "command_center/db/sql/0001_initial.up.sql").read_text(encoding="utf-8")
    body = ddl.split("CREATE TABLE owner_item (", 1)[1].split(");", 1)[0]
    declared = tuple(
        line.strip().split()[0]
        for line in body.strip().splitlines()
        if line.strip() and not line.strip().startswith("--")
    )
    assert declared == OWNER_ITEM_COLUMNS


# --- behaviour against a real PostgreSQL ------------------------------------


def test_the_boolean_and_timestamp_gaps_round_trip(mirror: PostgresOwnerItemMirror) -> None:
    """`done` is INTEGER here and boolean there; timestamps are TEXT and
    timestamptz. Unconverted, every row reads as different and the cutover gate
    is permanently red — which invites loosening the comparison."""
    mirror.upsert(_row("a", done=1))

    stored = mirror.list_records()[0]

    assert stored["done"] == 1 and isinstance(stored["done"], int)
    assert stored["created_at"] == "2026-08-13T00:00:00"
    assert isinstance(stored["created_at"], str)


def test_upsert_is_idempotent_and_updates_in_place(mirror: PostgresOwnerItemMirror) -> None:
    # The backfill runs more than once by design; an insert-only mirror would
    # fail the second run on rows it wrote itself.
    row = _row("a")
    mirror.upsert(row)
    mirror.upsert(row)
    assert len(mirror.list_records()) == 1

    mirror.upsert(_row("a", done=1, version=1, title="renamed"))

    stored = mirror.list_records()
    assert len(stored) == 1
    assert (stored[0]["done"], stored[0]["version"], stored[0]["title"]) == (1, 1, "renamed")


def test_reconciliation_reports_agreement_and_every_shape_of_disagreement(
    mirror: PostgresOwnerItemMirror,
) -> None:
    agreed = _row("same")
    mirror.upsert(agreed)
    assert divergence([agreed], mirror) == []

    # Field-level difference.
    mirror.upsert(_row("same", title="drifted"))
    reported = divergence([agreed], mirror)
    assert [entry["fields"] for entry in reported] == [["title"]]

    # Missing from the mirror.
    missing = divergence([agreed, _row("absent")], mirror)
    assert {entry["id"] for entry in missing} >= {"absent"}

    # Present in the mirror but not in the authority: a mirror ahead of the
    # system of record is the state no check would flag if reconciliation only
    # walked the authority.
    ahead = divergence([], mirror)
    assert {entry["id"] for entry in ahead} == {"same"}


def test_an_unreadable_mirror_is_reported_not_treated_as_agreement() -> None:
    """The cutover is gated on a session with no divergence. An absent store
    has nothing to disagree with, so returning `[]` here would let the
    migration advance on the strength of a store nobody wrote."""

    class Broken:
        name = "postgres"

        def list_records(self) -> list[dict]:
            raise RuntimeError("connection refused")

    reported = divergence([_row("a")], Broken())

    assert [entry["id"] for entry in reported] == [MIRROR_UNAVAILABLE]
    assert "RuntimeError" in reported[0]["detail"]


def test_importing_the_store_needs_no_postgresql_client() -> None:
    import subprocess
    import sys

    probe = (
        "import sys;"
        "import command_center.db.owner_item_store as s;"
        "assert 'aios_db' not in sys.modules;"
        "assert 'psycopg' not in sys.modules;"
        "s.PostgresOwnerItemMirror()"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, cwd=ROOT, check=False
    )
    assert result.returncode == 0, result.stderr


def test_a_mirror_failure_cannot_break_the_authoritative_write(tmp_path, monkeypatch) -> None:
    """During dual-write the mirror is not load-bearing.

    Letting it raise would mean a migration step could take down the very table
    it is migrating. The mirror's health is reported by `divergence`, not by
    exceptions on the write path.
    """
    from command_center.db import owner_item_store

    class Exploding:
        def upsert(self, record: dict) -> None:
            raise RuntimeError("postgres is down")

    monkeypatch.setattr(owner_item_store, "PostgresOwnerItemMirror", lambda: Exploding())

    db_path = tmp_path / "runtime.db"
    wave1.db.migrate(db_path)

    created = wave1.create_owner_item(db_path, title="survives")

    assert wave1.get_owner_item(db_path, created["id"])["title"] == "survives"


def test_the_authoritative_write_happens_before_the_mirror(tmp_path, monkeypatch) -> None:
    """Order is not cosmetic. A mirror written first can end up ahead of the
    system of record, which no reconciliation flags as wrong — whereas a stale
    mirror is exactly what `divergence` exists to surface."""
    from command_center.db import owner_item_store

    observed: list[str] = []
    db_path = tmp_path / "runtime.db"
    wave1.db.migrate(db_path)

    class Recording:
        def upsert(self, record: dict) -> None:
            # Recorded, not asserted. `_mirror_owner_item` swallows every
            # `Exception`, and `AssertionError` is one — an assertion in here
            # is caught, logged at DEBUG and lost, so the test would pass
            # whatever it claimed. Independent review proved that by inverting
            # this condition and watching the test still pass. The observation
            # has to escape the swallowing frame to mean anything.
            observed.append(wave1.get_owner_item(db_path, record["id"]) is not None)

    monkeypatch.setattr(owner_item_store, "PostgresOwnerItemMirror", lambda: Recording())

    wave1.create_owner_item(db_path, title="ordered")

    assert observed == [True], "the authoritative row must be readable before the mirror runs"


def test_a_real_iso_now_timestamp_round_trips(mirror: PostgresOwnerItemMirror) -> None:
    """The test that would have caught the blocker: use what the app writes.

    `models.iso_now()` returns *naive local time* — no offset, second
    precision. The first version of this mirror rendered UTC with a `Z` suffix
    and the tests fabricated `Z`-suffixed inputs to match, so the conversion was
    proved against data no writer in this application emits. Against real
    output, `divergence` would have reported every row as different — the
    permanently-red gate the module docstring warns about.
    """
    from command_center import models

    written = models.iso_now()
    assert "+" not in written and not written.endswith("Z")  # guard the premise

    mirror.upsert(_row("real", created_at=written, updated_at=written))

    stored = mirror.list_records()[0]
    assert stored["created_at"] == written
    assert stored["updated_at"] == written


def test_reconciliation_is_clean_for_a_row_the_application_actually_wrote(
    mirror: PostgresOwnerItemMirror, tmp_path
) -> None:
    """End to end against the authority, not against a fixture's idea of it.

    A row created through `wave1.create_owner_item` and mirrored must reconcile
    with zero divergence. This is the assertion the cutover is gated on, so it
    has to run on a row the real writer produced.
    """
    db_path = tmp_path / "runtime.db"
    wave1.db.migrate(db_path)
    created = wave1.create_owner_item(db_path, title="reconciles", detail="d")

    mirror.upsert(created)

    assert divergence([created], mirror) == []


def test_a_naive_timestamp_is_stored_as_the_instant_the_writer_meant(
    mirror: PostgresOwnerItemMirror, pg_connection_factory
) -> None:
    """Naive text handed to `timestamptz` is stamped with the *session* zone.

    That is silent: no error, every row shifted by the gap between the writing
    machine and the server. The zone is attached on the way in instead, so the
    stored instant is the one the writer meant regardless of the server's
    `TimeZone`.
    """
    from datetime import datetime

    written = "2026-08-13T12:00:00"
    expected = datetime.fromisoformat(written).astimezone()

    mirror.upsert(_row("tz", created_at=written, updated_at=written))

    with pg_connection_factory() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT created_at FROM owner_item WHERE id = 'tz'")
            stored = cur.fetchone()[0]
    assert stored == expected


# --- the staged family reconciliation ---------------------------------------
#
# Everything above this line either drives the mirror directly or watches the
# hook with a recording double. Neither shape can see a *missing* hook, which
# is what the checks below exist for.


#: Every function that writes an `owner_item` row and is expected to mirror it.
#: Read off the module rather than listed by hand — see the pin below, which
#: fails when a third write path appears without a stage in `_lifecycle`.
WRITE_PATHS: tuple[str, ...] = ("create_owner_item", "set_owner_item_done")

#: The first stage at which each path's loss becomes visible. Named per path
#: because "some stage failed" would also pass if the reconciliation happened
#: to be reporting an unrelated row.
FIRST_STAGE_THAT_NOTICES: dict[str, str] = {
    "create_owner_item": "item created",
    "set_owner_item_done": "item marked done",
}


def _patch(monkeypatch, factory) -> None:
    """Point the dual-write hook at this test's database.

    `_mirror_owner_item` imports the class from `command_center.db.
    owner_item_store` on every call, so replacing the attribute is what makes
    the real write path reach the real target — and the lazy import is why it
    has to be replaced there rather than on `wave1`.
    """
    from command_center.db import owner_item_store

    monkeypatch.setattr(
        owner_item_store,
        "PostgresOwnerItemMirror",
        lambda: PostgresOwnerItemMirror(connection_factory=factory),
    )


def _lifecycle(module: ModuleType, db_path: Path, reconciled: Callable[[str], None]) -> None:
    """Both write paths, each one in front of a reconciliation.

    The second item is created and never toggled, and that is the point rather
    than padding: `set_owner_item_done` upserts the **whole row**, so an item
    that both paths touch has its lost `create` write repaired by the next
    `done` write. A reconciliation run only at the end of the scenario would
    report agreement about a write that was genuinely lost — measured below in
    `test_a_lost_write_a_later_path_repairs_is_invisible_at_the_end`. An item
    only one path ever touches has nothing to repair it.

    Takes the module as an argument so the deletion demonstration can drive a
    recompiled `wave1` through exactly the stages the healthy one runs.
    """
    created = module.create_owner_item(db_path, title="mirror me", detail="d")
    reconciled("item created")

    module.create_owner_item(db_path, title="never toggled")
    reconciled("second item created")

    done = module.set_owner_item_done(
        db_path, created["id"], expected_version=created["version"], done=True
    )
    reconciled("item marked done")

    module.set_owner_item_done(
        db_path, done["id"], expected_version=done["version"], done=False
    )
    reconciled("item marked undone")


def test_every_write_path_is_covered_by_a_stage() -> None:
    """The pin that keeps this file honest when a third write path lands.

    `WRITE_PATHS` is what the demonstration below iterates over, so a path
    missing from it is a path whose mirror call nobody proves is required —
    which is the whole defect this module is closing. Checked against the
    module's own call sites rather than against a list somebody remembered to
    update.
    """
    tree = ast.parse(inspect.getsource(wave1))
    writers = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Name)
            and inner.func.id == "_mirror_owner_item"
            for inner in ast.walk(node)
        )
    }
    assert writers == set(WRITE_PATHS), (
        f"`wave1` mirrors `owner_item` from {sorted(writers)}, and this file stages "
        f"{sorted(WRITE_PATHS)}. Add the new path to `_lifecycle` and to WRITE_PATHS, "
        "or its mirror call can be deleted with the suite still green."
    )
    assert set(FIRST_STAGE_THAT_NOTICES) == set(WRITE_PATHS)


def test_the_owner_item_family_reconciles_after_every_write(
    pg_connection_factory, tmp_path, monkeypatch
) -> None:
    """The reconciliation the cutover is gated on, driven by the real writer.

    Not `mirror.upsert(created)` — that is the shape the older checks in this
    file use, and it proves the conversion while assuming the call that would
    have performed it.
    """
    _patch(monkeypatch, pg_connection_factory)
    mirror = PostgresOwnerItemMirror(connection_factory=pg_connection_factory)

    db_path = tmp_path / "runtime.db"
    wave1.db.migrate(db_path)

    def reconciled(stage: str) -> None:
        assert divergence(wave1.list_owner_items(db_path), mirror) == [], stage

    _lifecycle(wave1, db_path, reconciled)


# --- the demonstration: the reconciliation shown to fail ---------------------


def _wave1_without_the_mirror_call_in(write_path: str) -> ModuleType:
    """`wave1`, recompiled with the mirror call deleted from one write path.

    The reviewer's edit performed by the suite, rather than an imitation of it:
    the statement is located in the AST, its lines are blanked, and the source
    is compiled and executed as a module of its own. Nothing about the healthy
    `wave1` changes, so the two can be driven through the same `_lifecycle` in
    the same session.

    A monkeypatched `_mirror_owner_item` that skipped based on its caller would
    have been shorter and would have proved something weaker — that a hook can
    be made not to run — where the claim under test is about a line of source
    somebody can delete in a diff.

    Refuses rather than degrades when it cannot find exactly one call to
    delete: a perturbation that silently perturbs nothing turns the test below
    into one that passes for the wrong reason.
    """
    source = inspect.getsource(wave1)
    tree = ast.parse(source)
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == write_path
    ]
    assert len(functions) == 1, f"{write_path}: {len(functions)} definitions"

    calls = [
        node
        for node in ast.walk(functions[0])
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "_mirror_owner_item"
    ]
    assert len(calls) == 1, f"{write_path}: {len(calls)} mirror calls, expected 1"

    lines = source.splitlines(keepends=True)
    for lineno in range(calls[0].lineno, (calls[0].end_lineno or calls[0].lineno) + 1):
        lines[lineno - 1] = "\n"

    module = ModuleType(f"wave1_without_mirror_in_{write_path}")
    module.__file__ = wave1.__file__
    exec(compile("".join(lines), wave1.__file__ or "<wave1>", "exec"), module.__dict__)
    return module


def test_the_perturbation_deletes_the_call_and_nothing_else() -> None:
    """The positive control for the perturbation, before anything relies on it.

    Two ways this could pass while testing nothing: a recompiled module that
    still mirrors, and one so mangled it no longer behaves like `wave1` at all.
    Both are checked here so the reconciliation tests below can be read as
    statements about the missing call.

    Read off `__code__.co_names` rather than `inspect.getsource`, which was the
    first version and was wrong in the direction that matters: `getsource`
    resolves through `linecache` to the file on disk, so it hands back the
    *unperturbed* text for every function in the recompiled module and this
    control fails on a perturbation that worked. The compiled function is the
    only witness to what was actually deleted.
    """
    declared = {
        node.name
        for node in ast.walk(ast.parse(inspect.getsource(wave1)))
        if isinstance(node, ast.FunctionDef)
    }
    for write_path in WRITE_PATHS:
        perturbed = _wave1_without_the_mirror_call_in(write_path)
        assert declared <= set(vars(perturbed)), "the recompiled module lost a function"

        names = getattr(perturbed, write_path).__code__.co_names
        assert "_mirror_owner_item" not in names, write_path
        # ...and only there: the other path still carries its call.
        other = next(name for name in WRITE_PATHS if name != write_path)
        assert "_mirror_owner_item" in getattr(perturbed, other).__code__.co_names, other

        # The healthy module is untouched by any of this.
        assert "_mirror_owner_item" in getattr(wave1, write_path).__code__.co_names


@pytest.mark.parametrize("write_path", WRITE_PATHS)
def test_the_reconciliation_fails_when_a_write_path_stops_mirroring(
    pg_connection_factory, tmp_path, monkeypatch, write_path: str
) -> None:
    """Acceptance for VOYN-W0-AICC-OWNER-ITEM-MIRROR-UNCOVERED, per write path.

    A reconciliation that has only ever been observed passing is a
    reconciliation that guarantees nothing — the argument `test_mirror_coverage`
    makes about its own gates, and the reason that module feeds each of them a
    table nobody declared. This feeds this one a `wave1` that has stopped
    mirroring, once per path, and requires the loss to be reported.

    The stage is asserted, not merely that *something* diverged: a check that
    accepted any non-empty report would also pass if the reconciliation were
    broken in a way that flagged every row.
    """
    _patch(monkeypatch, pg_connection_factory)
    perturbed = _wave1_without_the_mirror_call_in(write_path)
    mirror = PostgresOwnerItemMirror(connection_factory=pg_connection_factory)

    db_path = tmp_path / "runtime.db"
    perturbed.db.migrate(db_path)

    reported: list[tuple[str, list[dict]]] = []

    def reconciled(stage: str) -> None:
        reported.append((stage, divergence(perturbed.list_owner_items(db_path), mirror)))

    _lifecycle(perturbed, db_path, reconciled)

    dirty = [stage for stage, entries in reported if entries]
    assert dirty, (
        f"deleting the mirror call from `{write_path}` changed nothing the "
        "reconciliation can see — which is the hole this task exists to close"
    )
    assert dirty[0] == FIRST_STAGE_THAT_NOTICES[write_path], reported


def test_every_lost_mirror_write_is_visible_to_reconciliation(
    pg_connection_factory, tmp_path, monkeypatch
) -> None:
    """The stronger statement: not "the call exists" but "no write is lost".

    The deletion demonstration covers the write paths this file knows about;
    the probe counts the writes from the *current* code on every run, so a
    stage that grows a second mirror write later is covered without anyone
    remembering to say so.

    `noticed()` reads the staged verdict rather than the end state, and that is
    load-bearing here — see the test below, which measures why.
    """
    from command_center.db import owner_item_store

    mirror = PostgresOwnerItemMirror(connection_factory=pg_connection_factory)
    runs: list[dict] = []

    def scenario() -> None:
        with pg_connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM owner_item")
        db_path = tmp_path / f"runtime-{len(runs)}.db"
        wave1.db.migrate(db_path)
        run: dict = {"dirty": []}
        runs.append(run)

        def reconciled(stage: str) -> None:
            if divergence(wave1.list_owner_items(db_path), mirror):
                run["dirty"].append(stage)

        _lifecycle(wave1, db_path, reconciled)
        run["final"] = divergence(wave1.list_owner_items(db_path), mirror)

    def noticed() -> bool:
        return bool(runs[-1]["dirty"])

    results = each_lost_write_is_noticed(
        monkeypatch,
        targets=(
            (
                owner_item_store,
                ("PostgresOwnerItemMirror",),
                lambda: PostgresOwnerItemMirror(connection_factory=pg_connection_factory),
            ),
        ),
        scenario=scenario,
        noticed=noticed,
    )

    # Four writes: two creations and two `done` toggles, all through the one
    # mirror this family has.
    assert [result.target for result in results] == ["PostgresOwnerItemMirror"] * 4
    missed = [result for result in results if not result.noticed]
    assert not missed, f"lost writes nothing noticed: {missed}"


def test_a_lost_write_a_later_path_repairs_is_invisible_at_the_end(
    pg_connection_factory, tmp_path, monkeypatch
) -> None:
    """Why the reconciliation above is staged, measured instead of asserted.

    `set_owner_item_done` mirrors the whole row, so it repairs whatever the
    `create` before it failed to write. Reconciling once at the end of the
    scenario therefore reports agreement for two of the four writes — including
    the very first one — and an end-state check is the shape a reconciliation
    naturally takes when nobody has thought about this.

    It is not a false alarm being avoided: the row *was* absent from the mirror
    in between, and nothing guarantees the repairing write ever comes. The item
    the scenario never toggles is the same loss with no repair behind it, and
    that one is visible at the end — which is the difference this test pins.

    Rerunning the probe rather than reusing the run above: sharing state
    between two tests to save a minute is how a test starts depending on
    execution order.
    """
    from command_center.db import owner_item_store

    mirror = PostgresOwnerItemMirror(connection_factory=pg_connection_factory)
    runs: list[dict] = []

    def scenario() -> None:
        with pg_connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM owner_item")
        db_path = tmp_path / f"endstate-{len(runs)}.db"
        wave1.db.migrate(db_path)
        run: dict = {}
        runs.append(run)
        _lifecycle(wave1, db_path, lambda _stage: None)
        run["final"] = divergence(wave1.list_owner_items(db_path), mirror)

    results = each_lost_write_is_noticed(
        monkeypatch,
        targets=(
            (
                owner_item_store,
                ("PostgresOwnerItemMirror",),
                lambda: PostgresOwnerItemMirror(connection_factory=pg_connection_factory),
            ),
        ),
        scenario=scenario,
        noticed=lambda: bool(runs[-1]["final"]),
    )

    # The counting run is `runs[0]`; the perturbed ones follow it in order.
    blind = [result.index for result in results if not result.noticed]
    assert blind == [0, 2], [
        (result.index, result.noticed) for result in results
    ]
