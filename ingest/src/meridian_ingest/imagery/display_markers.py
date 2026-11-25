"""The markers on the display picture, drawn where their records place them.

Each marker is numbered by its place in the manifest, from one, so the number
on the picture is the row of the viewer's table and the index of the
manifest's ``annotations``:

- the area and its changes share one box, the area's, outlined once and
  labelled with their numbers in its north-west corner;
- a fire is a filled dot with its number beside it;
- a place is a small ring with its name beside it, since a name says more
  than a number would.

Every position goes through :mod:`.graticule`'s ``row_of`` and ``column_of``
on the picture's own transform, as the latitude and longitude lines do. Nothing
is placed by looking at the picture (D-133, D-374).

Reference: docs/DECISIONS.md D-133, D-326, D-373, D-374.
"""

from __future__ import annotations

from collections.abc import Sequence

from PIL import ImageDraw, ImageFont

from meridian_ingest.imagery.graticule import column_of, row_of
from meridian_ingest.imagery.model import Annotation, Bounds, Point

__all__ = ["draw_markers", "numbers"]

Affine6 = tuple[float, float, float, float, float, float]

_BOX = (255, 196, 40, 255)
_FIRE = (232, 72, 28, 255)
_HALO = (0, 0, 0, 200)
_LABEL = (255, 255, 255, 255)
_FIRE_R = 6
_PLACE_R = 3


def numbers(first: int, last: int) -> str:
    """``3`` or ``3–5``: a run of marker numbers as the picture writes it."""
    return str(first) if first == last else f"{first}–{last}"


def draw_markers(
    draw: ImageDraw.ImageDraw,
    markers: Sequence[Annotation],
    transform: Affine6,
    origin: tuple[int, int],
    font: ImageFont.FreeTypeFont,
) -> None:
    """Draw every marker onto the picture whose top-left pixel is at ``origin``."""
    boxed = [
        n for n, one in enumerate(markers, start=1) if one.kind in {"area", "change"}
    ]
    box = next(
        (one.geometry for one in markers if isinstance(one.geometry, Bounds)), None
    )
    if box is not None:
        _box(draw, box, boxed, transform=transform, origin=origin, font=font)
    for n, marker in enumerate(markers, start=1):
        if not isinstance(marker.geometry, Point):
            continue
        x, y = _at(marker.geometry, transform, origin)
        if marker.kind == "fire":
            draw.ellipse(
                (x - _FIRE_R, y - _FIRE_R, x + _FIRE_R, y + _FIRE_R),
                fill=_FIRE,
                outline=_LABEL,
                width=2,
            )
            _label(draw, (x + _FIRE_R + 3, y), str(n), font)
        else:
            draw.ellipse(
                (x - _PLACE_R, y - _PLACE_R, x + _PLACE_R, y + _PLACE_R),
                fill=_LABEL,
                outline=_HALO,
                width=1,
            )
            _label(draw, (x + _PLACE_R + 4, y), marker.label, font)


def _box(  # noqa: PLR0913 — the box, its numbers and where the picture lies
    draw: ImageDraw.ImageDraw,
    box: Bounds,
    boxed: Sequence[int],
    *,
    transform: Affine6,
    origin: tuple[int, int],
    font: ImageFont.FreeTypeFont,
) -> None:
    left, top = _at(Point(box.north, box.west), transform, origin)
    right, bottom = _at(Point(box.south, box.east), transform, origin)
    draw.rectangle((left, top, right, bottom), outline=_HALO, width=4)
    draw.rectangle((left, top, right, bottom), outline=_BOX, width=2)
    if boxed:
        _label(draw, (left + 6, top + 4), numbers(boxed[0], boxed[-1]), font, "lt")


def _at(point: Point, transform: Affine6, origin: tuple[int, int]) -> tuple[int, int]:
    return (
        origin[0] + column_of(transform, point.lon_deg),
        origin[1] + row_of(transform, point.lat_deg),
    )


def _label(
    draw: ImageDraw.ImageDraw,
    at: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    anchor: str = "lm",
) -> None:
    """White text with a dark edge, which reads over any ground."""
    draw.text(
        at,
        text,
        font=font,
        fill=_LABEL,
        anchor=anchor,
        stroke_width=2,
        stroke_fill=_HALO,
    )
