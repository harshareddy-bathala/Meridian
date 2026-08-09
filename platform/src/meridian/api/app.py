"""The ASGI application.

Two surfaces, both mounted here: the MSP endpoints and the public read API. This
module is thin by rule — it wires things together and owns no decisions.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from psycopg import Connection
from psycopg_pool import ConnectionPool

from meridian import __version__
from meridian.api.errors import install_error_handlers
from meridian.api.msp import router as msp_router
from meridian.api.request_limits import RequestSizeLimitMiddleware
from meridian.config import Settings, load_settings
from meridian.store.invites import seed_bootstrap_invite
from meridian.store.pool import POOL_TIMEOUT_S, is_database_reachable, open_pool

__all__ = ["create_app"]

_log = logging.getLogger(__name__)


def _seed_bootstrap_invite(
    pool: ConnectionPool[Connection[tuple[object, ...]]], settings: Settings
) -> None:
    """Best-effort D-020 seeding: one invite from ``REGISTRATION_INVITE_TOKEN``.

    A database that is not reachable yet must not crash startup — the pool
    already opens without waiting (see :func:`open_pool`), and ``/healthz``
    exists to surface exactly this degradation. The next restart, and every
    heartbeat of ``/healthz`` in between, tries again.
    """
    try:
        with pool.connection(timeout=POOL_TIMEOUT_S) as conn:
            seed_bootstrap_invite(conn, token=settings.registration_invite_token)
    except (psycopg.Error, OSError):
        _log.warning("could not seed the bootstrap invite; will retry on next startup")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Settings are loaded at startup, not import, so a misconfiguration fails
    # loudly with the process rather than silently at first request.
    settings = load_settings()
    app.state.settings = settings

    # One pool for the process, opened here and closed on the way out. Before
    # this the health check opened a fresh connection per request, which is a TCP
    # connect, an authentication round trip and a teardown on the endpoint the
    # tunnel polls most often.
    app.state.pool = open_pool(settings)
    _seed_bootstrap_invite(app.state.pool, settings)
    try:
        yield
    finally:
        # In a finally, so a startup failure further down still returns the
        # connections rather than leaving backends alive on the Pi until the
        # server times them out.
        app.state.pool.close()


def create_app() -> FastAPI:
    """Build the application.

    A factory rather than a module-level singleton so tests can construct an app
    against a different environment without reimporting the module.
    """
    app = FastAPI(
        title="Meridian",
        version=__version__,
        summary="Predictive scheduling and reliability for satellite ground stations.",
        lifespan=_lifespan,
    )

    # Outermost, so an oversized body is refused before routing, before
    # validation and before anything allocates it — which is what MSP §6's
    # "before the body is parsed" and D-028's "ahead of JSON parsing" require.
    app.add_middleware(RequestSizeLimitMiddleware)

    # Before the routes, so a failure inside one already leaves in MSP §6's shape
    # rather than in FastAPI's default 422 or a bare 500.
    install_error_handlers(app)
    app.include_router(msp_router)

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        """Liveness and dependency check.

        This is the endpoint the tunnel is pointed at first and the one the
        scheduled public-reachability check curls, so it must answer without
        authentication and without depending on any station having registered.
        """
        database_ok = is_database_reachable(app.state.pool)
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
