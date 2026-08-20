"""Queue, lease and worker telemetry (VOYN-W0-AICC-SRV-08-WORKER-METRICS).

`contract.py` is the authority for metric names; `queue_metrics.py` produces
the series; `exposition.py` renders them; `textfile.py` delivers them to
node_exporter. The alert rules and dashboard that consume these names live in
`deploy/prometheus/` and `deploy/grafana/` and are tested against the contract.

Import purity, mirroring `command_center.db`'s promise: importing this package
pulls in neither `psycopg` nor a database connection. The pool is resolved on
use inside `QueueMetricsCollector`, so a test or a rules-linting job can import
the contract without a driver installed.
"""

from __future__ import annotations

__all__ = ["contract"]

from command_center.telemetry import contract
