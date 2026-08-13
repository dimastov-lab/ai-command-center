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
                for _, column, ctype, notnull, _default, pk in conn.execute(
                    f'PRAGMA table_info("{name}")'
                ):
                    columns[column] = {"notnull": bool(notnull), "pk": pk, "type": ctype}
                    if pk:
                        primary_key.append((pk, column))
                tables[name] = {
                    "columns": columns,
                    "pk": [c for _, c in sorted(primary_key)],
                }
            shapes, uniques = set(), 0
            for index, table in conn.execute(
                "SELECT name, tbl_name FROM sqlite_master WHERE type='index' "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchall():
                cols = tuple(
                    row[2] for row in conn.execute(f'PRAGMA index_info("{index}")')
                )
                shapes.add((table, cols))
            for table in tables:
                for row in conn.execute(f'PRAGMA index_list("{table}")'):
                    if row[3] == "u":
                        uniques += 1
            checks = sum(
                sql.upper().count("CHECK (")
                for (sql,) in conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL"
                )
            )
            return {
                "tables": tables,
                "index_shapes": shapes,
                "unique_constraints": uniques,
                "check_constraints": checks,
            }
        finally:
            conn.close()


@pytest.fixture
def postgres_schema(admin_conn) -> dict:
    """The PostgreSQL target, produced by applying the real migration."""
    admin_conn.execute(INITIAL_MIGRATION.read_text(encoding="utf-8"))

    tables: dict[str, dict] = {}
    for table, column, nullable, data_type, default in admin_conn.execute(
        "SELECT table_name, column_name, is_nullable, data_type, column_default "
        "FROM information_schema.columns "
        "WHERE table_schema='public' ORDER BY table_name, ordinal_position"
    ).fetchall():
        tables.setdefault(table, {"columns": {}, "pk": [], "fks": set()})
        tables[table]["columns"][column] = {
            "notnull": nullable == "NO",
            "type": data_type,
            "default": default,
        }
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
    # Index shapes by (table, ordered columns), so a named index that lost its
    # composite form is visible even though the per-table count is unchanged.
    shapes = {
        (table, tuple(columns))
        for table, columns in admin_conn.execute(
            "SELECT t.relname, array_agg(a.attname ORDER BY k.ord) "
            "FROM pg_index i "
            "JOIN pg_class c ON c.oid = i.indexrelid "
            "JOIN pg_class t ON t.oid = i.indrelid "
            "JOIN pg_namespace n ON n.oid = t.relnamespace "
            "JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord) ON TRUE "
            "JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum "
            "WHERE n.nspname = 'public' GROUP BY c.oid, t.relname"
        ).fetchall()
    }
    counts = admin_conn.execute(
        "SELECT contype, count(*) FROM pg_constraint con "
        "JOIN pg_namespace n ON n.oid = con.connamespace "
        "WHERE n.nspname = 'public' AND contype IN ('u', 'c') GROUP BY contype"
    ).fetchall()
    by_type = {t: n for t, n in counts}
    return {
        "tables": tables,
        "index_shapes": shapes,
        "unique_constraints": by_type.get("u", 0),
        "check_constraints": by_type.get("c", 0),
    }


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
    sqlite_schema,
) -> None:
    """33, not the 16 recorded before waves W1-W3.

    Deliberately depends on the SQLite fixture alone. Pinning it behind the
    PostgreSQL fixture would let the number silently stop being defended on any
    machine without a database — which is exactly the condition under which the
    stale 16 survived long enough to reach a plan.
    """
    assert len(sqlite_schema["tables"]) == 33


def test_the_postgres_target_carries_the_same_count(postgres_schema) -> None:
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


def test_every_sqlite_index_keeps_its_column_set(sqlite_schema, postgres_schema) -> None:
    """Compared by column set, not by count.

    A count comparison looked sufficient and was not: SQLite's `sqlite_master`
    scan omits the autoindexes behind PRIMARY KEY and UNIQUE, while PostgreSQL's
    `pg_indexes` includes them. That is the whole of the apparent 62 -> 102
    growth (62 named + 33 PK + 7 UNIQUE), and it left every table with a slot of
    pure accounting slack — a table could lose its only real index and still
    satisfy `sqlite_count <= postgres_count`.

    Matching the (table, ordered columns) of each named index closes that: an
    index replaced by two narrower ones, or dropped outright, fails here.
    """
    lost = sorted(sqlite_schema["index_shapes"] - postgres_schema["index_shapes"])
    assert lost == [], "SQLite index column sets with no PostgreSQL counterpart"


def test_constraint_parity(sqlite_schema, postgres_schema) -> None:
    """UNIQUE and CHECK counts must match.

    The two CHECKs are import preconditions (`attempt_number >= 1`,
    `max_attempts >= 1`): a source row violating one must fail the insert rather
    than pass silently, so losing the constraint in the move would turn a loud
    rejection into corrupt data.
    """
    assert postgres_schema["unique_constraints"] == sqlite_schema["unique_constraints"]
    assert postgres_schema["check_constraints"] == sqlite_schema["check_constraints"]


def test_no_postgres_only_required_column(sqlite_schema, postgres_schema) -> None:
    """The reverse direction of the column check.

    A NOT NULL column that exists only in PostgreSQL has no source value, so the
    import fails on the first row. Checking only SQLite -> PostgreSQL would miss
    it entirely.
    """
    orphan_required = {
        table: sorted(
            name
            for name, spec in postgres_schema["tables"][table]["columns"].items()
            if spec["notnull"]
            and spec["default"] is None
            and name not in sqlite_columns["columns"]
        )
        for table, sqlite_columns in sqlite_schema["tables"].items()
        if any(
            spec["notnull"] and spec["default"] is None
            and name not in sqlite_columns["columns"]
            for name, spec in postgres_schema["tables"][table]["columns"].items()
        )
    }
    assert orphan_required == {}, "PostgreSQL requires columns the SQLite source cannot supply"


def test_the_columns_needing_value_conversion_are_the_documented_ones(
    sqlite_schema, postgres_schema
) -> None:
    """The map's headline hazard, pinned.

    114 of 395 columns change type, and 75 of them are `TEXT` -> `timestamptz`.
    `command_center/models.py:iso_now` writes naive local time with no offset, so
    those strings are reinterpreted under the importer's session time zone —
    a silent, unrecoverable shift. The count is asserted so the migration cannot
    be planned against a smaller number than the one that exists.
    """
    conversions: dict[str, int] = defaultdict(int)
    for table, spec in postgres_schema["tables"].items():
        source = sqlite_schema["tables"][table]["columns"]
        for name, column in spec["columns"].items():
            declared = (source.get(name, {}).get("type") or "").upper()
            target = column["type"]
            if not declared:
                continue
            if target == "timestamp with time zone" and declared.startswith("TEXT"):
                conversions["timestamptz"] += 1
            elif target == "jsonb" and declared.startswith("TEXT"):
                conversions["jsonb"] += 1
            elif target == "boolean" and declared.startswith("INT"):
                conversions["boolean"] += 1

    assert conversions["timestamptz"] == 75
    assert conversions["jsonb"] == 22
    assert conversions["boolean"] == 8


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

    # The previous version asserted `waves[0]` truthy and that the waves sum to
    # the table count — both tautologies given the loop above, so the documented
    # five-wave shape was in fact unpinned. The sizes are pinned instead: a table
    # that changes wave has changed its dependency position, which is a planning
    # fact the map states and therefore has to defend.
    assert [len(w) for w in waves] == [11, 9, 1, 10, 2]
    assert waves[2] == ["run"], "wave 3 is the bottleneck ten wave-4 tables depend on"
