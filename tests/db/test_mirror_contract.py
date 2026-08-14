"""One contract every mirrored table inherits, whether or not anyone remembers.

Eight slices produced the same test module eight times: columns against the
DDL, columns against the live SQLite schema, the row-oriented protocol, a
round trip, a foreign-key refusal, timestamps, `jsonb`, identity. Each was
written by hand, and what a hand writes it can also forget — three slices
shipped a wrong test count, one shipped without a reconciliation entry point,
and each omission was found by review rather than by the suite.

So the contract is **discovered, not declared**: every `PostgresTableMirror`
subclass anywhere in `command_center/db/` is enrolled automatically — by what
the class is, not by what its file is called. A new table cannot opt out of
these checks by forgetting them, and a slice that adds a table now writes only
what is *specific* to it.

Two things make that possible and both live in the declaration:
`MirroredTable.columns` says what the row is, and `MirroredTable.references`
says which parents must exist first — so this file can build a valid parent for
any child without knowing anything about its family.

What stays in per-table modules: authority-side behaviour (which write paths
mirror, in what order, after which commit), the staged reconciliation against
the real writer, and anything a table does that the others do not.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from command_center import record_mirror
from command_center.db.table_mirror import MirroredTable, PostgresTableMirror
from command_center.db.table_mirror import divergence_against
from command_center.runtime.db import core as runtime_core

from tests.db.mirror_discovery import mirror_classes

ROOT = Path(__file__).resolve().parents[2]
DDL = (ROOT / "command_center/db/sql/0001_initial.up.sql").read_text(encoding="utf-8")

#: What `models.iso_now()` emits: naive local, second precision, no offset.
SAMPLE_TIMESTAMP = "2026-08-14T00:00:00"


def _discover() -> list[tuple[str, type[PostgresTableMirror]]]:
    """Every declared mirror, found rather than listed.

    Membership is decided by what a class *is*, not by what its file is called
    — see `mirror_discovery`, which both this suite and the stored-reader
    fitness gate now share. The earlier rule read `command_center/db/*_store.py`
    in each of them, and slice 9's acceptance used it to relocate the very
    defect that slice had just fixed: a mirror declared elsewhere in the package
    with a deliberately wrong key was collected by nothing and passed
    everything.
    """
    return [(table, mirror) for table, (mirror, _module) in mirror_classes().items()]


MIRRORS = _discover()
IDS = [table for table, _ in MIRRORS]


def _declared_columns(table: str) -> dict[str, str]:
    """`{column: declared type}` from the accepted schema.

    The DDL is the source of truth for the sample rows below: a `text` value in
    an `integer NOT NULL` column would fail on the real database and tell us
    nothing about the mirror.
    """
    body = DDL.split(f"CREATE TABLE {table} (", 1)[1].split(");", 1)[0]
    columns: dict[str, str] = {}
    for line in body.strip().splitlines():
        line = line.strip()
        if not line or line.startswith(("--", "UNIQUE", "PRIMARY KEY", "FOREIGN KEY", "CHECK")):
            continue
        name, _, rest = line.partition(" ")
        columns[name] = rest.strip().rstrip(",")
    return columns


def _value_for(table: str, column: str, spec: MirroredTable, row_id: str) -> object:
    declared = _declared_columns(table)[column]
    if column == "id":
        return 1 if spec.identity else row_id
    if column in spec.references:
        return f"{spec.references[column]}-parent"
    if column in spec.codec.json_values:
        return '{"b": 1, "a": 2}'
    if column in spec.codec.timestamps:
        return SAMPLE_TIMESTAMP
    if column in spec.codec.flags:
        return 1
    if declared.startswith(("integer", "bigint")):
        return 1
    if declared.startswith("double"):
        return 1.5
    if declared.startswith("boolean"):
        return True
    if declared.startswith("timestamptz"):
        # Declared as a timestamp but not converted: the map keeps two such
        # columns as free text on both sides, so this only fires if a mirror
        # forgot to declare a real one — which is the point.
        return SAMPLE_TIMESTAMP
    return f"<{column}>"


def sample_row(spec: MirroredTable, row_id: str = "row-1") -> dict:
    return {column: _value_for(spec.table, column, spec, row_id) for column in spec.columns}


def _ensure_parents(spec: MirroredTable, factory) -> None:
    """Create whatever `spec.references` says must exist first, recursively."""
    by_table = dict(MIRRORS)
    for parent_table in dict.fromkeys(spec.references.values()):
        parent_mirror = by_table[parent_table]
        _ensure_parents(parent_mirror.spec, factory)
        parent_mirror(connection_factory=factory).upsert(
            sample_row(parent_mirror.spec, f"{parent_table}-parent")
        )


# --- the schema half: no database needed -------------------------------------


@pytest.mark.parametrize(("table", "mirror"), MIRRORS, ids=IDS)
def test_the_column_list_matches_the_accepted_schema(table: str, mirror) -> None:
    assert tuple(_declared_columns(table)) == mirror.spec.columns


@pytest.mark.parametrize(("table", "mirror"), MIRRORS, ids=IDS)
def test_the_mirror_covers_what_the_authority_stores(table: str, mirror, tmp_path) -> None:
    """Against the live SQLite schema, not a source constant: a column added to
    a table and forgotten in the mirror is invisible to reconciliation."""
    db_path = tmp_path / "runtime.db"
    runtime_core.migrate(db_path)
    with runtime_core.connect(db_path) as conn:
        stored = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    assert stored == set(mirror.spec.columns)


@pytest.mark.parametrize(("table", "mirror"), MIRRORS, ids=IDS)
def test_the_mirror_satisfies_the_row_oriented_contract(table: str, mirror) -> None:
    instance = mirror(connection_factory=lambda: None)
    assert isinstance(instance, record_mirror.RecordMirror)
    assert mirror.name == "postgres"


@pytest.mark.parametrize(("table", "mirror"), MIRRORS, ids=IDS)
def test_the_declared_key_is_the_tables_primary_key(table: str, mirror) -> None:
    """The declaration must name the key the database actually has.

    `council_decision` is why the check exists: it is keyed by `motion_id` and
    carries an `id` column that is *not* unique, so a mirror left on the `id`
    default emits `ON CONFLICT (id)` — a constraint the table does not have.
    PostgreSQL raises `InvalidColumnReference`, the dual-write hook swallows it,
    and the table simply never mirrors. Nothing else in the suite notices:
    slice 9's acceptance flipped that declaration back to `"id"` and the whole
    `tests/db` run stayed green without a database, because every check that
    would have failed needs one and none of those says *declaration*.

    Two more tables have since surprised this migration the same way — `report`
    is keyed by `run_id`, and `provider_attempt` by `(run_id, attempt_number)`,
    the first composite key in the schema. Hence `key_columns` rather than a
    single name: the check compares whatever was declared against whatever the
    DDL declares, in either shape.
    """
    body = DDL.split(f"CREATE TABLE {table} (", 1)[1].split(");", 1)[0]
    composite = re.search(r"^\s*PRIMARY KEY \(([^)]+)\)", body, re.MULTILINE)
    if composite:
        declared = tuple(name.strip() for name in composite.group(1).split(","))
    else:
        inline = re.search(r"^\s*(\w+)\s+.*PRIMARY KEY", body, re.MULTILINE)
        assert inline, f"{table}: no primary key found in the accepted schema"
        declared = (inline.group(1),)
    assert mirror.spec.key_columns == declared, (
        f"{table}: declared key {mirror.spec.key!r}, schema says {declared}. "
        "A wrong key means `ON CONFLICT` names a constraint the table lacks, and the "
        "dual-write hook swallows the raise — the mirror stays empty and silent."
    )


@pytest.mark.parametrize(("table", "mirror"), MIRRORS, ids=IDS)
def test_the_declared_identity_matches_the_schema(table: str, mirror) -> None:
    """The third declaration field, checked like the other two.

    `identity` decides whether the statement carries `OVERRIDING SYSTEM VALUE`,
    which is what lets a mirror keep the authority's own id — the only id that
    makes a row identifiable on both sides. A wrong value fails loudly against
    a real PostgreSQL, so slice 9's acceptance called this non-blocking; it also
    observed that `identity` was the one declaration field with no check in the
    half that needs no server, which is the half that runs everywhere and the
    one where slice 9's blocking defect survived.
    """
    body = DDL.split(f"CREATE TABLE {table} (", 1)[1].split(");", 1)[0]
    declared = "GENERATED ALWAYS AS IDENTITY" in body
    assert mirror.spec.identity == declared, (
        f"{table}: declared identity={mirror.spec.identity}, schema says {declared}. "
        "Without `OVERRIDING SYSTEM VALUE` PostgreSQL refuses the authority's own id; "
        "with it on a table that has none, the statement is invalid."
    )


@pytest.mark.parametrize(("table", "mirror"), MIRRORS, ids=IDS)
def test_declared_references_match_the_schema(table: str, mirror) -> None:
    """`references` is documentation the dual-write depends on — it decides
    which hook runs first — so it is checked against the DDL rather than
    trusted."""
    body = DDL.split(f"CREATE TABLE {table} (", 1)[1].split(");", 1)[0]
    actual = {
        match.group(1): match.group(2)
        for match in re.finditer(r"^\s*(\w+)\s+.*REFERENCES (\w+)\(", body, re.MULTILINE)
    }
    assert mirror.spec.references == actual


# --- the behavioural half: against a real PostgreSQL -------------------------


@pytest.mark.parametrize(("table", "mirror"), MIRRORS, ids=IDS)
def test_a_row_round_trips_and_reconciles(table: str, mirror, pg_connection_factory) -> None:
    """The shape of every slice's first test, now inherited.

    Upsert what the authority would store, read it back, and reconcile: zero
    divergence means every declared conversion round-tripped, because
    `divergence` compares the stored row against the rendered one.
    """
    _ensure_parents(mirror.spec, pg_connection_factory)
    instance = mirror(connection_factory=pg_connection_factory)
    row = sample_row(mirror.spec)

    instance.upsert(row)
    instance.upsert(row)  # the backfill runs more than once by design

    assert len(instance.list_records()) == 1
    assert divergence_against(mirror.spec)([row], instance) == []


@pytest.mark.parametrize(
    ("table", "mirror"),
    [(t, m) for t, m in MIRRORS if m.spec.references],
    ids=[t for t, m in MIRRORS if m.spec.references],
)
def test_a_child_without_its_parent_is_refused(table: str, mirror, pg_connection_factory) -> None:
    """The target refuses it, and the mirror does not invent the parent —
    inventing one would put a row in the mirror the authority never wrote."""
    instance = mirror(connection_factory=pg_connection_factory)
    with pytest.raises(Exception) as refused:
        instance.upsert(sample_row(mirror.spec))
    assert "foreign key" in str(refused.value).lower()


@pytest.mark.parametrize(
    ("table", "mirror"),
    [(t, m) for t, m in MIRRORS if m.spec.codec.timestamps],
    ids=[t for t, m in MIRRORS if m.spec.codec.timestamps],
)
def test_timestamps_round_trip_to_what_the_application_writes(
    table: str, mirror, pg_connection_factory
) -> None:
    """Naive local text in, the same text out. The conversion this migration
    got wrong twice, checked for every declared column of every table."""
    from command_center import models

    written = models.iso_now()
    assert "+" not in written and not written.endswith("Z")  # guard the premise

    _ensure_parents(mirror.spec, pg_connection_factory)
    instance = mirror(connection_factory=pg_connection_factory)
    row = sample_row(mirror.spec)
    for column in mirror.spec.codec.timestamps:
        row[column] = written
    instance.upsert(row)

    stored = instance.list_records()[0]
    for column in mirror.spec.codec.timestamps:
        assert stored[column] == written, column


@pytest.mark.parametrize(
    ("table", "mirror"),
    [(t, m) for t, m in MIRRORS if m.spec.codec.json_values],
    ids=[t for t, m in MIRRORS if m.spec.codec.json_values],
)
def test_json_columns_reconcile_by_value_not_by_bytes(
    table: str, mirror, pg_connection_factory
) -> None:
    """`jsonb` does not preserve the source bytes — PostgreSQL reorders object
    keys — so a text comparison would report every object-valued row as
    different. Checked per table, because a table declaring a JSON column and
    forgetting to say so is exactly the omission this contract exists for."""
    _ensure_parents(mirror.spec, pg_connection_factory)
    instance = mirror(connection_factory=pg_connection_factory)
    row = sample_row(mirror.spec)
    instance.upsert(row)

    stored = instance.list_records()[0]
    for column in mirror.spec.codec.json_values:
        assert json.dumps(stored[column]) != row[column]  # the premise: bytes moved
    assert divergence_against(mirror.spec)([row], instance) == []


@pytest.mark.parametrize(
    ("table", "mirror"),
    [(t, m) for t, m in MIRRORS if m.spec.identity],
    ids=[t for t, m in MIRRORS if m.spec.identity],
)
def test_identity_tables_keep_the_authoritys_id_and_can_resync(
    table: str, mirror, pg_connection_factory
) -> None:
    """`divergence` matches rows by id, so a mirror minting its own would
    reconcile nothing. And the sequence is left behind by those inserts, which
    only bites on the first native write after a cutover."""
    _ensure_parents(mirror.spec, pg_connection_factory)
    instance = mirror(connection_factory=pg_connection_factory)
    row = sample_row(mirror.spec)
    row["id"] = 41
    instance.upsert(row)

    assert [stored["id"] for stored in instance.list_records()] == [41]
    assert instance.resync_identity() == 41
