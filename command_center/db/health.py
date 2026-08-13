"""Liveness and readiness checks for the AICC server.

The two probes answer different questions and must not be collapsed into one:

* **Liveness** — is this process healthy enough to keep running? It touches no
  external dependency. If liveness failed on a database outage, every replica
  would be killed and restarted at the exact moment the database came back,
  turning a recoverable outage into a restart storm.

* **Readiness** — should this process receive traffic right now? It checks the
  database round-trip *and* that the schema is at the version this build
  expects. A process talking to a database migrated by a newer deploy will
  produce wrong answers rather than errors, which is worse than serving none.

Neither probe returns the connection string, the host or the user: probe
endpoints are commonly unauthenticated, so the payload is deliberately limited
to a status, a schema version and timing.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from command_center.db import migrations, pool

__all__ = ["HealthReport", "check_liveness", "check_readiness"]

# The schema version this build of the code is written against. Bumped in the
# same commit that adds a migration.
EXPECTED_SCHEMA_VERSION = 1


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
    """Database round-trip plus schema-version compatibility."""
    checks: dict[str, Any] = {"expected_schema_version": EXPECTED_SCHEMA_VERSION}
    started = time.perf_counter()

    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            version = migrations.current_version(conn)
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

    return HealthReport(status="ok", checks=checks)
