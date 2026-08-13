"""Slice 3 of the runtime migration: `conflict`'s PostgreSQL mirror.

Same dual-write shape as slice 2 — SQLite is the authority and must stay it —
with one property neither earlier slice had: a **nullable** `timestamptz`.
`resolved_at` is set when a conflict resolves and cleared when it reopens, so
it is the first mirrored column where the authority's value can go *back* to
`NULL`, and the first that can leave a mirror asserting a resolution the
authority has withdrawn.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from command_center import record_mirror
from command_center.db.conflict_store import (
    CONFLICT_COLUMNS,
    MIRROR_UNAVAILABLE,
    PostgresConflictMirror,
    divergence,
)
from command_center.runtime.db import conflict as conflict_db

ROOT = Path(__file__).resolve().parents[2]


def _row(conflict_id: str, **overrides: object) -> dict:
    row = {
        "id": conflict_id,
        "kind": "merge",
        "source_ref": "incident:1",
        "severity": "sev3",
        "status": "open",
        "owner": None,
        "mitigation": None,
        "project_ref": None,
        "opened_at": "2026-08-13T00:00:00",  # naive local, what `models.iso_now()` emits
        "resolved_at": None,
        "version": 0,
        "created_at": "2026-08-13T00:00:00",
        "updated_at": "2026-08-13T00:00:00",
    }
    row.update(overrides)  # type: ignore[arg-type]
    return row


@pytest.fixture
def mirror(pg_connection_factory) -> PostgresConflictMirror:
    return PostgresConflictMirror(connection_factory=pg_connection_factory)


# --- contract and authority -------------------------------------------------


def test_the_mirror_satisfies_the_row_oriented_contract() -> None:
    assert isinstance(
        PostgresConflictMirror(connection_factory=lambda: None), record_mirror.RecordMirror
    )
    assert PostgresConflictMirror.name == "postgres"


def test_sqlite_remains_the_authority_for_conflicts() -> None:
    """The dangerous outcome of this slice is a second system of record.

    The write paths must still write SQLite, and no read path may consult
    PostgreSQL to decide anything. Reads switch after reconciliation and the
    rollback and backup/restore drills — not as a side effect of a mirror
    landing.
    """
    for function in (
        conflict_db.create_conflict,
        conflict_db.get_conflict,
        conflict_db.list_conflicts,
        conflict_db._conflict_transition,
    ):
        source = inspect.getsource(function)
        for marker in ("postgres", "conflict_store", "list_records"):
            assert marker not in source.lower(), f"{function.__name__}: {marker}"

    assert "INSERT INTO conflict" in inspect.getsource(conflict_db.create_conflict)


def test_the_column_list_matches_the_accepted_postgresql_schema() -> None:
    """The map is the contract; drifting from it silently is how a mirror ends
    up writing a column the target does not have."""
    ddl = (ROOT / "command_center/db/sql/0001_initial.up.sql").read_text(encoding="utf-8")
    body = ddl.split("CREATE TABLE conflict (", 1)[1].split(");", 1)[0]
    declared = tuple(
        line.strip().split()[0]
        for line in body.strip().splitlines()
        if line.strip() and not line.strip().startswith("--")
    )
    assert declared == CONFLICT_COLUMNS


def test_the_mirror_covers_every_column_the_authority_writes() -> None:
    """A column the authority stores and the mirror omits is invisible: the
    reconciliation only compares what it is given, so the missing field would
    never be reported as divergence."""
    assert set(conflict_db._CONFLICT_COLUMNS) == set(CONFLICT_COLUMNS)


# --- behaviour against a real PostgreSQL ------------------------------------


def test_the_timestamp_gap_round_trips(mirror: PostgresConflictMirror) -> None:
    """Timestamps are TEXT here and `timestamptz` there. Unconverted, every row
    reads as different and the cutover gate is permanently red — which invites
    loosening the comparison instead of fixing the conversion."""
    mirror.upsert(_row("a"))

    stored = mirror.list_records()[0]

    assert stored["opened_at"] == "2026-08-13T00:00:00"
    assert isinstance(stored["opened_at"], str)


def test_a_null_resolved_at_survives_the_round_trip(mirror: PostgresConflictMirror) -> None:
    """The column is nullable and an open conflict has no resolution date.
    `None` must come back as `None`, not as an epoch or a rendered string."""
    mirror.upsert(_row("open-one"))

    assert mirror.list_records()[0]["resolved_at"] is None


def test_reopening_clears_the_mirrors_resolution_date(mirror: PostgresConflictMirror) -> None:
    """The property that makes whole-row upserts load-bearing rather than
    stylistic. `_conflict_transition` clears `resolved_at` on a reopen; a
    mirror updated field-by-field would keep the old date and go on asserting a
    resolution the authority has withdrawn — and it would reconcile clean on
    every other column."""
    mirror.upsert(_row("r", status="resolved", resolved_at="2026-08-13T10:00:00", version=1))
    assert mirror.list_records()[0]["resolved_at"] == "2026-08-13T10:00:00"

    mirror.upsert(_row("r", status="open", resolved_at=None, version=2))

    assert mirror.list_records()[0]["resolved_at"] is None


def test_upsert_is_idempotent_and_updates_in_place(mirror: PostgresConflictMirror) -> None:
    # The backfill runs more than once by design; an insert-only mirror would
    # fail the second run on rows it wrote itself.
    row = _row("a")
    mirror.upsert(row)
    mirror.upsert(row)
    assert len(mirror.list_records()) == 1

    mirror.upsert(_row("a", status="mitigating", owner="ops", version=1))

    stored = mirror.list_records()
    assert len(stored) == 1
    assert (stored[0]["status"], stored[0]["owner"], stored[0]["version"]) == (
        "mitigating",
        "ops",
        1,
    )


def test_reconciliation_reports_agreement_and_every_shape_of_disagreement(
    mirror: PostgresConflictMirror,
) -> None:
    agreed = _row("same")
    mirror.upsert(agreed)
    assert divergence([agreed], mirror) == []

    mirror.upsert(_row("same", severity="sev1"))
    assert [entry["fields"] for entry in divergence([agreed], mirror)] == [["severity"]]

    missing = divergence([agreed, _row("absent")], mirror)
    assert {entry["id"] for entry in missing} >= {"absent"}

    # A mirror ahead of the system of record is the state no check would flag
    # if reconciliation only walked the authority.
    assert {entry["id"] for entry in divergence([], mirror)} == {"same"}


def test_an_unreadable_mirror_is_reported_not_treated_as_agreement() -> None:
    class Broken:
        name = "postgres"

        def list_records(self) -> list[dict]:
            raise RuntimeError("connection refused")

    reported = divergence([_row("a")], Broken())

    assert [entry["id"] for entry in reported] == [MIRROR_UNAVAILABLE]
    assert "RuntimeError" in reported[0]["detail"]


def test_reconciliation_is_clean_for_rows_the_application_actually_wrote(
    mirror: PostgresConflictMirror, tmp_path
) -> None:
    """End to end against the authority, not against a fixture's idea of it.

    Rows created and then *transitioned* through the real writer must reconcile
    with zero divergence — including the one carrying a real `resolved_at`.
    This is the assertion the cutover is gated on, so it runs on rows the real
    writer produced rather than on `_row`, which is this file's own guess at
    their shape.
    """
    db_path = tmp_path / "runtime.db"
    conflict_db.db.migrate(db_path)

    opened = conflict_db.create_conflict(db_path, kind="merge", source_ref="incident:7")
    resolved = conflict_db.set_conflict_status(
        db_path, opened["id"], expected_version=0, status="resolved"
    )
    assert resolved["resolved_at"], "the premise: this row carries a real resolution date"

    for record in (resolved,):
        mirror.upsert(record)

    assert divergence([resolved], mirror) == []


def test_importing_the_store_needs_no_postgresql_client() -> None:
    import subprocess
    import sys

    probe = (
        "import sys;"
        "import command_center.db.conflict_store as s;"
        "assert 'aios_db' not in sys.modules;"
        "assert 'psycopg' not in sys.modules;"
        "s.PostgresConflictMirror()"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, cwd=ROOT, check=False
    )
    assert result.returncode == 0, result.stderr


# --- the dual-write itself --------------------------------------------------


def test_a_mirror_failure_cannot_break_the_authoritative_write(tmp_path, monkeypatch) -> None:
    """During dual-write the mirror is not load-bearing. Letting it raise would
    mean a migration step could take down the very table it is migrating."""
    from command_center.db import conflict_store

    class Exploding:
        def upsert(self, record: dict) -> None:
            raise RuntimeError("postgres is down")

    monkeypatch.setattr(conflict_store, "PostgresConflictMirror", lambda: Exploding())

    db_path = tmp_path / "runtime.db"
    conflict_db.db.migrate(db_path)

    created = conflict_db.create_conflict(db_path, kind="perf", source_ref="incident:9")
    updated = conflict_db.update_conflict_fields(
        db_path, created["id"], expected_version=0, fields={"owner": "ops"}
    )

    assert conflict_db.get_conflict(db_path, created["id"])["owner"] == "ops"
    assert updated["owner"] == "ops"


def test_every_write_path_mirrors_the_committed_row(tmp_path, monkeypatch) -> None:
    """Ordering and coverage in one, because both failures look identical from
    outside: a mirror that disagrees with the authority.

    Recorded rather than asserted inside the callback. `_mirror_conflict`
    swallows every `Exception`, and `AssertionError` is one — an assertion in
    there is caught, logged at DEBUG and lost, so the test would pass whatever
    it claimed. Independent review proved that on slice 2 by inverting the
    condition and watching the test still pass.
    """
    from command_center.db import conflict_store

    db_path = tmp_path / "runtime.db"
    conflict_db.db.migrate(db_path)
    observed: list[tuple[str, dict | None]] = []

    class Recording:
        def upsert(self, record: dict) -> None:
            # What SQLite holds *now*: if this runs before the commit, the
            # authority still shows the previous row — or no row at all.
            observed.append((record["status"], conflict_db.get_conflict(db_path, record["id"])))

    monkeypatch.setattr(conflict_store, "PostgresConflictMirror", lambda: Recording())

    created = conflict_db.create_conflict(db_path, kind="budget", source_ref="incident:3")
    conflict_db.update_conflict_fields(
        db_path, created["id"], expected_version=0, fields={"owner": "ops", "mitigation": "m"}
    )
    conflict_db.set_conflict_status(db_path, created["id"], expected_version=1, status="resolved")

    # Three writes, three mirror calls: an unmirrored path is divergence that
    # only reconciliation would find, and only if someone ran it.
    assert [status for status, _ in observed] == ["open", "open", "resolved"]
    # Each one saw the committed row, and saw the row this very write produced.
    for status, authoritative in observed:
        assert authoritative is not None, "the mirror ran before the commit"
        assert authoritative["status"] == status
    assert observed[1][1]["owner"] == "ops"
    assert observed[2][1]["resolved_at"] is not None
