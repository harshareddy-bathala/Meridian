"""``/api/v1/imagery`` — published open imagery of the published areas.

``/imagery`` lists the scenes ``IMAGERY_INDEX`` names, newest acquisition
first, paged by keyset (D-085); ``/imagery/{hash}`` is one scene's manifest:
its caption, markers, bounds, zooms and files. The files themselves are served
at ``/imagery/{hash}/{path}``, outside ``/api/v1`` (``meridian.api.imagery_files``).

**Only a published area's scenes are listed.** A scene is listed while its
area is in the summary ``AREAS_SUMMARY`` names and ``AREAS_PUBLISHED`` is on,
and not otherwise: an area taken down takes its pictures with it (D-326).
Without the index, or without published areas, the answer is
``not_yet_computed``, with no field a chart could read (D-086).

**Checked, not trusted** (D-332, D-369). The index is read whole: a key it
does not know, or a scene listed twice, is a ``server_error`` naming the
setting, never part of a list. Each manifest is read whole too, and must hash
to the name it is published under and describe the scene its index entry says.
A parsed manifest is kept for as long as its file's size and time are
unchanged, so a viewer's thirty tile requests do not parse it thirty times.

Every scene is public archive imagery, credited, except the demonstration's
synthetic one, which says it is simulated in its index entry, its manifest and
every answer here (rule 5, D-378). Images received by Meridian's own stations
arrive with Stage 40.

Reference: docs/DECISIONS.md D-085, D-086, D-326, D-332, D-369, D-375, D-376.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi import Path as PathParam
from pydantic import Field, ValidationError

from meridian.api.dependencies import get_settings
from meridian.api.public.areas import published_area_ids
from meridian.api.public.envelope import (
    INVALID_QUERY,
    NOT_FOUND,
    SERVER_ERROR,
    PublicError,
)
from meridian.api.public.models.imagery import (
    HASH12,
    ImageryIndexEntry,
    ImageryIndexFile,
    ImageryList,
    ImageryScene,
    ImagerySceneDetail,
    SceneManifest,
    SourceClass,
)
from meridian.api.public.models.not_yet_computed import NotYetComputed
from meridian.api.public.pagination import (
    PageRequest,
    encode_cursor,
    page_request,
    trim_overfetch,
)
from meridian.config import Settings

__all__ = [
    "FILES_PREFIX",
    "IMAGERY_STAGE",
    "Imagery",
    "PublishedScene",
    "published_scene",
    "router",
]

IMAGERY_STAGE = 38
"""Stage 38 — open imagery, placed and explained."""

FILES_PREFIX = "/imagery"
"""Where a scene's files are served: ``/imagery/<hash>/<path>``."""

MANIFEST_NAME = "manifest.json"

Imagery = Annotated[ImageryList | NotYetComputed, Field(discriminator="status")]
SceneDetail = Annotated[
    ImagerySceneDetail | NotYetComputed, Field(discriminator="status")
]

_log = logging.getLogger(__name__)

router = APIRouter()

_NONE_YET = (
    "No open imagery is published yet: a deployment publishes it once its areas "
    "are public and the imagery index is named (D-326, D-375)."
)
_REFUSED = (
    "The imagery index IMAGERY_INDEX names was refused; the platform's log says why."
)
_SortKey = tuple[float, int, str, str]
_CURSOR_PARTS = ("acquired_at", "area_id", "source_class", "hash")


