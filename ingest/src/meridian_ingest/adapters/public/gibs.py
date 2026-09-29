"""Near-real-time global imagery: NASA GIBS true-colour tiles, for display only.

**What it is for.** A picture behind an area on the dashboard, so a reader
sees the scene a number describes — labelled as imagery, and never sampled for
a value (D-133). Every number beside it comes from a data product.

**Format.** WMTS REST tiles in the geographic (EPSG:4326) tile matrix set,
JPEG. At level ``z`` a tile spans ``288 / 2**z`` degrees: level 0 is two tiles
of 288° side covering the globe from its north-west corner, and each level
halves the span. A time of ``default`` asks for the most recent day.

Reference: docs/DECISIONS.md D-133, D-220.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta

from meridian_ingest.adapters.protocol import FetchRequest, SourceDescriptor
from meridian_ingest.adapters.public.common import (
    days_between,
    require_bbox,
    version_from_headers,
)
from meridian_ingest.extent import BoundingBox
from meridian_ingest.retrieval import RemoteArtefact, RetrievedArtefact

__all__ = ["GIBS", "LEVEL", "GibsAdapter", "tile_span_deg", "tiles_covering"]

GIBS = SourceDescriptor(
    source_id="nasa_gibs",
    source_class="imagery",
    name="NASA GIBS WMTS imagery tiles (display only)",
    licence="NASA open data; acknowledgement of GIBS/EOSDIS requested",
    terms_url="https://nasa-gibs.github.io/gibs-api-docs/",
    attribution_entry="NASA GIBS imagery tiles",
    access_constraint="none",
)

BASE = "https://gibs.earthdata.nasa.gov/wmts/epsg4326/best"
DEFAULT_LAYER = "MODIS_Terra_CorrectedReflectance_TrueColor"
LEVEL = 6
"""4.5° tiles: an area of interest is a handful of them, and the picture is
context, not evidence."""

_LEVEL_ZERO_SPAN = 288.0


def tile_span_deg(level: int) -> float:
    """The side of one tile at ``level``, in degrees."""
    return _LEVEL_ZERO_SPAN / float(2**level)


def tiles_covering(bbox: BoundingBox, level: int) -> list[tuple[int, int]]:
    """Every ``(row, column)`` a box touches at ``level``, row-major."""
    span = tile_span_deg(level)
    first_col = math.floor((bbox.west + 180.0) / span)
    last_col = math.floor((bbox.east + 180.0 - 1e-9) / span)
    first_row = math.floor((90.0 - bbox.north) / span)
    last_row = math.floor((90.0 - bbox.south - 1e-9) / span)
    return [
        (row, col)
        for row in range(first_row, last_row + 1)
        for col in range(first_col, last_col + 1)
    ]


class GibsAdapter:
    """Tiles of each configured layer over the configured box."""

    @property
    def descriptor(self) -> SourceDescriptor:
        """What this source is, and what it may be used under."""
        return GIBS

    def plan(self, request: FetchRequest) -> tuple[RemoteArtefact, ...]:
        """Each layer, day and tile, as tiles — never as data.

        Raises:
            ValueError: No box is configured.
        """
        bbox = require_bbox(request, GIBS.source_id)
        layers = request.layers or (DEFAULT_LAYER,)
        days: list[date | None] = (
            [None]
            if request.since is None or request.until is None
            else list(days_between(request.since, request.until))
        )
        planned = [
            _tile(layer, day, row, col)
            for layer in layers
            for day in days
            for row, col in tiles_covering(bbox, LEVEL)
        ]
        return tuple(planned[: request.limit])

    def source_version(self, retrieved: RetrievedArtefact) -> str:
        """The tile server's validator, or when it served the tile."""
        return version_from_headers(retrieved)


def _tile(layer: str, day: date | None, row: int, col: int) -> RemoteArtefact:
    stamp = "default" if day is None else day.isoformat()
    start = None if day is None else datetime(day.year, day.month, day.day, tzinfo=UTC)
    return RemoteArtefact(
        url=f"{BASE}/{layer}/default/{stamp}/250m/{LEVEL}/{row}/{col}.jpg",
        original_identifier=f"{layer}/{stamp}/{LEVEL}/{row}/{col}",
        payload_kind="tile",
        valid_from=start,
        valid_to=None if start is None else start + timedelta(days=1),
    )
