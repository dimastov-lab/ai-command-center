"""Connection pooling for the AICC server database.

A pool rather than per-request connections because PostgreSQL forks a backend
process per connection: at the request rates the dispatcher and the worker
fleet generate, connect-per-query would spend more time forking backends than
running queries, and an unbounded connection count is the standard way to take
a PostgreSQL server down.

The pool itself — opening it, proving the open, counting it — is generic
PostgreSQL machinery and belongs to `aios-db` (VOYN-W0-AIOS-DB-01), reached
through `command_center.db.adapter`. What stays here is the part that is about
*this* service: which configuration the pool is built from, and the fact that
there is exactly one of it per process.

That singleton is why this module exists at all rather than callers holding
their own pool. It is opened explicitly (`open_pool`) and closed explicitly
(`close_pool`) rather than lazily on first use, so a process that cannot reach
the database fails at startup — where an operator sees it — instead of on the
first request that happens to need data.

`connection()` yields a connection with autocommit on. Transactions are opened
deliberately via `conn.transaction()` at the call site; an implicit transaction
per checkout is how a long-lived pooled connection ends up holding an idle
transaction open and blocking VACUUM. Autocommit is also what the migration
runner and the advisory-lock primitives require.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from command_center.db import adapter
from command_center.db.config import PostgresConfig, load_config

__all__ = [
    "PoolNotOpenError",
    "close_pool",
    "connection",
    "get_pool",
    "open_pool",
    "pool_stats",
]

_LOG = logging.getLogger(__name__)

_lock = threading.Lock()
_pool = None
_config: PostgresConfig | None = None


class PoolNotOpenError(RuntimeError):
    """Raised when the pool is used before `open_pool()` or after `close_pool()`."""


def open_pool(config: PostgresConfig | None = None):
    """Open the process-wide pool and verify connectivity. Idempotent."""
    global _pool, _config

    with _lock:
        if _pool is not None:
            return _pool
        resolved = config or load_config()
        # `aios_db.open_pool` waits for the first connections and closes the
        # half-built pool if they fail, so a bad DSN, an unreachable host or a
        # rejected certificate surfaces here rather than on the first request.
        pool = adapter.open_pool(
            resolved.conninfo(),
            min_size=resolved.pool_min_size,
            max_size=resolved.pool_max_size,
            timeout=resolved.pool_timeout_seconds,
            checkout_timeout=resolved.pool_timeout_seconds,
            autocommit=True,
            name="aicc",
        )
        _pool = pool
        _config = resolved
        _LOG.info("postgres pool open: %s", resolved.redacted())
        return pool


def get_pool():
    """Return the open pool, or raise `PoolNotOpenError`."""
    if _pool is None:
        raise PoolNotOpenError(
            "PostgreSQL pool is not open. Call open_pool() during startup."
        )
    return _pool


def close_pool() -> None:
    """Close the pool and drop the cached config. Safe to call when not open."""
    global _pool, _config

    with _lock:
        if _pool is not None:
            _pool.close()
        _pool = None
        _config = None


@contextmanager
def connection() -> Iterator:
    """Check a connection out of the pool for the duration of the block."""
    with get_pool().connection() as conn:
        yield conn


def pool_stats() -> dict[str, int]:
    """Pool counters for the readiness probe and metrics."""
    return adapter.pool_stats(get_pool())