@router.get("/imagery")
def list_imagery(  # noqa: PLR0913 — the page and four filters
    page: Annotated[PageRequest, Depends(page_request)],
    *,
    area: Annotated[int | None, Query(ge=1)] = None,
    source_class: SourceClass | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    settings: Settings = Depends(get_settings),
) -> Imagery:
    """Published scenes of the published areas, newest acquisition first.

    ``since`` and ``until`` bound the acquisition time, both inclusive, and
    carry a time zone. ``source_class`` is ``high_resolution`` (Sentinel-2),
    ``older_record`` (Landsat) or ``daily_wide`` (GIBS).

    Raises:
        PublicError: ``invalid_query`` for a time without a zone, or a span
            that ends before it starts; ``server_error`` when the index or a
            manifest it lists is refused.
    """
    for name, value in (("since", since), ("until", until)):
        if value is not None and value.utcoffset() is None:
            raise PublicError(INVALID_QUERY, f"{name} must carry a time zone.")
    if since is not None and until is not None and until < since:
        raise PublicError(INVALID_QUERY, "until is before since.")
    held = _held(settings)
    if held is None:
        return NotYetComputed(reason=_NONE_YET, available_from_stage=IMAGERY_STAGE)
    root, index, areas = held
    after = _cursor_key(page.cursor_key)
    chosen = sorted(
        (
            one
            for one in index.scenes
            if one.area_id in areas
            and (area is None or one.area_id == area)
            and (source_class is None or one.source_class == source_class)
            and (since is None or one.acquired_at >= since)
            and (until is None or one.acquired_at <= until)
            and (after is None or _sort_key(one) > after)
        ),
        key=_sort_key,
    )
    trimmed = trim_overfetch(chosen[: page.limit + 1], page.limit)
    items = [_scene(one, _manifest(root, one)) for one in trimmed.rows]
    last = trimmed.rows[-1] if trimmed.rows and trimmed.has_more_rows else None
    return ImageryList(
        items=items,
        next_cursor=None if last is None else encode_cursor(_cursor_parts(last)),
    )


@router.get("/imagery/{scene}")
def imagery_scene(
    scene: Annotated[str, PathParam(pattern=HASH12)],
    settings: Settings = Depends(get_settings),
) -> SceneDetail:
    """One published scene: its manifest, and where its files are served.

    Raises:
        PublicError: ``not_found`` for a scene the index does not list, or one
            whose area is not published; ``server_error`` when the index or the
            manifest is refused.
    """
    held = _held(settings)
    if held is None:
        return NotYetComputed(reason=_NONE_YET, available_from_stage=IMAGERY_STAGE)
    found = _published(held, scene)
    return ImagerySceneDetail(
        hash=found.entry.hash,
        published_at=found.entry.published_at,
        base=f"{FILES_PREFIX}/{found.entry.hash}/",
        manifest=found.manifest,
        simulated=found.manifest.simulated,
    )


class PublishedScene:
    """A listed scene of a published area, its manifest, and where it lives."""

    def __init__(
        self, directory: Path, entry: ImageryIndexEntry, manifest: SceneManifest
    ) -> None:
        """Hold what was read."""
        self.directory = directory
        self.entry = entry
        self.manifest = manifest


def published_scene(settings: Settings, scene: str) -> PublishedScene:
    """The scene named ``scene``, read and checked, for the file route.

    Raises:
        PublicError: ``not_found`` when nothing is published, the index does
            not list it, or its area is not published; ``server_error`` when
            the index or the manifest is refused.
    """
    held = _held(settings)
    if held is None:
        raise PublicError(NOT_FOUND, "No open imagery is published yet.")
    return _published(held, scene)


# --- reading -----------------------------------------------------------------------

_Held = tuple[Path, ImageryIndexFile, frozenset[int]]


def _held(settings: Settings) -> _Held | None:
    """The index and the published areas, or ``None`` when either is missing."""
    if not settings.imagery_index:
        return None
    areas = published_area_ids()
    if areas is None:
        return None
    path = Path(settings.imagery_index)
    try:
        index = ImageryIndexFile.model_validate_json(path.read_bytes())
    except (OSError, ValidationError):
        _log.warning("imagery index refused", exc_info=True)
        raise PublicError(SERVER_ERROR, _REFUSED) from None
    return path.parent, index, areas


