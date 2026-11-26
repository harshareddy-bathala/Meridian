"""``/imagery/{hash}/{path}`` — one file of one published scene (D-376).

**An explicit route, not a static mount** (amends D-091). A mount at
``/imagery`` would serve whatever the directory holds, and would answer for
paths no route claims. This route serves only:

- a file the scene's manifest lists, or the manifest itself;
- of a scene the index lists, whose area is published (D-326);
- while its bytes still have the digest and size the manifest states. A file
  changed after it was published is refused, never served under a name that
  promises other bytes.

Every other path under ``/imagery/`` is ``not_found``, in the public API's
words.

**Outside ``/api/v1``, with a rate bucket of its own.** A viewer asks for a
few dozen tiles at once, which the public bucket's five a second would refuse
(D-202). A scene's files are named by its content and never rewritten, so
they are served ``immutable`` for a year, and an edge cache may keep them; the
front forwards them and never keeps a copy (``deploy/front/nginx.conf``).

The GeoTIFF, the PNG, its world file and the manifest are attachments, for a
reader to keep and open in a GIS tool. The display picture and the tiles are
inline, for the page that draws them. The content-security policy is the
platform's own, unchanged: these are same-origin images (D-208).

Reference: docs/DECISIONS.md D-091, D-202, D-208, D-326, D-369, D-375, D-376.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi import Path as PathParam
from fastapi.responses import FileResponse, Response

from meridian.api.dependencies import get_settings
from meridian.api.public.envelope import NOT_FOUND, SERVER_ERROR, PublicError
from meridian.api.public.imagery import FILES_PREFIX, published_scene
from meridian.api.public.models.imagery import FILE_PATH, HASH12
from meridian.config import Settings

__all__ = ["IMMUTABLE", "router"]

IMMUTABLE = "public, max-age=31536000, immutable"
"""A year, and never revalidated: the name is the content's (D-375)."""

MANIFEST_NAME = "manifest.json"
_ATTACHMENTS = frozenset({"scene.tif", "scene.png", "scene.pgw", MANIFEST_NAME})
_MEDIA_TYPES = {
    ".tif": "image/tiff",
    ".png": "image/png",
    ".webp": "image/webp",
    ".pgw": "text/plain; charset=us-ascii",
    ".txt": "text/plain; charset=utf-8",
    ".json": "application/json",
}
_CHUNK = 1 << 20
_FILE = re.compile(FILE_PATH)

_log = logging.getLogger(__name__)

router = APIRouter(prefix=FILES_PREFIX, tags=["imagery"])


@router.get("/{scene}/{path:path}")
def imagery_file(
    scene: Annotated[str, PathParam(pattern=HASH12)],
    path: str,
    settings: Settings = Depends(get_settings),
) -> Response:
    """One file of a published scene, served while it matches its manifest.

    Raises:
        PublicError: ``not_found`` for a scene or a file nothing lists;
            ``server_error`` when the index or the manifest is refused, or the
            file no longer has the digest its manifest states.
    """
    found = published_scene(settings, scene)
    headers = {"Cache-Control": IMMUTABLE}
    if path in _ATTACHMENTS:
        headers["Content-Disposition"] = (
            f'attachment; filename="{scene}-{path.replace("/", "-")}"'
        )
    target = found.directory / path
    if path == MANIFEST_NAME:
        # Its bytes were hashed to the scene's name as the manifest was read.
        return Response(
            content=_read(target, scene),
            media_type=_MEDIA_TYPES[".json"],
            headers=headers,
        )
    listed = found.manifest.file(path) if _FILE.fullmatch(path) else None
    if listed is None:
        raise PublicError(NOT_FOUND, f"Scene {scene} lists no file {path}.")
    if _digest(target, scene) != (listed.sha256, listed.size_bytes):
        _log.warning("published scene file does not match its manifest: %s", target)
        raise PublicError(SERVER_ERROR, _damaged(scene, path))
    return FileResponse(
        target,
        media_type=_MEDIA_TYPES.get(Path(path).suffix, "application/octet-stream"),
        headers=headers,
    )


def _read(target: Path, scene: str) -> bytes:
    try:
        return target.read_bytes()
    except OSError:
        _log.warning("published scene file unreadable: %s", target, exc_info=True)
        raise PublicError(SERVER_ERROR, _damaged(scene, target.name)) from None


def _digest(target: Path, scene: str) -> tuple[str, int]:
    """The file's sha256 and size, read in pieces so a GeoTIFF is never held whole."""
    digest = hashlib.sha256()
    size = 0
    try:
        with target.open("rb") as handle:
            while chunk := handle.read(_CHUNK):
                digest.update(chunk)
                size += len(chunk)
    except OSError:
        _log.warning("published scene file unreadable: %s", target, exc_info=True)
        raise PublicError(SERVER_ERROR, _damaged(scene, target.name)) from None
    return digest.hexdigest(), size


def _damaged(scene: str, path: str) -> str:
    return f"{scene}/{path} does not match its manifest; the platform's log says why."
