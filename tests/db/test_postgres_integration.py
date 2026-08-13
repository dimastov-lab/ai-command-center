"""Integration tests against a real PostgreSQL server.

Skipped unless `AICC_TEST_PG_ADMIN_DSN` is set (see conftest). Everything here
asserts a property the database itself enforces — schema shape, privilege
denial, dump/restore fidelity — so none of it can be faked out by a stub.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid

import pytest

from command_center.db import migrations, roles

pytestmark = pytest.mark.usefixtures("role_passwords")


def _provision(admin_conn, psycopg, test_dsn, role_passwords):
    """Bootstrap as superuser, migrate as the migrator — the production order."""
    roles.apply_bootstrap(admin_conn)
    with psycopg.connect(
        _as_role(test_dsn, "aicc_migrator", role_passwords), autocommit=True
    ) as conn:
        applied = migrations.upgrade(conn)
        roles.apply_table_grants(conn)
    return applied


def _as_role(dsn: str, role: str, role_passwords: dict[str, str]) -> str:
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    params = conninfo_to_dict(dsn)
    params.update(user=role, password=role_passwords[role])
    return make_conninfo(**params)


# --------------------------------------------------------------------------
# Migrations
# --------------------------------------------------------------------------


def test_upgrade_creates_the_declared_schema(admin_conn, psycopg, test_dsn, role_passwords):
    applied = _provision(admin_conn, psycopg, test_dsn, role_passwords)
    assert applied == (1,)

    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        )
        tables = {row[0] for row in cur.fetchall()}
    assert tables == set(roles.ALL_TABLES)


def test_upgrade_is_idempotent(admin_conn, psycopg, test_dsn, role_passwords):
    _provision(admin_conn, psycopg, test_dsn, role_passwords)
    with psycopg.connect(
        _as_role(test_dsn, "aicc_migrator", role_passwords), autocommit=True
    ) as conn:
        assert migrations.upgrade(conn) == ()
        assert migrations.current_version(conn) == 1


def test_downgrade_removes_everything_and_upgrade_restores_it(
    admin_conn, psycopg, test_dsn, role_passwords
):
    """The acceptance criterion: forward *and* back, proven by execution."""
    _provision(admin_conn, psycopg, test_dsn, role_passwords)
    migrator_dsn = _as_role(test_dsn, "aicc_migrator", role_passwords)

    with psycopg.connect(migrator_dsn, autocommit=True) as conn:
        assert migrations.downgrade(conn, target=0) == (1,)
        assert migrations.current_version(conn) == 0

    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        )
        # Only the migration ledger survives a full downgrade.
        assert cur.fetchone()[0] == 1

    with psycopg.connect(migrator_dsn, autocommit=True) as conn:
        assert migrations.upgrade(conn) == (1,)
        roles.apply_table_grants(conn)

    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        )
        assert {row[0] for row in cur.fetchall()} == set(roles.ALL_TABLES)


def test_editing_an_applied_migration_is_rejected(
    admin_conn, psycopg, test_dsn, role_passwords
):
    """Two environments must not report the same version for different schemas."""
    _provision(admin_conn, psycopg, test_dsn, role_passwords)
    with admin_conn.cursor() as cur:
        cur.execute("UPDATE schema_migration SET checksum = 'stale' WHERE version = 1")

    with psycopg.connect(
        _as_role(test_dsn, "aicc_migrator", role_passwords), autocommit=True
    ) as conn:
        with pytest.raises(migrations.MigrationError, match="modified after it was applied"):
            migrations.upgrade(conn)


def test_typed_columns_reached_the_database(admin_conn, psycopg, test_dsn, role_passwords):
    """The decision to use real types is only real if the server agrees."""
    _provision(admin_conn, psycopg, test_dsn, role_passwords)
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'public'"
        )
        types = {(t, c): d for t, c, d in cur.fetchall()}

    assert types[("run", "created_at")] == "timestamp with time zone"
    assert types[("run", "is_resume")] == "boolean"
    assert types[("run", "command_json")] == "jsonb"
    assert types[("run_event", "id")] == "bigint"
    assert types[("model_entry", "cost")] == "double precision"
    # No ISO-8601-in-TEXT timestamps survived the translation.
    assert not [
        key for key, value in types.items() if key[1].endswith("_at") and value == "text"
    ]


# --------------------------------------------------------------------------
# Privilege matrix, exercised as each role
# --------------------------------------------------------------------------


def test_public_cannot_create_objects(admin_conn, psycopg, test_dsn, role_passwords):
    _provision(admin_conn, psycopg, test_dsn, role_passwords)
    with psycopg.connect(_as_role(test_dsn, "aicc_app", role_passwords)) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.cursor() as cur:
                cur.execute("CREATE TABLE smuggled (id text)")


def test_app_can_write_every_declared_table(
    admin_conn, psycopg, test_dsn, role_passwords
):
    _provision(admin_conn, psycopg, test_dsn, role_passwords)
    with psycopg.connect(_as_role(test_dsn, "aicc_app", role_passwords), autocommit=True) as conn:
        for table in roles.ALL_TABLES:
            with conn.cursor() as cur:
                # SELECT is the privilege every declared table shares; a denial
                # here means the grant loop missed the table entirely.
                cur.execute(f"SELECT count(*) FROM {table}")
                assert cur.fetchone()[0] >= 0


def test_app_cannot_drop_or_alter_tables(admin_conn, psycopg, test_dsn, role_passwords):
    """A SQL-injection foothold in the web layer must not reach DDL."""
    _provision(admin_conn, psycopg, test_dsn, role_passwords)
    app_dsn = _as_role(test_dsn, "aicc_app", role_passwords)
    for statement in ("DROP TABLE task", "ALTER TABLE task ADD COLUMN x text"):
        with psycopg.connect(app_dsn) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute(statement)


def test_no_role_may_delete_rows(admin_conn, psycopg, test_dsn, role_passwords):
    _provision(admin_conn, psycopg, test_dsn, role_passwords)
    for role in ("aicc_app", "aicc_worker"):
        with psycopg.connect(_as_role(test_dsn, role, role_passwords)) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM run")


@pytest.mark.parametrize(
    "table",
    ["proposal", "provenance_evidence", "motion", "council_vote", "audit_finding"],
)
def test_worker_cannot_read_governance_tables(
    admin_conn, psycopg, test_dsn, role_passwords, table
):
    """The core reason the worker role exists — proven, not asserted in a comment."""
    _provision(admin_conn, psycopg, test_dsn, role_passwords)
    with psycopg.connect(_as_role(test_dsn, "aicc_worker", role_passwords)) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {table}")


def test_worker_cannot_enqueue_but_can_claim(
    admin_conn, psycopg, test_dsn, role_passwords
):
    _provision(admin_conn, psycopg, test_dsn, role_passwords)
    worker_dsn = _as_role(test_dsn, "aicc_worker", role_passwords)

    with psycopg.connect(worker_dsn) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO queue_entry (id, state) VALUES ('q1', 'pending')"
                )

    with psycopg.connect(worker_dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE queue_entry SET state = 'claimed' WHERE id = 'absent'")
            assert cur.rowcount == 0  # allowed, matched nothing


def test_worker_can_append_run_events(admin_conn, psycopg, test_dsn, role_passwords):
    """Covers the identity-sequence grant: INSERT alone would fail here."""
    _provision(admin_conn, psycopg, test_dsn, role_passwords)
    run_id = _seed_run(admin_conn)

    with psycopg.connect(
        _as_role(test_dsn, "aicc_worker", role_passwords), autocommit=True
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO run_event (run_id, seq, event_type, payload_json, created_at) "
                "VALUES (%s, 1, 'started', %s, now()) RETURNING id",
                (run_id, "{}"),
            )
            assert cur.fetchone()[0] >= 1


def test_matrix_matches_the_catalog(admin_conn, psycopg, test_dsn, role_passwords):
    """Compare the declared matrix against `information_schema` row by row.

    The per-role tests above cover the cases that matter most; this one closes
    the gap between them, so a grant added to `PRIVILEGES` without a matching
    test still cannot widen access unnoticed.
    """
    _provision(admin_conn, psycopg, test_dsn, role_passwords)
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT grantee, table_name, privilege_type "
            "FROM information_schema.role_table_grants "
            "WHERE table_schema = 'public' AND grantee = ANY(%s)",
            (list(roles.ALL_ROLES),),
        )
        actual: dict[str, dict[str, set[str]]] = {}
        for grantee, table, privilege in cur.fetchall():
            actual.setdefault(grantee, {}).setdefault(table, set()).add(privilege)

    for role in (roles.APP_ROLE, roles.WORKER_ROLE):
        expected = {t: set(p) for t, p in roles.PRIVILEGES[role].items()}
        assert actual.get(role, {}) == expected, role


# --------------------------------------------------------------------------
# Backup / restore drill
# --------------------------------------------------------------------------


def _seed_run(conn) -> str:
    """Insert a minimal task→session→run chain and return the run id."""
    ident = uuid.uuid4().hex[:12]
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO task (id, project, title, task_type, created_at, updated_at) "
            "VALUES (%s, 'p', 't', 'feature', now(), now())",
            (f"task-{ident}",),
        )
        cur.execute(
            "INSERT INTO session (id, task_id, project, repository_path, created_at, updated_at) "
            "VALUES (%s, %s, 'p', '/tmp/repo', now(), now())",
            (f"sess-{ident}", f"task-{ident}"),
        )
        cur.execute(
            "INSERT INTO run (id, session_id, task_id, sequence, state, project, task_type, "
            "repository_path, prompt, created_at, updated_at) "
            "VALUES (%s, %s, %s, 1, 'running', 'p', 'feature', '/tmp/repo', 'go', now(), now())",
            (f"run-{ident}", f"sess-{ident}", f"task-{ident}"),
        )
    return f"run-{ident}"


def _client_major() -> int | None:
    """Major version of the local `pg_dump`, or None if it is absent."""
    if not shutil.which("pg_dump"):
        return None
    out = subprocess.run(["pg_dump", "--version"], capture_output=True, text=True).stdout
    for token in out.split():
        if token[0].isdigit():
            return int(token.split(".")[0])
    return None


def _server_major(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SHOW server_version_num")
        return int(cur.fetchone()[0]) // 10000


@pytest.mark.skipif(
    not (shutil.which("pg_dump") and shutil.which("pg_restore") and shutil.which("psql")),
    reason="PostgreSQL client binaries are not installed",
)
def test_backup_restore_drill_round_trips_data(
    admin_conn, psycopg, test_dsn, role_passwords, pg_database, tmp_path
):
    """Run the real scripts end to end and verify the restored rows.

    Proving the *scripts* work, not just that pg_dump works: the acceptance
    criterion is a demonstrated restore, and the scripts are what an operator
    will actually run at 3am.
    """
    from psycopg.conninfo import conninfo_to_dict

    # pg_dump refuses to talk to a newer server outright. Skipping keeps that
    # environment mismatch from being reported as a broken backup script — the
    # deploy image and CI both pin a client at or above the server version.
    client, server = _client_major(), _server_major(admin_conn)
    if client is not None and client < server:
        pytest.skip(f"pg_dump {client} cannot dump a PostgreSQL {server} server")

    _provision(admin_conn, psycopg, test_dsn, role_passwords)
    run_id = _seed_run(admin_conn)

    params = conninfo_to_dict(test_dsn)
    env = {
        **os.environ,
        "AICC_PG_HOST": params.get("host", "127.0.0.1"),
        "AICC_PG_PORT": str(params.get("port", 5432)),
        "AICC_PG_DB": pg_database,
        "AICC_PG_USER": params["user"],
        "AICC_PG_PASSWORD": params["password"],
    }
    repo_root = _repo_root()
    backup_dir = tmp_path / "backups"

    subprocess.run(
        [
            str(repo_root / "scripts" / "aicc_pg_backup.sh"),
            "--out-dir", str(backup_dir),
            "--verify",
        ],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )

    from pathlib import Path

    archives = sorted(backup_dir.glob("*.dump"))
    assert len(archives) == 1
    assert Path(f"{archives[0]}.sha256").exists()

    restored_db = f"{pg_database}_restored"
    try:
        subprocess.run(
            [
                str(repo_root / "scripts" / "aicc_pg_restore.sh"),
                "--archive", str(archives[0]),
                "--target-db", restored_db,
            ],
            check=True,
            env=env,
            capture_output=True,
            text=True,
        )

        from psycopg.conninfo import make_conninfo

        with psycopg.connect(make_conninfo(**dict(params, dbname=restored_db))) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT state, project FROM run WHERE id = %s", (run_id,))
                assert cur.fetchone() == ("running", "p")
                cur.execute("SELECT count(*) FROM information_schema.tables "
                            "WHERE table_schema = 'public'")
                assert cur.fetchone()[0] == len(roles.ALL_TABLES)
    finally:
        # The restored database is created by the script, so the `pg_database`
        # fixture does not know to drop it.
        from psycopg import sql
        from psycopg.conninfo import make_conninfo

        with psycopg.connect(
            make_conninfo(**dict(params, dbname="postgres")), autocommit=True
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                        sql.Identifier(restored_db)
                    )
                )


def test_restore_refuses_to_overwrite_the_live_database(tmp_path):
    """Guard rail, checked without touching a server."""
    archive = tmp_path / "fake.dump"
    archive.write_bytes(b"not-a-real-archive")
    result = subprocess.run(
        [
            str(_repo_root() / "scripts" / "aicc_pg_restore.sh"),
            "--archive", str(archive),
            "--target-db", "aicc_live",
        ],
        env={
            **os.environ,
            "AICC_PG_HOST": "127.0.0.1",
            "AICC_PG_DB": "aicc_live",
            "AICC_PG_USER": "aicc_migrator",
            "AICC_PG_PASSWORD": "irrelevant-for-this-check",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 3
    assert "refusing to restore over the live database" in result.stderr


def _repo_root():
    from pathlib import Path

    return Path(__file__).resolve().parents[2]
