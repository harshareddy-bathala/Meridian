"""Serving the built dashboard from the platform's own origin (D-081).

Two routes and nothing else: ``GET /`` answers ``index.html`` and ``/assets/``
answers Vite's hashed build output. There is deliberately no fallback that hands
``index.html`` to any unmatched path — that would answer ``200`` for a mistyped
API URL and, through Starlette's matching order, turn a wrong method on an MSP
route from ``405`` into whatever the fallback says. D-091 records both.

The dashboard never talks to anything but this origin, which is why no CORS
middleware exists anywhere in the repository.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

__all__ = ["DASHBOARD_DIR_ENV", "dashboard_directory", "mount_dashboard"]

_log = logging.getLogger(__name__)

DASHBOARD_DIR_ENV = "DASHBOARD_DIR"
"""Where the image keeps ``dashboard/dist``.

Read when the application is built, not through ``Settings``: routes must exist
before the lifespan that loads settings has run.
"""


def dashboard_directory() -> Path | None:
    """The build directory named by the environment, or ``None`` if unset."""
    configured = os.environ.get(DASHBOARD_DIR_ENV, "").strip()
    return Path(configured) if configured else None


def mount_dashboard(app: FastAPI, directory: Path | None) -> bool:
    """Add the dashboard's two routes when ``directory`` holds a build.

    Returns whether anything was mounted. A checkout run without Node has no
    build, and the platform starts without a dashboard rather than refusing to
    start — the API is the part that must always be there.
    """
    if directory is None:
        return False
    index = directory / "index.html"
    if not index.is_file():
        _log.warning("no dashboard build at %s; serving the API only", directory)
        return False

    @app.get("/", include_in_schema=False)
    def dashboard_index() -> FileResponse:
        # no-cache rather than a max-age: index.html names the hashed assets, so
        # a stale copy keeps a browser on the previous build after a deploy.
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    assets = directory / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="dashboard-assets")
    return True
