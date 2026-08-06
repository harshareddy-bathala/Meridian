"""The ASGI application.

Two surfaces, both mounted here: the MSP endpoints and the public read API. This
module is thin by rule — it wires things together and owns no decisions.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from meridian import __version__
from meridian.api.errors import install_error_handlers
from meridian.api.msp import router as msp_router
from meridian.config import load_settings
from meridian.store.pool import is_database_reachable, open_pool

__all__ = ["create_app"]


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
        summary="Control platform for satellite ground stations.",
        lifespan=_lifespan,
    )

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
