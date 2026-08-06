"""The platform's one connection pool.

Every module that reaches PostgreSQL borrows a connection from the pool opened
here. The pool is opened once, in the API's lifespan, and closed when the process
stops — not per request, which is what the health check did before and which cost
a TCP connect, a TLS handshake and an authentication round trip on an endpoint
whose whole job is to answer quickly.

This module sits at the bottom of ``meridian.store``. It contains **no SQL** and
makes **no domain decisions**: it opens connections, hands them out, and closes
them. What to run on one belongs to the store modules beside it; what the answer
means belongs to the services above them.

Connections are handed out already configured for this project's conventions —
see :data:`SESSION_PARAMETERS` for the one that matters, which is that the session
time zone is UTC.

Reference: psycopg 3 connection pools,
https://www.psycopg.org/psycopg3/docs/advanced/pool.html
"""

from __future__ import annotations

import psycopg
from psycopg import Connection
from psycopg_pool import ConnectionPool

from meridian.config import Settings

__all__ = [
    "POOL_MAX_SIZE",
    "POOL_MIN_SIZE",
    "SESSION_PARAMETERS",
    "is_database_reachable",
    "open_pool",
]

POOL_MIN_SIZE = 1
"""Connections opened before the pool reports itself ready.

One, so that startup does not wait on a database that may still be starting.
``deploy/docker-compose.yml`` already orders ``db`` before ``api`` with a health
check, and the ten-minute bring-up requirement is not helped by the API blocking
a second time on the same condition.
"""

POOL_MAX_SIZE = 8
"""Ceiling on concurrent connections held by one API process.

Sized from the load Phase 3 actually produces, not from a default. Fifty
simulated stations at D-030's thirty-second poll interval is 1.7 heartbeats per
second; each is one short transaction. Eight gives roughly a 4x margin over that
while staying far below the Pi's memory budget for backend processes — PostgreSQL
forks one per connection, and a pool sized for a server is how a Raspberry Pi runs
out of RAM serving fifty stations it could have served with eight.
"""

POOL_TIMEOUT_S = 5.0
"""How long a caller waits for a free connection before giving up.

Bounded rather than infinite: a request that waits forever for a connection is a
request that never returns an error, and a station whose heartbeat never completes
cannot fall back to holding the work it already has (MSP §4.2). Failing at five
seconds lets the client retry on its next heartbeat thirty seconds later.
"""

CONNECT_TIMEOUT_S = 2
"""libpq connect timeout, in whole seconds — libpq rejects a fractional value."""

SESSION_PARAMETERS = {"timezone": "UTC"}
"""Applied to every connection the pool hands out.

**UTC is not the default and must not be left to one.** ``DATA-MODEL.md`` says
"all timestamps UTC, no exceptions"; a session inheriting the server's local zone
would return ``timestamptz`` values converted into it, and every naive comparison
downstream would be wrong by an offset that changes twice a year. Ruff's DTZ rules
enforce this in our own code — this enforces it on the way out of the database.
"""


def open_pool(settings: Settings) -> ConnectionPool[Connection[tuple[object, ...]]]:
    """Open the process-wide connection pool.

    Returns immediately without waiting for the first connection: an API that
    refuses to start because the database is not up yet cannot serve the
    ``/healthz`` that would report the database is not up yet.

    Args:
        settings: Runtime configuration. Only ``psycopg_url`` is read, which is
            ``DATABASE_URL`` with the ``+psycopg`` driver suffix stripped — libpq
            rejects it and Alembic requires it, from the one variable (D-033).

    Returns:
        An open pool. The caller owns closing it; ``meridian.api.app`` does that
        in its lifespan.
    """
    pool: ConnectionPool[Connection[tuple[object, ...]]] = ConnectionPool(
        conninfo=settings.psycopg_url,
        min_size=POOL_MIN_SIZE,
        max_size=POOL_MAX_SIZE,
        timeout=POOL_TIMEOUT_S,
        kwargs={
            "connect_timeout": CONNECT_TIMEOUT_S,
            "options": _session_options(),
        },
        # open=False then open(wait=False), rather than opening in the
        # constructor: the constructor form is deprecated in psycopg_pool 3.2,
        # and it is the form that blocks.
        open=False,
    )
    pool.open(wait=False)
    return pool


def _session_options() -> str:
    """``SESSION_PARAMETERS`` as a libpq ``options`` string."""
    return " ".join(f"-c {name}={value}" for name, value in SESSION_PARAMETERS.items())


def is_database_reachable(pool: ConnectionPool[Connection[tuple[object, ...]]]) -> bool:
    """Whether a pooled connection can complete a trivial query.

    I/O only — it answers "did the database respond", never "is the platform
    healthy". Mapping that to a status and a status code is the API's job.

    Every failure is reported as unreachable rather than raised. A health check
    that propagates its exception returns 500 instead of reporting degradation,
    which is the opposite of what it is for.

    Args:
        pool: The pool to borrow from.

    Returns:
        ``True`` if the database answered, ``False`` on any connection, timeout
        or query failure.
    """
    try:
        with pool.connection(timeout=POOL_TIMEOUT_S) as conn, conn.cursor() as cur:
            cur.execute("select 1")
            return cur.fetchone() is not None
    except (psycopg.Error, OSError):
        # Narrow on purpose, and narrower than it looks: psycopg_pool's
        # PoolTimeout, PoolClosed and TooManyRequests all derive from
        # psycopg.OperationalError, so `psycopg.Error` already covers exhaustion
        # and a closed pool as well as every driver failure. OSError covers the
        # DNS and socket failures underneath them.
        #
        # `except Exception` would additionally swallow a TypeError in this
        # function, and a health check reporting "database unreachable" because
        # our own code is broken hides the outage it exists to surface.
        return False
