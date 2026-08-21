"""The deployment gate for the privilege matrix (VOYN-W0-AICC-GRANTS-…-HYGIENE).

The defect this module is built around is not a hygiene failure. Migration
`0002_queue_claim` contains no `GRANT` and no `REVOKE` — the whole policy lives
in `command_center/db/roles.py` and is applied by a separate call. On a
database where that call was skipped, PostgreSQL's default `EXECUTE` to
`PUBLIC` leaves the control plane's recovery surface (`queue_enqueue`,
`queue_reap`, `queue_redrive`) open to every execution host, and
`test_a_worker_resurrects_its_own_dead_letter_without_grants` below drives the
consequence end to end: a worker takes an item budgeted for a single attempt
and runs it six times, because it can widen its own budget.

So the tests here are in two halves. The first proves the reachability defect
against a real server, as the role, so it cannot be argued about. The second
proves the gate: it is red exactly on the database that permits the attack,
green on the one that refuses it, and — the acceptance clause — red again the
moment a migration lands *after* the grants were applied, until they are
re-asserted.

Skipped wholesale unless ``AICC_TEST_PG_ADMIN_DSN`` is set — see conftest.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import shutil
from contextlib import nullcontext

import pytest

from command_center.db import grant_gate, health, migrations, roles

pytestmark = pytest.mark.usefixtures("role_passwords")

QUEUE = "execution"


# ---------------------------------------------------------------------------
# Provisioning: the two orders that matter
# ---------------------------------------------------------------------------


def _migrator(psycopg, test_dsn, role_passwords):
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    params = conninfo_to_dict(test_dsn)
    params.update(
        user=roles.MIGRATOR_ROLE, password=role_passwords[roles.MIGRATOR_ROLE]
    )
    return psycopg.connect(make_conninfo(**params), autocommit=True)


def _migrate_only(admin_conn, psycopg, test_dsn, role_passwords) -> None:
    """Bootstrap and migrate, and stop there — the state under test."""
    roles.apply_bootstrap(admin_conn)
    with _migrator(psycopg, test_dsn, role_passwords) as conn:
        migrations.upgrade(conn)


def _provision(admin_conn, psycopg, test_dsn, role_passwords) -> None:
    """Bootstrap, migrate, grant — the order `python -m command_center.db` runs."""
    roles.apply_bootstrap(admin_conn)
    with _migrator(psycopg, test_dsn, role_passwords) as conn:
        migrations.upgrade(conn)
        roles.apply_table_grants(conn)


# ---------------------------------------------------------------------------
# Queue protocol helpers, driven exactly as a worker process would drive them
# ---------------------------------------------------------------------------


def _token() -> tuple[str, str]:
    token = secrets.token_hex(32)
    return token, hashlib.sha256(token.encode("utf-8")).hexdigest()


def _enqueue(conn, key: str, *, max_attempts: int) -> str:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT queue_enqueue(%s, %s, %s::jsonb, NULL, NULL, %s, 0, 0, 0)",
            (QUEUE, key, json.dumps({"job": key}), max_attempts),
        )
        return cur.fetchone()[0]


def _observe(admin_conn, work_item_id: str) -> tuple[int, int, str]:
    """`(max_attempts, attempt_count, state)`, read as the superuser.

    Deliberately not read as the worker: the worker holds no privilege on
    `work_item` even on an ungranted database, because PostgreSQL's PUBLIC
    default covers functions and not tables. Its whole reach is the four —
    here, seven — `SECURITY DEFINER` functions, which is the point.
    """
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT max_attempts, attempt_count, state FROM work_item "
            "WHERE work_item_id = %s",
            (work_item_id,),
        )
        return cur.fetchone()


def _burn_one_attempt(conn, *, reason: str) -> None:
    """Claim the item and fail it — one attempt off the budget, retryable."""
    token, token_hash = _token()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM queue_claim(%s, %s, %s)", (QUEUE, token_hash, 300))
        verdict = cur.fetchone()
        assert verdict[0] is True, f"claim refused: {verdict[1]}"
        attempt_id = verdict[3]
        cur.execute(
            "SELECT * FROM queue_fail(%s, %s, %s, true)", (attempt_id, token, reason)
        )
        assert cur.fetchone()[0] is True


# ---------------------------------------------------------------------------
# Half one: the defect, as the role, against a real server
# ---------------------------------------------------------------------------


def test_a_worker_resurrects_its_own_dead_letter_without_grants(
    admin_conn, psycopg, test_dsn, role_passwords, connect_as
):
    """An execution host widens its own attempt budget: `max_attempts` 1 -> 6.

    Every call in this test is made on a connection PostgreSQL authenticated as
    `aicc_worker`, over the same functions production uses. The budget is the
    only bound on a poisoned item — a job that wedges the host, a payload that
    crashes the agent — so a worker that can raise it has escaped the bound and
    can re-run the work the queue had already given up on, indefinitely.

    The route is not a bug in the protocol's SQL: `queue_redrive` is declared
    app-only in `roles.FUNCTION_PRIVILEGES`. It is reachable because migrations
    grant nothing and PostgreSQL gives `PUBLIC` `EXECUTE` on every new function
    by default. That is what makes the grants part of the protocol rather than
    a deployment nicety.
    """
    _migrate_only(admin_conn, psycopg, test_dsn, role_passwords)

    with connect_as(roles.WORKER_ROLE) as worker:
        # A control-plane function, called by a worker credential.
        work_item_id = _enqueue(worker, "poison", max_attempts=1)
        assert _observe(admin_conn, work_item_id)[0] == 1

        for cycle in range(5):
            _burn_one_attempt(worker, reason=f"wedged on cycle {cycle}")
            budget, attempts, state = _observe(admin_conn, work_item_id)
            assert state == "dead", (
                f"cycle {cycle}: the item should be dead-lettered with "
                f"{attempts}/{budget} attempts burned"
            )
            with worker.cursor() as cur:
                cur.execute("SELECT queue_redrive(%s, 1)", (work_item_id,))
                assert cur.fetchone()[0] is True, (
                    "the worker was refused a redrive — the defect does not "
                    "reproduce, so the rest of this assertion proves nothing"
                )

        budget, attempts, state = _observe(admin_conn, work_item_id)
        assert (budget, attempts, state) == (6, 5, "ready")
        _burn_one_attempt(worker, reason="sixth run of a single-attempt item")
        assert _observe(admin_conn, work_item_id)[1] == 6


def test_the_same_worker_is_refused_once_the_grants_are_applied(
    admin_conn, psycopg, test_dsn, role_passwords, connect_as
):
    """The identical calls, against the identical schema, after `roles.py` lands.

    Not just `queue_redrive`: the whole control-plane surface goes away, and
    the worker has no direct privilege on the queue tables to fall back on, so
    there is no second route to the same effect.
    """
    _provision(admin_conn, psycopg, test_dsn, role_passwords)

    refused = [
        ("SELECT queue_redrive('wki_nonexistent', 1)", None),
        ("SELECT queue_reap()", None),
        (
            "SELECT queue_enqueue(%s, %s, '{}'::jsonb, NULL, NULL, 1, 0, 0, 0)",
            (QUEUE, "k"),
        ),
        ("SELECT count(*) FROM work_item", None),
        ("UPDATE work_item SET max_attempts = 99", None),
    ]
    with connect_as(roles.WORKER_ROLE) as worker:
        for statement, params in refused:
            with worker.cursor() as cur:
                with pytest.raises(Exception, match="permission denied"):
                    cur.execute(statement, params)


# ---------------------------------------------------------------------------
# Half two: the gate
# ---------------------------------------------------------------------------


def test_the_gate_is_red_on_the_database_that_permits_the_attack(
    admin_conn, psycopg, test_dsn, role_passwords
):
    """Migrated, unimpeachable schema, no grants — and the gate names the route."""
    _migrate_only(admin_conn, psycopg, test_dsn, role_passwords)

    violations = grant_gate.audit_grants(admin_conn)
    assert violations

    public_execute = {
        violation.obj.partition("(")[0].removeprefix("public.")
        for violation in violations
        if violation.kind == "public_execute"
    }
    assert {"queue_enqueue", "queue_reap", "queue_redrive"} <= public_execute

    # And the same three, named against the role that must not reach them.
    worker_extra = {
        violation.obj.partition("(")[0].removeprefix("public.")
        for violation in violations
        if violation.kind == "extra_execute" and violation.role == roles.WORKER_ROLE
    }
    assert {"queue_enqueue", "queue_reap", "queue_redrive"} <= worker_extra

    # Not only "too much": the declared half is absent too, so the database is
    # not on the matrix in either direction. (The worker's own four protocol
    # functions are *not* reported missing — PUBLIC's default satisfies them,
    # which is exactly why "the app works" is no evidence the grants landed.)
    assert any(
        violation.kind == "missing_privilege" and violation.role == roles.APP_ROLE
        for violation in violations
    )


def test_the_gate_is_green_on_a_provisioned_database(
    admin_conn, psycopg, test_dsn, role_passwords
):
    """Zero violations after the production order. Anything else is noise.

    This is the anti-tautology half of the pair above: a checker that reported
    violations unconditionally would pass every assertion in this module except
    this one.
    """
    _provision(admin_conn, psycopg, test_dsn, role_passwords)

    violations = grant_gate.audit_grants(admin_conn)
    assert violations == (), "\n".join(str(v) for v in violations)


def test_the_gate_reopens_when_a_migration_lands_after_the_grants(
    admin_conn, psycopg, test_dsn, role_passwords, tmp_path
):
    """The acceptance clause: applied after *each* migration, not once.

    A real migration, run through the real runner and recorded in the real
    ledger, adds one function to a database whose grants were already applied.
    PostgreSQL hands `PUBLIC` `EXECUTE` on it, so the deploy that stops at
    "migrations applied" leaves the schema wider than the matrix — and the gate
    stays red until `apply_table_grants()` runs again.
    """
    _provision(admin_conn, psycopg, test_dsn, role_passwords)
    assert grant_gate.audit_grants(admin_conn) == ()

    sql_dir = tmp_path / "sql"
    shutil.copytree(migrations.SQL_DIR, sql_dir)
    version = len(migrations.discover()) + 1
    (sql_dir / f"{version:04d}_gate_probe.up.sql").write_text(
        "CREATE FUNCTION public.gate_probe() RETURNS integer\n"
        "    LANGUAGE sql VOLATILE SECURITY DEFINER AS $$ SELECT 1 $$;\n",
        encoding="utf-8",
    )
    (sql_dir / f"{version:04d}_gate_probe.down.sql").write_text(
        "DROP FUNCTION IF EXISTS public.gate_probe();\n", encoding="utf-8"
    )

    with _migrator(psycopg, test_dsn, role_passwords) as conn:
        assert migrations.upgrade(conn, sql_dir=sql_dir) == (version,)

        violations = grant_gate.audit_grants(admin_conn)
        assert [v for v in violations if v.kind == "public_execute"], (
            "a function created after the grants were applied is executable by "
            "PUBLIC, and the gate must refuse the database until it is not"
        )
        assert any("gate_probe" in v.obj for v in violations)

        # The step a deploy must not skip. `upgrade` makes it unconditionally.
        roles.apply_table_grants(conn)

    assert grant_gate.audit_grants(admin_conn) == ()


def test_the_gate_sees_a_hand_widened_column_grant(
    admin_conn, psycopg, test_dsn, role_passwords
):
    """`completion`'s `review_*` carve-out is enforced, not merely rendered.

    A table-wide `UPDATE` is the shape a well-meaning fix takes ("the worker
    could not write its completion row"), and it hands a compromised execution
    host the ability to stamp `review_verdict = 'approved'` on any run. The
    gate must name the columns, because at table level the difference between
    this and the declared grant is invisible.
    """
    _provision(admin_conn, psycopg, test_dsn, role_passwords)
    with admin_conn.cursor() as cur:
        cur.execute(f"GRANT UPDATE ON public.completion TO {roles.WORKER_ROLE}")

    violations = grant_gate.audit_grants(admin_conn)
    widened = {v.obj for v in violations if v.kind == "extra_column"}
    assert {f"public.completion.{column}" for column in ("review_verdict",)} <= widened
    assert any(v.kind == "extra_privilege" and "completion" in v.obj for v in violations)


def test_the_gate_answers_the_same_as_the_application_role(
    admin_conn, psycopg, test_dsn, role_passwords, connect_as
):
    """`aicc_app` runs the readiness probe and must see the worker's half.

    This is why the gate reads `pg_catalog` rather than `information_schema`:
    `role_table_grants` shows only grants the current role is party to, so an
    audit written against it would report a fully compliant database from the
    app's connection while the worker held the queue's recovery surface.
    """
    _migrate_only(admin_conn, psycopg, test_dsn, role_passwords)

    with connect_as(roles.APP_ROLE) as app:
        as_app = grant_gate.audit_grants(app)
    assert as_app == grant_gate.audit_grants(admin_conn)
    assert any(
        violation.role == roles.WORKER_ROLE and violation.kind == "extra_execute"
        for violation in as_app
    ), "the app role could not see the worker's widened reach"


def test_the_gate_reports_a_role_that_does_not_exist(
    admin_conn, psycopg, test_dsn, role_passwords, monkeypatch
):
    """A missing role is reported, not raised.

    `has_table_privilege` raises on an unknown role, which would abort the
    audit mid-query and leave readiness reporting "unverified" — true, but far
    less useful than naming the role nobody created.
    """
    _provision(admin_conn, psycopg, test_dsn, role_passwords)
    monkeypatch.setattr(
        roles, "GRANTED_ROLES", (*roles.GRANTED_ROLES, "aicc_never_created")
    )

    violations = grant_gate.audit_grants(admin_conn)
    assert [v for v in violations if v.kind == "missing_role"] == [
        grant_gate.GrantViolation(
            "missing_role", "aicc_never_created", "aicc_never_created"
        )
    ]


# ---------------------------------------------------------------------------
# The gate as a deployment decision: readiness, and the operator command
# ---------------------------------------------------------------------------


def test_readiness_stays_degraded_until_the_grants_are_applied(
    admin_conn, psycopg, test_dsn, role_passwords, monkeypatch
):
    """The gate's whole point, against a real database: migrated is not ready.

    Nothing else in the probe can tell these two databases apart — same
    version, same round trip, same pool.
    """
    _migrate_only(admin_conn, psycopg, test_dsn, role_passwords)
    monkeypatch.setattr(health.pool, "connection", lambda: nullcontext(admin_conn))
    monkeypatch.setattr(health.pool, "pool_stats", dict)
    monkeypatch.setattr(health, "_LAST_LOGGED_VIOLATIONS", None, raising=False)

    report = health.check_readiness()
    assert not report.ok
    assert report.checks["database"] == "ok"
    assert report.checks["schema_version"] == health.EXPECTED_SCHEMA_VERSION
    assert report.checks["grants"] == "noncompliant"
    assert "public_execute" in report.checks["grant_violation_kinds"]

    with _migrator(psycopg, test_dsn, role_passwords) as conn:
        roles.apply_table_grants(conn)

    report = health.check_readiness()
    assert report.ok, report.checks
    assert report.checks["grants"] == "ok"


def test_verify_grants_exits_non_zero_and_says_what_to_run(
    admin_conn, psycopg, test_dsn, role_passwords, capsys
):
    """`python -m command_center.db verify-grants` is the gate for a deploy script."""
    from command_center.db import cli

    _migrate_only(admin_conn, psycopg, test_dsn, role_passwords)
    assert cli._report_grant_audit(admin_conn) == 1
    err = capsys.readouterr().err
    assert "queue_redrive" in err
    assert "command_center.db upgrade" in err

    with _migrator(psycopg, test_dsn, role_passwords) as conn:
        roles.apply_table_grants(conn)
    assert cli._report_grant_audit(admin_conn) == 0
    assert "compliant" in capsys.readouterr().out
