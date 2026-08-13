"""Fixtures for the PostgreSQL foundation tests.

The integration tests here need a real PostgreSQL server: the properties under
test — transactional DDL, advisory locks, `GRANT`/`REVOKE` enforcement, and a
pg_dump/pg_restore round trip — have no meaningful in-memory substitute, and a
mocked driver would only assert that the test's own fake behaves as the test
expects.

The server is supplied out of band via `AICC_TEST_PG_ADMIN_DSN` (a superuser
connection string). When it is unset every test in this package skips, so the
default `pytest -q` gate on a laptop without Docker stays green; CI provides a
PostgreSQL service container and therefore actually runs them.

Each test gets its own freshly created database. Sharing one database across
tests would let a migration test's `DROP TABLE` race a privilege test's
`SELECT`, and this suite runs under `pytest -n auto`.
"""

from __future__ import annotations

import os
import secrets

import pytest

ADMIN_DSN_ENV = "AICC_TEST_PG_ADMIN_DSN"

# Passwords for the login-enabled variants of the three product roles. Random
# per session: nothing in the suite should work against a fixed credential that
# could accidentally be reused by a real deployment.
_ROLE_PASSWORDS = {
    "aicc_migrator": secrets.token_urlsafe(24),
    "aicc_app": secrets.token_urlsafe(24),
    "aicc_worker": secrets.token_urlsafe(24),
}


@pytest.fixture(scope="session")
def admin_dsn() -> str:
    dsn = os.environ.get(ADMIN_DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"{ADMIN_DSN_ENV} is not set; PostgreSQL integration tests skipped")
    return dsn


@pytest.fixture(scope="session")
def psycopg(admin_dsn):  # noqa: ARG001 — depend on admin_dsn so we skip before importing
    return pytest.importorskip("psycopg")


@pytest.fixture(scope="session")
def role_passwords(admin_dsn, psycopg) -> dict[str, str]:
    """Give the three product roles LOGIN and a password, cluster-wide.

    Roles are cluster objects rather than per-database ones, so this runs once
    and the per-test databases reuse them. Production does the same: the roles
    are created by `render_grants()`, and the operator attaches credentials.
    """
    from psycopg import sql

    from command_center.db import roles as roles_module

    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            for role in roles_module.ALL_ROLES:
                cur.execute(roles_module.render_role_creation(role))
                cur.execute(
                    sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                        sql.Identifier(role), sql.Literal(_ROLE_PASSWORDS[role])
                    )
                )
    return dict(_ROLE_PASSWORDS)


@pytest.fixture
def pg_database(admin_dsn, psycopg) -> str:
    """Create an empty database for one test and drop it afterwards."""
    name = f"aicc_test_{secrets.token_hex(8)}"
    from psycopg import sql

    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield name
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                        sql.Identifier(name)
                    )
                )


@pytest.fixture
def test_dsn(admin_dsn, pg_database) -> str:
    """The admin DSN pointed at this test's database."""
    return _override(admin_dsn, dbname=pg_database)


@pytest.fixture
def admin_conn(psycopg, test_dsn):
    """Superuser connection to the per-test database."""
    with psycopg.connect(test_dsn, autocommit=True) as conn:
        yield conn


@pytest.fixture
def connect_as(psycopg, test_dsn, role_passwords):
    """Factory opening a connection to the test database as a product role."""

    def _connect(role: str):
        return psycopg.connect(
            _override(test_dsn, user=role, password=role_passwords[role]),
            autocommit=True,
        )

    return _connect


def _override(dsn: str, **overrides: str) -> str:
    """Return `dsn` with the given libpq parameters replaced.

    Uses psycopg's own conninfo parser so both URL (`postgresql://…`) and
    keyword/value (`host=… dbname=…`) forms work — CI and a local Docker setup
    tend to disagree about which one they hand out.
    """
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    params = conninfo_to_dict(dsn)
    params.update(overrides)
    return make_conninfo(**params)


@pytest.fixture
def pg_connection_factory(admin_conn, psycopg, test_dsn):  # noqa: ARG001
    """A `connection()`-shaped factory over a migrated throwaway database.

    Shaped like `command_center.db.pool.connection` so the store under test
    talks to the real seam rather than to a fixture-specific interface — a
    store proved against a bespoke connection object is not proved against the
    one it runs on.
    """
    from contextlib import contextmanager

    from command_center.db import migrations

    migrations.upgrade(admin_conn)

    @contextmanager
    def factory():
        yield admin_conn

    return factory
