"""An area of interest's shape: a polygon on the globe, and the questions asked of it.

A polygon is one ring of longitude–latitude vertices, as GeoJSON writes it.
Four questions are asked of it: is a point inside, how far is a point from it,
where is its centre, and how big is it. None of them needs a GIS library at
the scale areas are registered at — a district, a reserve, a city — so they
are answered here, with the approximation each one makes stated beside it.

**Distances and areas use a local equirectangular projection**: degrees of
longitude are shrunk by the cosine of the latitude they are measured at. Over
a few hundred kilometres its error is well under a percent, far inside the
swath half-widths and cell sizes the answers are compared against (D-230).

**Refused, not repaired:** a ring with fewer than three distinct corners, a
vertex off the globe, a ring crossing the antimeridian, and a ring that crosses
itself. A polygon an operator did not mean would give a series about ground
nobody asked about.

Standard library only; nothing here reads a file, a clock or a database.

Reference: docs/DECISIONS.md D-227, D-230.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from itertools import pairwise

__all__ = ["KM_PER_DEG", "GeometryError", "Polygon"]

KM_PER_DEG = 111.195
"""Kilometres per degree of latitude on the mean sphere (radius 6371 km)."""

_MIN_CORNERS = 3
_MAX_CORNERS = 1000
_MAX_LON_SPAN = 180.0
_PAIR = 2


class GeometryError(ValueError):
    """A shape that is not one polygon on the globe."""


@dataclass(frozen=True, slots=True)
class Polygon:
    """One closed ring of ``(lon_deg, lat_deg)`` vertices, first equal to last.

    Raises:
        GeometryError: See the module's list of refusals.
    """

    ring: tuple[tuple[float, float], ...]

    def __post_init__(self) -> None:
        """Refuse a ring that is not one simple polygon on the globe."""
        _check_ring(self.ring)

    @classmethod
    def from_bbox(cls, west: float, south: float, east: float, north: float) -> Polygon:
        """A rectangle in degrees, as ``west, south, east, north``."""
        return cls(
            ((west, south), (east, south), (east, north), (west, north), (west, south))
        )

    @classmethod
    def from_geojson(cls, value: object) -> Polygon:
        """A GeoJSON Polygon with one ring, or a Feature holding one.

        Raises:
            GeometryError: It is not that.
        """
        if isinstance(value, dict) and value.get("type") == "Feature":
            value = value.get("geometry")
        if not isinstance(value, dict) or value.get("type") != "Polygon":
            message = "an area is a GeoJSON Polygon"
            raise GeometryError(message)
        rings = value.get("coordinates")
        if not isinstance(rings, list) or len(rings) != 1:
            message = "an area is one ring, with no holes"
            raise GeometryError(message)
        return cls(tuple(_vertex(one) for one in _as_list(rings[0])))

    def to_geojson(self) -> dict[str, object]:
        """The GeoJSON Polygon, vertices as lists."""
        return {"type": "Polygon", "coordinates": [[list(one) for one in self.ring]]}

    @property
    def sha256(self) -> bytes:
        """The digest that names this exact shape."""
        text = json.dumps(self.to_geojson(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).digest()

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """``west, south, east, north`` of the ring."""
        lons = [one[0] for one in self.ring]
        lats = [one[1] for one in self.ring]
        return min(lons), min(lats), max(lons), max(lats)

    def centroid(self) -> tuple[float, float]:
        """The area-weighted centre, as ``(lat_deg, lon_deg)``."""
        lat0 = _mean_lat(self.ring)
        points = [_project(lon, lat, lat0) for lon, lat in self.ring]
        twice, cx, cy = 0.0, 0.0, 0.0
        for (x1, y1), (x2, y2) in pairwise(points):
            cross = x1 * y2 - x2 * y1
            twice += cross
            cx += (x1 + x2) * cross
            cy += (y1 + y2) * cross
        x, y = cx / (3.0 * twice), cy / (3.0 * twice)
        return y / KM_PER_DEG, x / (KM_PER_DEG * math.cos(math.radians(lat0)))

    def area_km2(self) -> float:
        """Its area, in square kilometres, by the shoelace formula projected."""
        lat0 = _mean_lat(self.ring)
        points = [_project(lon, lat, lat0) for lon, lat in self.ring]
        twice = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in pairwise(points))
        return abs(twice) / 2.0

    def contains(self, lat_deg: float, lon_deg: float) -> bool:
        """Whether a point is inside, by counting edge crossings.

        A point exactly on an edge may fall either way; at the resolution of
        any product ingested here that is a coin toss about a boundary nobody
        drew to the metre.
        """
        inside = False
        for (x1, y1), (x2, y2) in pairwise(self.ring):
            if (y1 > lat_deg) != (y2 > lat_deg):
                crossing = x1 + (lat_deg - y1) * (x2 - x1) / (y2 - y1)
                if lon_deg < crossing:
                    inside = not inside
        return inside

    def distance_km(self, lat_deg: float, lon_deg: float) -> float:
        """How far a point is from the area: 0 inside, else to the nearest edge."""
        if self.contains(lat_deg, lon_deg):
            return 0.0
        here = _project(lon_deg, lat_deg, lat_deg)
        points = [_project(lon, lat, lat_deg) for lon, lat in self.ring]
        return min(_to_segment(here, a, b) for a, b in pairwise(points))


def _check_ring(ring: tuple[tuple[float, float], ...]) -> None:
    if len(ring) > _MAX_CORNERS:
        message = f"a ring of {len(ring)} vertices is more than an area needs"
        raise GeometryError(message)
    if not ring or ring[0] != ring[-1]:
        message = "a ring is closed: its last vertex repeats its first"
        raise GeometryError(message)
    if len(set(ring)) < _MIN_CORNERS:
        message = "a polygon has at least three distinct corners"
        raise GeometryError(message)
    for lon, lat in ring:
        if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):  # noqa: PLR2004
            message = f"vertex ({lon}, {lat}) is off the globe"
            raise GeometryError(message)
    lons = [one[0] for one in ring]
    if max(lons) - min(lons) >= _MAX_LON_SPAN:
        message = "an area may not cross the antimeridian or span half the globe"
        raise GeometryError(message)
    if _crosses_itself(ring):
        message = "the ring crosses itself"
        raise GeometryError(message)


def _crosses_itself(ring: tuple[tuple[float, float], ...]) -> bool:
    edges = list(pairwise(ring))
    count = len(edges)
    for i in range(count):
        for j in range(i + 2, count):
            if i == 0 and j == count - 1:
                continue
            if _segments_cross(*edges[i], *edges[j]):
                return True
    return False


def _segments_cross(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> bool:
    """Whether two segments properly intersect (touching ends does not count)."""

    def side(
        p: tuple[float, float], q: tuple[float, float], r: tuple[float, float]
    ) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    return side(a, b, c) * side(a, b, d) < 0 and side(c, d, a) * side(c, d, b) < 0


def _mean_lat(ring: tuple[tuple[float, float], ...]) -> float:
    corners = ring[:-1]
    return sum(one[1] for one in corners) / len(corners)


def _project(lon: float, lat: float, lat0: float) -> tuple[float, float]:
    """Kilometres east and north on an equirectangular plane scaled at ``lat0``."""
    return lon * KM_PER_DEG * math.cos(math.radians(lat0)), lat * KM_PER_DEG


def _to_segment(
    p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dy * dy
    t = 0.0 if length == 0 else ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length
    t = max(0.0, min(1.0, t))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def _as_list(value: object) -> list[object]:
    if not isinstance(value, list):
        message = "a ring is a list of [longitude, latitude] pairs"
        raise GeometryError(message)
    return value


def _vertex(value: object) -> tuple[float, float]:
    pair = _as_list(value)
    if len(pair) < _PAIR or not all(
        isinstance(one, int | float) and not isinstance(one, bool) for one in pair[:2]
    ):
        message = f"{value!r} is not a [longitude, latitude] pair"
        raise GeometryError(message)
    return float(pair[0]), float(pair[1])  # type: ignore[arg-type]
