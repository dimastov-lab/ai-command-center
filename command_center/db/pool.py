"""Connection pooling for the AICC server database.

A pool rather than per-request connections because PostgreSQL forks a backend
process per connection: at the request rates the dispatcher and the worker
fleet generate, connect-per-query would spend more time forking backends than
running queries, and an unbounded connection count is the standard way to take
a PostgreSQL server down.

The pool is opened explicitly (`open_pool`) and closed explicitly
(`close_pool`) rather than lazily on first use, so a process that cannot reach
the database fails at startup — where an operator sees it — instead of on the
first request that happens to need data.

`connection()` yields a connection with autocommit on. Transactions are opened
deliberately via `conn.transaction()` at the call site; an implicit transaction
per checkout is how a long-lived pooled connection ends up holding an idle
transaction open and blocking VACUUM.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager

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

    from psycopg_pool import ConnectionPool

    with _lock:
        if _pool is not None:
            return _pool
        resolved = config or load_config()
        pool = ConnectionPool(
            conninfo=resolved.conninfo(),
            min_size=resolved.pool_min_size,
            max_size=resolved.pool_max_size,
            timeout=resolved.pool_timeout_seconds,
            kwargs={"autocommit": True},
            open=False,
            name="aicc",
        )
        # `wait` performs the first connections now, so a bad DSN, an
        # unreachable host or a rejected certificate surfaces here.
        pool.open(wait=True, timeout=resolved.pool_timeout_seconds)
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
    stats = get_pool().get_stats()
    return {
        "pool_size": int(stats.get("pool_size", 0)),
        "pool_available": int(stats.get("pool_available", 0)),
        "requests_waiting": int(stats.get("requests_waiting", 0)),
    }
