"""Shared FastAPI dependencies for the MSP and public routers.

Two dependencies, neither holding a decision: a connection borrowed from the
pool opened in ``meridian.api.app``'s lifespan, and the ``Settings`` loaded
there alongside it. A route declares what it needs and FastAPI resolves it,
rather than every handler reaching into ``request.app.state`` itself.

Stage 3's suggested ``api/dependencies.py`` — the first endpoint to touch the
database (``register``) is what this file exists for; every later one reuses
it rather than repeating the pool-borrowing call.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import cast

from fastapi import Request

from meridian.config import Settings
from meridian.store.stations import Connection

__all__ = ["get_connection", "get_settings"]


def get_connection(request: Request) -> Iterator[Connection]:
    """Borrow one connection from the pool for the lifetime of this request.

    The same ``pool.connection()`` call ``meridian.store.pool.is_database_reachable``
    already uses — the connection is returned to the pool when the request
    finishes, whether it succeeded or raised.
    """
    with request.app.state.pool.connection() as conn:
        yield conn


def get_settings(request: Request) -> Settings:
    """The ``Settings`` loaded once at startup, by ``meridian.api.app``'s lifespan."""
    # Starlette's State.__getattr__ returns Any; cast to the concrete type
    # rather than let it flow through — mypy's disallow_any_explicit exists
    # to catch exactly this kind of unchecked value (D-044).
    return cast(Settings, request.app.state.settings)
