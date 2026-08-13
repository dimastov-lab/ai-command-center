"""Slice 1 of the runtime migration: the queue's PostgreSQL mirror.

Two things are under test and only one of them is the code. The other is the
claim that this slice does *not* move authority — a migration that quietly
promoted a mirror would look exactly like a passing test suite.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from command_center import execution_mirror, execution_queue
from command_center.db.execution_mirror import QUEUE_ENTRY_COLUMNS, PostgresQueueMirror

ROOT = Path(__file__).resolve().parents[2]


def _entry(entry_id: str, **overrides: object) -> dict:
    entry = {
        "id": entry_id,
        "task_id": f"task-{entry_id}",
        "project": "demo",
        "state": "queued",
        "reason": None,
        "run_id": None,
        "added_at": "2026-08-13T00:00:00Z",
        "evaluated_at": None,
        "launched_at": None,
    }
    entry.update(overrides)  # type: ignore[arg-type]
    return entry


# --- contract ---------------------------------------------------------------


def test_the_postgres_mirror_satisfies_the_shared_contract() -> None:
    # Structural, not nominal: the divergence check must be able to take any
    # mirror, so conformance is the property that matters, not inheritance.
    assert isinstance(PostgresQueueMirror(connection_factory=lambda: None), execution_mirror.QueueMirror)
    assert PostgresQueueMirror.name == "postgres"


def test_position_is_not_part_of_an_entry() -> None:
    """`position` is how the mirror preserves order, not a field of the queue.

    Including it would make every entry differ from its JSON counterpart and
    turn the divergence check — the thing gating the cutover — into noise.
    """
    assert "position" not in QUEUE_ENTRY_COLUMNS


# --- authority --------------------------------------------------------------


def test_this_slice_does_not_move_authority_away_from_json() -> None:
    """The dangerous outcome of a migration slice is a second authority.

    `save_queue` must still write JSON first and treat every mirror as
    best-effort; if that inverts, a mirror failure could take down the queue it
    is migrating, and a mirror ahead of the real queue is a state no check
    flags as wrong.
    """
    source = inspect.getsource(execution_queue.save_queue)
    assert "atomic_write_json" in source
    json_write = source.index("atomic_write_json")
    assert all(
        json_write < source.index(marker)
        for marker in ("_mirror",)
        if marker in source
    ), "the authoritative JSON write must precede any mirror write"


def test_the_postgres_mirror_is_never_read_on_the_queue_read_path() -> None:
    """`load_queue` is the read path. It must read the authority, not a mirror
    — otherwise authority has moved by accident rather than by decision."""
    assert "read_json" in inspect.getsource(execution_queue.load_queue)
    assert "postgres" not in inspect.getsource(execution_queue.load_queue).lower()


# --- behaviour against a real PostgreSQL ------------------------------------


@pytest.fixture
def mirror(pg_connection_factory) -> PostgresQueueMirror:
    return PostgresQueueMirror(connection_factory=pg_connection_factory)


def test_replace_is_idempotent(mirror: PostgresQueueMirror) -> None:
    # The backfill is expected to run more than once — once per operator, once
    # after a rollback and re-advance. An appending mirror would manufacture
    # exactly the divergence the backfill exists to remove.
    entries = [_entry("a"), _entry("b")]

    mirror.replace_entries(entries)
    mirror.replace_entries(entries)

    assert mirror.list_entries() == entries


def test_order_survives_the_round_trip(mirror: PostgresQueueMirror) -> None:
    # The queue is planned and displayed in insertion order; a set-shaped table
    # would reorder it silently.
    entries = [_entry(name) for name in ("z", "m", "a")]

    mirror.replace_entries(entries)

    assert [entry["id"] for entry in mirror.list_entries()] == ["z", "m", "a"]


def test_replacing_with_fewer_entries_removes_the_rest(mirror: PostgresQueueMirror) -> None:
    mirror.replace_entries([_entry("a"), _entry("b"), _entry("c")])

    mirror.replace_entries([_entry("b")])

    assert [entry["id"] for entry in mirror.list_entries()] == ["b"]


def test_an_empty_queue_is_representable(mirror: PostgresQueueMirror) -> None:
    # Draining the queue must be expressible; a mirror that cannot go empty
    # reports a queue that no longer exists.
    mirror.replace_entries([_entry("a")])

    mirror.replace_entries([])

    assert mirror.list_entries() == []


def test_the_mirror_round_trips_a_json_entry_unchanged(mirror: PostgresQueueMirror) -> None:
    """What comes back must be comparable to JSON with no translation step,
    because the divergence check compares them directly."""
    entry = _entry(
        "full",
        reason="blocked",
        run_id="run-1",
        evaluated_at="2026-08-13T01:00:00Z",
        launched_at="2026-08-13T02:00:00Z",
    )

    mirror.replace_entries([entry])

    assert mirror.list_entries() == [entry]


def test_importing_the_mirror_needs_no_postgresql_client(tmp_path: Path) -> None:
    """`command_center.db` promises that importing it pulls in neither
    `aios_db` nor `psycopg`, so the desktop and CLI entry points keep working on
    a machine with no client library. A module-level pool import here would
    break that for every future importer, at import time."""
    import subprocess
    import sys

    probe = (
        "import sys;"
        "import command_center.db.execution_mirror as qs;"
        "assert 'aios_db' not in sys.modules, sorted(m for m in sys.modules if 'aios' in m);"
        "assert 'psycopg' not in sys.modules;"
        "qs.PostgresQueueMirror()"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, cwd=ROOT, check=False
    )
    assert result.returncode == 0, result.stderr


def test_timestamps_come_back_normalised_not_as_datetimes(mirror: PostgresQueueMirror) -> None:
    """`timestamptz` stores an instant; JSON holds a string.

    Left unconverted the mirror returns `datetime` objects and the divergence
    check reports every timestamped entry as different — a cutover gate that is
    permanently red, which invites loosening the comparison instead of fixing
    the conversion.
    """
    mirror.replace_entries([_entry("t")])

    value = mirror.list_entries()[0]["added_at"]

    assert isinstance(value, str)
    assert value == "2026-08-13T00:00:00Z"


def test_a_differently_spelled_instant_normalises_rather_than_round_trips(
    mirror: PostgresQueueMirror,
) -> None:
    """Honest statement of the limit this slice inherits.

    `timestamptz` keeps the instant, not the spelling, so `+00:00` comes back
    as `Z`. Every timestamped table in the remaining waves has this property,
    and the divergence check has to compare instants for those columns rather
    than strings. Asserting it here stops a later reader from assuming a
    byte-exact round trip that the column type cannot provide.
    """
    mirror.replace_entries([_entry("spelling", added_at="2026-08-13T00:00:00+00:00")])

    assert mirror.list_entries()[0]["added_at"] == "2026-08-13T00:00:00Z"
