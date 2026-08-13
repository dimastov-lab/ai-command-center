"""Slice 2 of the runtime migration: `owner_item`'s PostgreSQL mirror.

Slice 2 is the harder shape. `queue_entry`'s authority was already JSON, so
slice 1 added a mirror beside an existing one and moved nothing. Here SQLite
*is* the authority, so the claim under test is that a dual-write leaves it that
way — and that the reconciliation which will eventually gate the cutover can
actually compare the two stores.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from command_center import record_mirror
from command_center.db.owner_item_store import (
    MIRROR_UNAVAILABLE,
    OWNER_ITEM_COLUMNS,
    PostgresOwnerItemMirror,
    divergence,
)
from command_center.runtime.db import wave1

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
        "created_at": "2026-08-13T00:00:00Z",
        "updated_at": "2026-08-13T00:00:00Z",
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
    assert stored["created_at"] == "2026-08-13T00:00:00Z"
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
            # By now the authoritative row must already be readable.
            observed.append("mirror")
            assert wave1.get_owner_item(db_path, record["id"]) is not None

    monkeypatch.setattr(owner_item_store, "PostgresOwnerItemMirror", lambda: Recording())

    wave1.create_owner_item(db_path, title="ordered")

    assert observed == ["mirror"]
