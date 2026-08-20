"""Atomic delivery of the exposition into node_exporter's textfile collector.

WHY A TEXTFILE AND NOT AN HTTP ENDPOINT
=======================================
Measured on the target host rather than chosen from preference: the only metric
substrate that exists is `prometheus-node-exporter` with its textfile collector
at `/var/lib/prometheus/node-exporter/`, already fed by
`/usr/local/sbin/voyn-service-metrics` on a 30s timer. There is no Prometheus
server on the host and no scrape configuration to add a target to. An HTTP
exporter would therefore have shipped a port nothing connects to, plus a
long-lived process holding a database connection, to replace a file write that
the existing collector already publishes.

The control plane's own credential makes the same point: `queue_reap` and the
public views are `aicc_app` privileges, and the reaper already runs as a
oneshot under a timer (`aicc-queue-reaper.timer`) for exactly this reason —
"idempotent, so a missed tick delays recovery and never corrupts it". A gauge
snapshot has the same shape.

THE TWO RULES THAT MAKE THIS SAFE
=================================
1. **Write to a temp file in the SAME directory, then `os.replace`.** The
   replace is atomic within a filesystem, so a scrape either sees the whole
   previous file or the whole new one. Writing in place would let node_exporter
   read a half-written exposition and report a parse error for the whole
   textfile directory — which takes the *other* .prom files down with it.
2. **The temp file must not be named `*.prom`.** node_exporter globs `*.prom`
   in this directory, so a temp file matching that pattern is itself scraped
   mid-write. The existing shell collector gets this right with a `.prom.tmp`
   suffix and this module uses the same one.

Permissions are an operator concern and deliberately not solved here: the
directory is root-owned, so whatever user runs this must be granted write
access to it (the unit file documents the one-line ACL). Failing loudly on a
permission error is correct — silently skipping the write would present as a
stale-metrics incident hours later.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

__all__ = ["DEFAULT_TEXTFILE_DIR", "DEFAULT_FILENAME", "write_atomic"]

DEFAULT_TEXTFILE_DIR = Path("/var/lib/prometheus/node-exporter")

# Namespaced by product, matching `voyn_services.prom` beside it, so an
# operator clearing one collector's output cannot take the other's with it.
DEFAULT_FILENAME = "aicc_queue.prom"


def write_atomic(exposition: str, destination: Path) -> Path:
    """Write ``exposition`` so that no reader ever observes a partial file."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    # delete=False plus an explicit replace: NamedTemporaryFile's own cleanup
    # would unlink the path we are about to rename.
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=destination.parent,
        prefix=f"{destination.name}.",
        suffix=".tmp",
        delete=False,
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            handle.write(exposition)
            handle.flush()
            # The rename is atomic but not durable: without this the file can
            # be visible with zero length after a power loss, which is a
            # parse error rather than the stale-but-valid file a reader can
            # cope with.
            os.fsync(handle.fileno())
        # node_exporter reads files it does not own; the default 0600 from
        # mkstemp would make every scrape a permission error.
        os.chmod(temp_path, 0o644)
        os.replace(temp_path, destination)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    return destination
