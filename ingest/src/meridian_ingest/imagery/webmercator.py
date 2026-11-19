"""The Web Mercator tile grid a scene's pyramid is cut on, as arithmetic.

The viewer asks for tiles by ``z/x/y`` in the scheme every web map shares:
EPSG:3857, 256-pixel tiles, ``y`` counted down from the north edge at
85.05°. A tile's bounds are a few lines of arithmetic, written here and
checked against tiles whose numbers are known, rather than taken from a
tiling library (D-371).

**The zoom range comes from the scene.** The deepest zoom is the first whose
pixel, at the box's centre latitude, is no larger than the scene's own pixel,
so the deepest tiles show every pixel the archive gave. Three zooms above it
are cut too: each halves the detail, and three show the area within some
eight times its width of surroundings, which is enough to find it on a map.

Stdlib only: nothing here reads a pixel.

Reference: docs/DECISIONS.md D-371, D-373.
"""

from __future__ import annotations

import math

from meridian_ingest.imagery.model import Bounds

__all__ = [
    "HALF_WORLD_M",
    "MAX_ZOOM",
    "TILE_PX",
    "ZOOMS_ABOVE",
    "tile_bounds",
    "tile_of",
    "tiles_covering",
    "zoom_range",
]

TILE_PX = 256
HALF_WORLD_M = math.pi * 6_378_137.0
"""Half the width of the Web Mercator world, in metres: π times the sphere's radius."""

MAX_LAT = 85.051_128_779_806_59
"""Where the square world ends: the latitude whose Mercator ``y`` is ``π``."""

MAX_ZOOM = 18
"""No archive this pipeline reads is finer than a pixel at zoom 18 (about
0.6 m at the equator), so a deeper zoom would only enlarge pixels."""

ZOOMS_ABOVE = 3
"""Zooms cut above the deepest: an eightfold zoom-out around the area."""

_EDGE = 1e-9


def tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """A tile's left, bottom, right and top, in EPSG:3857 metres."""
    span = 2 * HALF_WORLD_M / 2**z
    left = -HALF_WORLD_M + x * span
    top = HALF_WORLD_M - y * span
    return left, top - span, left + span, top


def tile_of(lon_deg: float, lat_deg: float, z: int) -> tuple[int, int]:
    """The ``(x, y)`` of the tile a point falls in at zoom ``z``.

    A point on a tile boundary belongs to the tile east of it and south of
    it, as a pixel's top-left corner belongs to that pixel.
    """
    n = 2**z
    lat = max(-MAX_LAT, min(MAX_LAT, lat_deg))
    fx = (lon_deg + 180.0) / 360.0 * n
    fy = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return (
        min(n - 1, max(0, math.floor(fx + _EDGE))),
        min(n - 1, max(0, math.floor(fy + _EDGE))),
    )


def tiles_covering(bounds: Bounds, z: int) -> list[tuple[int, int]]:
    """Every ``(x, y)`` the box touches at zoom ``z``, row by row from the north."""
    west, north = tile_of(bounds.west, bounds.north, z)
    east, south = tile_of(bounds.east - _EDGE, bounds.south + _EDGE, z)
    return [(x, y) for y in range(north, south + 1) for x in range(west, east + 1)]


def zoom_range(resolution_m: float, lat_deg: float) -> tuple[int, int]:
    """The zooms a scene's pyramid is cut at, from its own pixel size.

    Raises:
        ValueError: ``resolution_m`` is not a positive number.
    """
    if not resolution_m > 0:
        message = f"a scene's resolution must be positive, not {resolution_m}"
        raise ValueError(message)
    at_zoom_zero = 2 * HALF_WORLD_M / TILE_PX * math.cos(math.radians(lat_deg))
    deepest = math.ceil(math.log2(at_zoom_zero / resolution_m) - _EDGE)
    deepest = max(0, min(MAX_ZOOM, deepest))
    return max(0, deepest - ZOOMS_ABOVE), deepest
