"""Shared FastAPI dependencies for the MSP and public routers.

A connection borrowed from the pool opened in ``meridian.api.app``'s
lifespan, the ``Settings`` loaded there alongside it, and bearer
authentication. A route declares what it needs and FastAPI resolves it,
rather than every handler reaching into ``request.app.state`` itself.

Stage 3's suggested ``api/dependencies.py`` — the first endpoint to touch the
database (``register``) is what this file exists for; every later one reuses
it rather than repeating the pool-borrowing call.

Unlike ``get_connection`` and ``get_settings``, bearer authentication *is* a
decision — "who does this token belong to" — which is why it is split into a
pure parser (:func:`parse_bearer_token`) and the dependency that calls it,
the same shape ``api/versioning.py`` already uses for ``MSP-Version``.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import cast

from fastapi import Depends, Header, Request

from meridian.api.errors import UNAUTHORIZED, MspError
from meridian.config import Settings
from meridian.registry.psycopg_registry import PsycopgRegistry
from meridian.store.stations import Connection

__all__ = [
    "get_authenticated_station_id",
    "get_connection",
    "get_settings",
    "parse_bearer_token",
]

_BEARER_PREFIX = "Bearer "


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


def parse_bearer_token(header: str | None) -> str:
    """Extract the token from an ``Authorization: Bearer <token>`` header.

    Args:
        header: The raw header value, or ``None`` when absent.

    Returns:
        The token, exactly as presented — hashing it is the caller's job.

    Raises:
        MspError: ``unauthorized`` when the header is missing or does not
            start with ``Bearer ``. MSP §6 defines ``401`` as covering a
            bearer token that is "missing, unknown or revoked" — a
            malformed header is the first of those three.
    """
    if header is None or not header.startswith(_BEARER_PREFIX):
        raise MspError(
            UNAUTHORIZED, "Authorization: Bearer <token> header is required."
        )
    return header.removeprefix(_BEARER_PREFIX).strip()


def get_authenticated_station_id(
    authorization: str | None = Header(default=None),
    conn: Connection = Depends(get_connection),
    settings: Settings = Depends(get_settings),
) -> str:
    """The ``station_id`` a bearer token authenticates as.

    Does **not** check that this identity matches any ``station_id`` a
    request body separately claims — a route with a body decides whether
    that comparison applies to it, and how to report a mismatch.

    Raises:
        MspError: ``unauthorized`` — from :func:`parse_bearer_token`, or
            when the token does not resolve to a live station. The two
            cases are deliberately not distinguished in the response: MSP
            §3 does not let a client learn whether a token is merely
            unknown or was actively revoked.
    """
    token = parse_bearer_token(authorization)
    registry = PsycopgRegistry(
        conn,
        pepper=settings.token_hash_pepper,
        # authenticate() reads neither of these, but PsycopgRegistry is one
        # class with one constructor; passing the real values costs nothing
        # and keeps this call site correct if authentication ever grows a
        # time-dependent rule (an expiring token, say).
        recovery_window_s=settings.registration_recovery_window_s,
        now_utc=datetime.now(UTC),
    )
    station_id = registry.authenticate(token)
    if station_id is None:
        raise MspError(UNAUTHORIZED, "Bearer token is missing, unknown or revoked.")
    return station_id
