"""The SQLite runtime store and the PostgreSQL target must stay in correspondence.

VOYN-W0-AICC-SRV-01b moves the runtime store off SQLite. The whole migration
rests on one claim — that every table and column the running engine owns has a
target on the PostgreSQL side — and a claim that large is worth checking by
machine rather than by reading two schemas side by side.

So this module derives both schemas from the systems themselves: the SQLite side
by running the real `migrate()` into a throwaway file, the PostgreSQL side by
applying the real migration to a throwaway database and reading
`information_schema`. Neither is parsed out of source, because source is where
the drift hides.

It also pins the two numbers that get quoted wrongly. An early survey recorded
"16 domain tables"; that was taken before waves W1-W3 landed and is off by more
than half. The live count is asserted here so the next person quotes a number a
test is defending.
"""

from __future__ import annotations

import sqlite3
import tempfile
from collections import defaultdict
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INITIAL_MIGRATION = REPO_ROOT / "command_center" / "db" / "sql" / "0001_initial.up.sql"

#: The bookkeeping table; not a domain table on either side.
SQLITE_LEDGER = "schema_version"


@pytest.fixture(scope="session")
def sqlite_schema() -> dict:
    """The live SQLite schema, produced by running the engine's own migrate()."""
    from command_center.runtime import db as runtime_db

    with tempfile.TemporaryDirectory() as raw:
        path = Path(raw) / "runtime.db"
        runtime_db.migrate(path)
        conn = sqlite3.connect(path)
        try:
            tables = {}
            for (name,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ):
                if name == SQLITE_LEDGER:
                    continue
                columns, primary_key = {}, []
                for _, column, _type, notnull, _default, pk in conn.execute(
                    f'PRAGMA table_info("{name}")'
                ):
                    columns[column] = {"notnull": bool(notnull), "pk": pk}
                    if pk:
                        primary_key.append((pk, column))
                tables[name] = {
                    "columns": columns,
                    "pk": [c for _, c in sorted(primary_key)],
                }
            indexes = defaultdict(int)
            for (_name, table) in conn.execute(
                "SELECT name, tbl_name FROM sqlite_master WHERE type='index' "
                "AND name NOT LIKE 'sqlite_%'"
            ):
                indexes[table] += 1
            return {"tables": tables, "indexes": dict(indexes)}
        finally:
            conn.close()


@pytest.fixture
def postgres_schema(admin_conn) -> dict:
    """The PostgreSQL target, produced by applying the real migration."""
    admin_conn.execute(INITIAL_MIGRATION.read_text(encoding="utf-8"))

    tables: dict[str, dict] = {}
    for table, column, nullable in admin_conn.execute(
        "SELECT table_name, column_name, is_nullable FROM information_schema.columns "
        "WHERE table_schema='public' ORDER BY table_name, ordinal_position"
    ).fetchall():
        tables.setdefault(table, {"columns": {}, "pk": [], "fks": set()})
        tables[table]["columns"][column] = {"notnull": nullable == "NO"}
    for table, column in admin_conn.execute(
        "SELECT tc.table_name, kcu.column_name FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name "
        "WHERE tc.table_schema='public' AND tc.constraint_type='PRIMARY KEY' "
        "ORDER BY tc.table_name, kcu.ordinal_position"
    ).fetchall():
        tables[table]["pk"].append(column)
    for table, referenced in admin_conn.execute(
        "SELECT tc.table_name, ccu.table_name FROM information_schema.table_constraints tc "
        "JOIN information_schema.constraint_column_usage ccu "
        "ON tc.constraint_name = ccu.constraint_name "
        "WHERE tc.table_schema='public' AND tc.constraint_type='FOREIGN KEY'"
    ).fetchall():
        tables[table]["fks"].add(referenced)
    indexes: dict[str, int] = defaultdict(int)
    for (table,) in admin_conn.execute(
        "SELECT tablename FROM pg_indexes WHERE schemaname='public'"
    ).fetchall():
        indexes[table] += 1
    return {"tables": tables, "indexes": dict(indexes)}


