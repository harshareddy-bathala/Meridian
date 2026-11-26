"""Where latitude and longitude fall on a picture, worked out by our own arithmetic.

A published picture carries its own latitude and longitude lines, so a reader
can place it without trusting the viewer. The lines are not read from the
picture or from a tiling library: each is a coordinate taken through the
picture's affine transform to a pixel, here, in a few lines a test can check
against a known answer (D-373).

**The interval** is chosen from the box's extent, among 0.01°, 0.02°, 0.05°,
0.1°, 0.2°, 0.5° and 1°: the finest that puts at most :data:`MOST_LINES` lines
across the longer side. Intervals are held as whole hundredths of a degree,
so a line is ``k × step / 100`` and never an accumulated sum that drifts.

**The scale bar** uses the WGS 84 ellipsoid's length of a degree at the
picture's centre latitude. Across one published area that length changes by a
fraction of a per cent, which is below what a bar a few hundred pixels long
can show.

Stdlib only: nothing here reads a pixel.

Reference: docs/DECISIONS.md D-326, D-373.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from meridian_ingest.imagery.model import Bounds

__all__ = [
    "INTERVALS_HUNDREDTHS",
    "MOST_LINES",
    "GridLine",
    "Ruler",
    "column_of",
    "graticule",
    "interval_hundredths",
    "metres_per_degree",
    "row_of",
    "scale_bar",
]

Affine6 = tuple[float, float, float, float, float, float]

INTERVALS_HUNDREDTHS = (1, 2, 5, 10, 20, 50, 100)
"""The graticule's intervals, in hundredths of a degree: 0.01° to 1°."""

MOST_LINES = 5
"""The most lines of one kind across the longer side of a picture."""

_WGS84_A = 6_378_137.0
_WGS84_E2 = 6.694_379_990_141_316e-3
_EDGE = 1e-9
"""Slack for a line that falls on a box's edge, or a pixel's, in floating point."""


@dataclass(frozen=True, slots=True)
class GridLine:
    """One latitude or longitude line: its value and its label."""

    axis: str
    """``lat`` or ``lon``."""

    value_deg: float
    label: str


@dataclass(frozen=True, slots=True)
class Ruler:
    """A scale bar: how long on the ground, how long on the picture, and its words."""

    metres: int
    pixels: int
    label: str


def interval_hundredths(bounds: Bounds) -> int:
    """The finest interval that draws at most :data:`MOST_LINES` lines a side."""
    for step in INTERVALS_HUNDREDTHS:
        if (
            max(
                len(_multiples(bounds.south, bounds.north, step)),
                len(_multiples(bounds.west, bounds.east, step)),
            )
            <= MOST_LINES
        ):
            return step
    return INTERVALS_HUNDREDTHS[-1]


def graticule(bounds: Bounds) -> tuple[GridLine, ...]:
    """Every latitude and then longitude line inside ``bounds``, labelled."""
    step = interval_hundredths(bounds)
    lines = [
        GridLine("lat", value, _label(value, step, "N", "S"))
        for value in _multiples(bounds.south, bounds.north, step)
    ]
    lines.extend(
        GridLine("lon", value, _label(value, step, "E", "W"))
        for value in _multiples(bounds.west, bounds.east, step)
    )
    return tuple(lines)


def _multiples(low: float, high: float, step: int) -> list[float]:
    """Each multiple of ``step`` hundredths of a degree in ``[low, high]``."""
    first = math.ceil(low * 100 / step - _EDGE)
    last = math.floor(high * 100 / step + _EDGE)
    return [k * step / 100 for k in range(first, last + 1)]


def _label(value: float, step: int, positive: str, negative: str) -> str:
    """``12.90°N``: as many decimals as the interval needs, and a hemisphere."""
    decimals = 2 if step < 10 else 1 if step < 100 else 0  # noqa: PLR2004 — 0.1° and 1°
    magnitude = f"{abs(value):.{decimals}f}°"
    if round(abs(value), decimals) == 0:
        return magnitude
    return magnitude + (positive if value > 0 else negative)


def row_of(transform: Affine6, lat_deg: float) -> int:
    """The pixel row a latitude falls in, on a north-up picture.

    Rows are counted from the top edge, and a latitude on the boundary between
    two rows belongs to the lower one, as a pixel's top edge belongs to it.

    Raises:
        ValueError: The transform is rotated, or not north-up.
    """
    _north_up(transform)
    _, _, _, _, e, f = transform
    return math.floor((lat_deg - f) / e + _EDGE)


def column_of(transform: Affine6, lon_deg: float) -> int:
    """The pixel column a longitude falls in, counted from the left edge.

    Raises:
        ValueError: The transform is rotated, or not north-up.
    """
    _north_up(transform)
    a, _, c, _, _, _ = transform
    return math.floor((lon_deg - c) / a + _EDGE)


def _north_up(transform: Affine6) -> None:
    a, b, _, d, e, _ = transform
    if b != 0 or d != 0 or a <= 0 or e >= 0:
        message = f"a picture's transform must be north-up, not {transform}"
        raise ValueError(message)


def metres_per_degree(lat_deg: float) -> tuple[float, float]:
    """The length of a degree of longitude and of latitude, on WGS 84, in metres."""
    phi = math.radians(lat_deg)
    w = 1.0 - _WGS84_E2 * math.sin(phi) ** 2
    east = math.pi / 180.0 * _WGS84_A * math.cos(phi) / math.sqrt(w)
    north = math.pi / 180.0 * _WGS84_A * (1.0 - _WGS84_E2) / w**1.5
    return east, north


def scale_bar(transform: Affine6, lat_deg: float, longest_px: int) -> Ruler:
    """The longest round length — 1, 2 or 5 × 10ⁿ m — that fits in ``longest_px``.

    Raises:
        ValueError: The transform is not north-up, or not even a metre fits.
    """
    _north_up(transform)
    metres_per_px = transform[0] * metres_per_degree(lat_deg)[0]
    room = longest_px * metres_per_px
    if room < 1.0:
        message = f"{longest_px} px is under a metre on the ground"
        raise ValueError(message)
    power = 10 ** math.floor(math.log10(room))
    metres = max(
        factor * power for factor in (1, 2, 5) if factor * power <= room + _EDGE
    )
    label = f"{metres} m" if metres < 1000 else f"{metres // 1000} km"  # noqa: PLR2004
    return Ruler(int(metres), round(metres / metres_per_px), label)
