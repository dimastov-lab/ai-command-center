"""Liveness and readiness checks for the AICC server.

The two probes answer different questions and must not be collapsed into one:

* **Liveness** — is this process healthy enough to keep running? It touches no
  external dependency. If liveness failed on a database outage, every replica
  would be killed and restarted at the exact moment the database came back,
  turning a recoverable outage into a restart storm.

* **Readiness** — should this process receive traffic right now? It checks the
  database round-trip, that the schema is at the version this build expects,
  *and* that the privilege matrix in `roles.py` is the one the database is
  actually enforcing. A process talking to a database migrated by a newer
  deploy will produce wrong answers rather than errors, which is worse than
  serving none; a database whose migrations landed without their grants is
  worse still, because it is silently running the claim protocol with the
  control plane's recovery surface exposed to every execution host
  (`grant_gate`, VOYN-W0-AICC-GRANTS-ARE-CORRECTNESS-NOT-HYGIENE).

Neither probe returns the connection string, the host or the user: probe
endpoints are commonly unauthenticated, so the payload is deliberately limited
to a status, a schema version, timing, and — for a failed grant audit — the
*kinds* of violation rather than which role reaches which object. The full
list goes to the process log and to `python -m command_center.db verify-grants`.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from command_center.db import grant_gate, migrations, pool

__all__ = ["HealthReport", "check_liveness", "check_readiness"]

_LOG = logging.getLogger(__name__)

# The last grant-audit result written to the log, so a probe scraped every few
# seconds reports a standing violation once rather than filling the log with
# the same forty lines until someone fixes it.
_LAST_LOGGED_VIOLATIONS: tuple[str, ...] | None = None

# The schema version this build of the code is written against. Derived from the
# migration set rather than hand-maintained: a constant someone must remember to
# bump alongside a new migration is one that eventually is not bumped, and the
# failure mode is severe — the deploy migrates successfully, then every replica
# reports schema_mismatch and 503s, taking the service down *after* the
# migration appeared to work.
EXPECTED_SCHEMA_VERSION = len(migrations.discover())


@dataclass(frozen=True, slots=True)
class HealthReport:
    status: str  # "ok" | "degraded"
    checks: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def check_liveness() -> HealthReport:
    """Process-local liveness. Never touches the database."""
    return HealthReport(status="ok", checks={"process": "ok"})


def check_readiness() -> HealthReport:
    """Database round-trip, schema-version compatibility and grant compliance."""
    checks: dict[str, Any] = {"expected_schema_version": EXPECTED_SCHEMA_VERSION}
    started = time.perf_counter()

    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            version = migrations.current_version(conn)
            # Nested, and after the round-trip has already succeeded, so an
            # audit that fails on its own is not reported as the database being
            # unreachable — the two need different operator responses.
            grants = _audit_grants(conn, checks)
    except Exception as exc:  # noqa: BLE001 — probe must not raise
        checks["database"] = "unreachable"
        # Type name only: exception text can carry host names and DSN fragments.
        checks["error"] = type(exc).__name__
        checks["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return HealthReport(status="degraded", checks=checks)

    checks["database"] = "ok"
    checks["schema_version"] = version
    checks["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)

    try:
        checks.update(pool.pool_stats())
    except pool.PoolNotOpenError:  # pragma: no cover — connection() would have raised
        pass

    if version != EXPECTED_SCHEMA_VERSION:
        checks["database"] = "schema_mismatch"
        return HealthReport(status="degraded", checks=checks)

    # Last, and non-negotiable: a schema at the right version whose grants were
    # never applied is exactly the state this gate exists to keep out of
    # rotation, and it is indistinguishable from a healthy one by every other
    # check here.
    if grants != "ok":
        return HealthReport(status="degraded", checks=checks)

    return HealthReport(status="ok", checks=checks)


def _audit_grants(conn, checks: dict[str, Any]) -> str:
    """Run the grant gate, record its verdict in `checks`, return that verdict.

    Fails closed. An audit that cannot be completed leaves `grants` at
    `"unverified"` and the process out of rotation, because "we could not check
    whether the worker can redrive its own dead-letter" is not a reason to send
    it traffic.
    """
    global _LAST_LOGGED_VIOLATIONS

    try:
        violations = grant_gate.audit_grants(conn)
    except Exception as exc:  # noqa: BLE001 — probe must not raise
        checks["grants"] = "unverified"
        checks["grant_check_error"] = type(exc).__name__
        _LOG.error("grant audit could not run: %s", type(exc).__name__)
        return "unverified"

    if not violations:
        checks["grants"] = "ok"
        _LAST_LOGGED_VIOLATIONS = None
        return "ok"

    checks["grants"] = "noncompliant"
    checks["grant_violations"] = len(violations)
    checks["grant_violation_kinds"] = grant_gate.violation_kinds(violations)

    rendered = tuple(str(violation) for violation in violations)
    if rendered != _LAST_LOGGED_VIOLATIONS:
        _LAST_LOGGED_VIOLATIONS = rendered
        _LOG.error(
            "database is not ready: %d grant violation(s). Re-run "
            "`python -m command_center.db upgrade` as the migrator. %s",
            len(rendered),
            " | ".join(rendered),
        )
    return "noncompliant"
