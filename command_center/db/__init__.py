"""PostgreSQL foundation for the server deployment of AI Command Center.

Scope of this package (VOYN-W0-AICC-SRV-01a): configuration, pooling, the
migration runner, the least-privilege role matrix and the health probes — the
substrate a server install needs before anything is stored in it. Moving the
runtime store off SQLite onto this seam is the follow-up slice
(VOYN-W0-AICC-SRV-01b); until that lands, `command_center.runtime.db` remains
the authority for existing installs.

Submodules are imported lazily by callers rather than re-exported here, so
importing `command_center.db` does not pull in `psycopg` — the desktop and CLI
entry points must keep working on machines with no PostgreSQL client library.
"""

from __future__ import annotations

__all__ = ["config", "health", "migrations", "pool", "roles"]