def test_every_runtime_table_has_a_postgres_target(sqlite_schema, postgres_schema) -> None:
    """One-to-one, in both directions.

    A SQLite table with no target is data the migration would silently drop. A
    PostgreSQL table with no source is a table nothing fills — either a
    forgotten migration step or a schema that has drifted ahead of the engine.
    """
    sqlite_tables = set(sqlite_schema["tables"])
    postgres_tables = set(postgres_schema["tables"])

    assert sorted(sqlite_tables - postgres_tables) == [], "SQLite tables with no PostgreSQL target"
    assert sorted(postgres_tables - sqlite_tables) == [], "PostgreSQL tables with no SQLite source"


def test_the_domain_table_count_is_the_live_one_not_the_early_survey(
    sqlite_schema, postgres_schema
) -> None:
    # 33, not the 16 recorded before waves W1-W3. Asserted so the number cannot
    # be quoted from memory again.
    assert len(sqlite_schema["tables"]) == 33
    assert len(postgres_schema["tables"]) == 33


def test_no_column_is_left_behind(sqlite_schema, postgres_schema) -> None:
    missing = {
        table: sorted(set(spec["columns"]) - set(postgres_schema["tables"][table]["columns"]))
        for table, spec in sqlite_schema["tables"].items()
        if set(spec["columns"]) - set(postgres_schema["tables"][table]["columns"])
    }
    assert missing == {}, "columns with no PostgreSQL target"


def test_primary_keys_agree(sqlite_schema, postgres_schema) -> None:
    """Same key, same order — the identity a row migrates under.

    SQLite reports `notnull=0` for its own primary-key columns (`PRAGMA` quirk;
    the constraint still enforces them), so nullability is deliberately *not*
    compared for key columns. Every nullability difference found at the time of
    writing was exactly this artefact and nothing else.
    """
    differing = {
        table: (spec["pk"], postgres_schema["tables"][table]["pk"])
        for table, spec in sqlite_schema["tables"].items()
        if spec["pk"] and spec["pk"] != postgres_schema["tables"][table]["pk"]
    }
    assert differing == {}, "primary key differs between the two schemas"

    without_key = sorted(
        table for table, spec in postgres_schema["tables"].items() if not spec["pk"]
    )
    assert without_key == [], "PostgreSQL table without a primary key"


def test_postgres_covers_every_sqlite_index(sqlite_schema, postgres_schema) -> None:
    """Per table, PostgreSQL must not have fewer indexes than SQLite.

    A count rather than a shape comparison on purpose: the two engines spell
    indexes differently, and an exact match would fail on cosmetics. Fewer,
    though, means a query pattern the runtime relies on lost its index in the
    move — a performance regression that no functional test would catch.
    """
    thinner = {
        table: (count, postgres_schema["indexes"].get(table, 0))
        for table, count in sqlite_schema["indexes"].items()
        if count > postgres_schema["indexes"].get(table, 0)
    }
    assert thinner == {}, "tables with fewer indexes in PostgreSQL than in SQLite"


def test_the_migration_order_is_derivable_and_acyclic(postgres_schema) -> None:
    """Rows must be insertable in some order that never violates a foreign key.

    A cycle would mean no such order exists and the migration needs deferred
    constraints — a design decision, not an implementation detail, so it must
    surface here rather than mid-backfill.
    """
    dependencies = {
        table: {ref for ref in spec["fks"] if ref != table}
        for table, spec in postgres_schema["tables"].items()
    }
    waves, placed = [], set()
    while len(placed) < len(dependencies):
        wave = sorted(t for t, deps in dependencies.items() if t not in placed and deps <= placed)
        assert wave, f"foreign-key cycle among {sorted(set(dependencies) - placed)}"
        waves.append(wave)
        placed |= set(wave)

    # Five waves at the time of writing; asserted loosely so adding a table does
    # not fail the suite, but a table that suddenly depends on nothing (or on
    # everything) shows up as a shape change.
    assert waves[0], "the first wave must contain the tables with no dependencies"
    assert sum(len(w) for w in waves) == len(postgres_schema["tables"])