def _published(held: _Held, scene: str) -> PublishedScene:
    root, index, areas = held
    entry = next((one for one in index.scenes if one.hash == scene), None)
    if entry is None or entry.area_id not in areas:
        raise PublicError(NOT_FOUND, f"No published scene is named {scene}.")
    return PublishedScene(root / entry.hash, entry, _manifest(root, entry))


def _manifest(root: Path, entry: ImageryIndexEntry) -> SceneManifest:
    """The entry's manifest, checked against its name and its entry."""
    path = root / entry.hash / MANIFEST_NAME
    try:
        status = path.stat()
        manifest = _read_manifest(str(path), status.st_mtime_ns, status.st_size)
    except (OSError, ValueError):
        _log.warning("imagery manifest refused: %s", path, exc_info=True)
        raise PublicError(SERVER_ERROR, _damaged(entry.hash)) from None
    if (
        manifest.area_id,
        manifest.source_id,
        manifest.source_class,
        manifest.acquired_at,
        manifest.simulated,
    ) != (
        entry.area_id,
        entry.source_id,
        entry.source_class,
        entry.acquired_at,
        entry.simulated,
    ):
        _log.warning("imagery manifest is not its index entry's scene: %s", path)
        raise PublicError(SERVER_ERROR, _damaged(entry.hash))
    return manifest


@lru_cache(maxsize=256)
def _read_manifest(path: str, mtime_ns: int, size: int) -> SceneManifest:
    """A manifest read whole, once per version of its file.

    Raises:
        ValueError: Its bytes do not hash to the name it is published under,
            or it is not an ``imagery-manifest-1`` manifest.
    """
    del mtime_ns  # part of the cache key only
    data = Path(path).read_bytes()
    if len(data) != size:
        raise ValueError(f"{path} changed while it was read")
    name = Path(path).parent.name
    if hashlib.sha256(data).hexdigest()[:12] != name:
        raise ValueError(f"{path} does not hash to {name}")
    return SceneManifest.model_validate_json(data)


def _scene(entry: ImageryIndexEntry, manifest: SceneManifest) -> ImageryScene:
    return ImageryScene(
        hash=entry.hash,
        area_id=entry.area_id,
        area_label=manifest.area_label,
        source_class=entry.source_class,
        source_id=entry.source_id,
        platform=manifest.platform,
        instrument=manifest.instrument,
        acquired_at=entry.acquired_at,
        published_at=entry.published_at,
        cloud_fraction=manifest.cloud_fraction,
        caption=manifest.caption,
        credit=manifest.credit,
        display=f"{FILES_PREFIX}/{entry.hash}/display.png",
        size_bytes=entry.size_bytes,
        simulated=entry.simulated,
    )


# --- the keyset ---------------------------------------------------------------------


def _sort_key(entry: ImageryIndexEntry) -> _SortKey:
    """Newest acquisition first, then by area, class and name."""
    return (
        -entry.acquired_at.timestamp(),
        entry.area_id,
        entry.source_class,
        entry.hash,
    )


def _cursor_parts(entry: ImageryIndexEntry) -> tuple[str, ...]:
    return (
        entry.acquired_at.isoformat(),
        str(entry.area_id),
        entry.source_class,
        entry.hash,
    )


def _cursor_key(parts: tuple[str, ...] | None) -> _SortKey | None:
    """The sort key a cursor carries, refusing one another endpoint issued."""
    if parts is None:
        return None
    refused = PublicError(INVALID_QUERY, "cursor is not a valid cursor.")
    if len(parts) != len(_CURSOR_PARTS) or not parts[1].isdigit():
        raise refused
    acquired, area, source_class, name = parts
    try:
        at = datetime.fromisoformat(acquired)
    except ValueError:
        raise refused from None
    if at.utcoffset() is None:
        raise refused
    return (-at.timestamp(), int(area), source_class, name)


def _damaged(name: str) -> str:
    return f"The published scene {name} was refused; the platform's log says why."
