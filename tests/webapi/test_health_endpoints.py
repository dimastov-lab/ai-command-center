"""Liveness and readiness endpoints (VOYN-W0-AICC-SRV-01a).

The important behaviours are the ones that only show up during an outage:
liveness must stay green when the database is gone (otherwise every replica is
killed at once), readiness must go red, and neither may leak connection
details into a payload that is typically served unauthenticated.

Readiness also carries the grant gate
(VOYN-W0-AICC-GRANTS-ARE-CORRECTNESS-NOT-HYGIENE): a database at the right
schema version whose privilege matrix was never applied is not ready, and it
is indistinguishable from a healthy one by every other check here. What the
gate *finds* is proved against a real PostgreSQL in
`tests/db/test_grant_gate.py`; what this module pins is the wiring — the
status code, the payload, and that an audit which cannot run fails closed.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from command_center.db import health
from command_center.db.grant_gate import GrantViolation
from command_center.webapi.app import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


class _Cursor:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args, **kwargs):
        return None

    def fetchone(self):
        return (1,)


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _Cursor()


def _stub_database(monkeypatch, *, version: int, audit) -> None:
    """Put a reachable database at `version` in front of the probe.

    `audit` stands in for `grant_gate.audit_grants`; it is patched rather than
    run because a stub connection cannot answer catalog questions, and a fake
    that could would be asserting about itself.
    """
    monkeypatch.setattr(health.pool, "connection", lambda: _Conn())
    monkeypatch.setattr(health.migrations, "current_version", lambda conn: version)
    monkeypatch.setattr(health.pool, "pool_stats", lambda: {"pool_size": 2})
    monkeypatch.setattr(health.grant_gate, "audit_grants", audit)
    # The probe suppresses repeats of an unchanged violation set; tests must
    # not inherit a previous test's suppression.
    monkeypatch.setattr(health, "_LAST_LOGGED_VIOLATIONS", None, raising=False)


def test_healthz_is_ok_without_a_database(client: TestClient) -> None:
    """No PostgreSQL is configured in this test process — liveness still passes."""
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"process": "ok"}}


def test_readyz_is_503_when_the_database_is_unreachable(client: TestClient) -> None:
    response = client.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["database"] == "unreachable"


def test_readyz_payload_never_leaks_connection_details(client: TestClient) -> None:
    """Probe endpoints are usually unauthenticated; the body must stay boring."""
    body = client.get("/readyz").text
    for secret in ("password", "dbname", "sslmode", "@", "postgresql://"):
        assert secret not in body


def test_readyz_reports_schema_mismatch_as_degraded(
    client: TestClient, monkeypatch
) -> None:
    """A process talking to a newer schema returns wrong answers, not errors."""
    _stub_database(
        monkeypatch,
        version=health.EXPECTED_SCHEMA_VERSION + 1,
        audit=lambda conn: (),
    )

    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "schema_mismatch"


def test_readyz_is_200_when_schema_matches_and_grants_are_applied(
    client: TestClient, monkeypatch
) -> None:
    _stub_database(
        monkeypatch, version=health.EXPECTED_SCHEMA_VERSION, audit=lambda conn: ()
    )

    response = client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["schema_version"] == health.EXPECTED_SCHEMA_VERSION
    assert body["checks"]["pool_size"] == 2
    assert body["checks"]["grants"] == "ok"


def test_readyz_is_503_when_the_grants_were_not_applied(
    client: TestClient, monkeypatch
) -> None:
    """A correctly migrated database with no grants must not receive traffic."""
    violation = GrantViolation(
        "public_execute", "PUBLIC", "public.queue_redrive(text,integer)", "EXECUTE"
    )
    _stub_database(
        monkeypatch,
        version=health.EXPECTED_SCHEMA_VERSION,
        audit=lambda conn: (violation,),
    )

    response = client.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    # The schema itself is fine — which is exactly why the other checks cannot
    # stand in for this one.
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["grants"] == "noncompliant"
    assert body["checks"]["grant_violations"] == 1
    assert body["checks"]["grant_violation_kinds"] == ["public_execute"]


def test_readyz_names_the_kind_but_not_the_reachable_object(
    client: TestClient, monkeypatch
) -> None:
    """The payload says what is wrong, not what an attacker could reach.

    `/readyz` is unauthenticated by convention, and a violation list is a map
    of which credential currently reaches which function. The detail goes to
    the process log and to `python -m command_center.db verify-grants`.
    """
    violation = GrantViolation(
        "extra_execute", "aicc_worker", "public.queue_redrive(text,integer)", "EXECUTE"
    )
    _stub_database(
        monkeypatch,
        version=health.EXPECTED_SCHEMA_VERSION,
        audit=lambda conn: (violation,),
    )

    body = client.get("/readyz").text
    assert "extra_execute" in body
    assert "queue_redrive" not in body
    assert "aicc_worker" not in body


def test_readyz_fails_closed_when_the_grant_audit_cannot_run(
    client: TestClient, monkeypatch
) -> None:
    """Not knowing whether the matrix holds is not a reason to serve traffic."""

    def _explode(conn):
        raise RuntimeError("catalog unavailable")

    _stub_database(
        monkeypatch, version=health.EXPECTED_SCHEMA_VERSION, audit=_explode
    )

    response = client.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["checks"]["grants"] == "unverified"
    assert body["checks"]["grant_check_error"] == "RuntimeError"
