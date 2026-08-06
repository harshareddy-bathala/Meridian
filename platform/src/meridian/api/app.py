"""The ASGI application.

Two surfaces, both mounted here: the MSP endpoints and the public read API. This
module is thin by rule — it wires things together and owns no decisions.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from meridian import __version__
from meridian.config import Settings, load_settings

__all__ = ["create_app"]


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Settings are loaded at startup, not import, so a misconfiguration fails
    # loudly with the process rather than silently at first request.
    app.state.settings = load_settings()
    yield


def _database_ok(settings: Settings) -> bool:
    # Any failure to reach the database is "not ok" — a health check that
    # propagates the exception is a health check that returns 500 instead of
    # reporting degradation, which is the opposite of what it is for.
    #
    # psycopg_url rather than database_url: one DATABASE_URL serves both this and
    # Alembic, and libpq rejects the "+psycopg" driver suffix SQLAlchemy needs
    # (D-033).
    url = settings.psycopg_url
    try:
        with psycopg.connect(url, connect_timeout=2) as conn, conn.cursor() as cur:
            cur.execute("select 1")
            return cur.fetchone() is not None
    except (psycopg.Error, OSError):
        # psycopg.Error covers refused connections, authentication failures and
        # timeouts; OSError covers the DNS and socket failures underneath it. A
        # bare `except Exception` would also swallow a TypeError in this
        # function, and a health check reporting "database unreachable" because
        # our own code is broken hides the outage it exists to surface.
        return False


def create_app() -> FastAPI:
    """Build the application.

    A factory rather than a module-level singleton so tests can construct an app
    against a different environment without reimporting the module.
    """
    app = FastAPI(
        title="Meridian",
        version=__version__,
        summary="Control platform for satellite ground stations.",
        lifespan=_lifespan,
    )

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        """Liveness and dependency check.

        This is the endpoint the tunnel is pointed at first and the one the
        scheduled public-reachability check curls, so it must answer without
        authentication and without depending on any station having registered.
        """
        settings: Settings = app.state.settings
        database_ok = _database_ok(settings)
        # dict[str, str], not dict[str, Any]. All three values are strings, and a
        # response body typed Any is a body whose shape nothing checks — which is
        # the case mypy's disallow_any_explicit exists to catch (D-044).
        body: dict[str, str] = {
            "status": "ok" if database_ok else "degraded",
            "version": __version__,
            "database": "ok" if database_ok else "unreachable",
        }
        return JSONResponse(body, status_code=200 if database_ok else 503)

    @app.get("/metrics")
    def metrics() -> Response:
        """Prometheus scrape endpoint."""
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
