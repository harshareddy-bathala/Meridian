"""The picture people look at: the scene, its graticule, a scale bar, north and credit.

The scene is drawn at :data:`DISPLAY_SIDE` pixels on its longer side, on the
same EPSG:4326 grid enlarged or reduced as one, inside a margin that holds the
labels. Enlarging repeats pixels (nearest neighbour), so a 30 m scene still
looks like 30 m pixels and is not made to look sharper than it is; reducing
averages them.

**Every line is placed by arithmetic.** :mod:`.graticule` chooses the
interval and takes each latitude and longitude through this picture's own
affine transform to a pixel row or column. Nothing here estimates a position
from the image (D-133, D-373).

**The same scene draws the same bytes.** Text is laid out by FreeType's basic
layout, not by a shaping library that may or may not be installed, in IBM Plex
Sans, which travels inside the package with its licence.

Reference: docs/DECISIONS.md D-133, D-373.
"""

from __future__ import annotations

import io
from functools import cache
from importlib import resources

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from rasterio.enums import Resampling
from rasterio.transform import Affine
from rasterio.warp import reproject

from meridian_ingest.imagery.encode import png_bytes
from meridian_ingest.imagery.graticule import (
    column_of,
    graticule,
    row_of,
    scale_bar,
)
from meridian_ingest.imagery.scene import WGS84, Rgba, Scene
from meridian_ingest.imagery.sources import credit_for

__all__ = ["DISPLAY_SIDE", "display_png", "display_transform"]

DISPLAY_SIDE = 1200
"""The longer side of the drawn scene, in pixels, before the margin."""

Affine6 = tuple[float, float, float, float, float, float]

_LEFT, _RIGHT, _TOP, _BOTTOM_PAD = 88, 56, 28, 16
_TICK = 6
_GAP = 10
_LINE_HEIGHT = 19
_PAPER = (250, 250, 248)
_NO_DATA = (224, 224, 220)
_INK = (28, 28, 28, 255)
_LINE_DARK = (0, 0, 0, 80)
_LINE_LIGHT = (255, 255, 255, 210)
_FONT = "fonts/IBMPlexSans-Regular.woff2"


@cache
def _font(size: int) -> ImageFont.FreeTypeFont:
    data = resources.files("meridian_ingest.imagery").joinpath(_FONT).read_bytes()
    return ImageFont.truetype(
        io.BytesIO(data), size, layout_engine=ImageFont.Layout.BASIC
    )


def display_transform(scene: Scene) -> tuple[Affine6, tuple[int, int]]:
    """The drawn scene's grid: the scene's bounds on :data:`DISPLAY_SIDE` pixels."""
    scale = DISPLAY_SIDE / max(scene.width, scene.height)
    width = max(1, round(scene.width * scale))
    height = max(1, round(scene.height * scale))
    bounds = scene.bounds
    dx = (bounds.east - bounds.west) / width
    dy = (bounds.north - bounds.south) / height
    return (dx, 0.0, bounds.west, 0.0, -dy, bounds.north), (height, width)


def _drawn(scene: Scene) -> tuple[Rgba, Affine6]:
    transform, shape = display_transform(scene)
    drawn = np.zeros((4, *shape), dtype=np.uint8)
    enlarging = shape[1] >= scene.width
    reproject(
        source=scene.rgba,
        destination=drawn,
        src_transform=Affine(*scene.transform),
        src_crs=WGS84,
        src_alpha=4,
        dst_transform=Affine(*transform),
        dst_crs=WGS84,
        dst_alpha=4,
        resampling=Resampling.nearest if enlarging else Resampling.average,
    )
    return drawn, transform


def display_png(scene: Scene) -> bytes:
    """The scene with its graticule, scale bar, north arrow and credit, as a PNG."""
    drawn, transform = _drawn(scene)
    height, width = drawn.shape[1:]
    label, small = _font(14), _font(13)
    centre = (scene.bounds.south + scene.bounds.north) / 2.0
    ruler = scale_bar(transform, centre, max(1, width // 4))
    credit = _wrapped(
        credit_for(scene.source, scene.acquired_at.year),
        small,
        max(200, width - ruler.pixels - 3 * _GAP),
    )
    below = _TOP + height + _TICK + _GAP + _LINE_HEIGHT + _GAP
    total = below + max(len(credit) * _LINE_HEIGHT, 2 * _LINE_HEIGHT) + _BOTTOM_PAD
    canvas = Image.new("RGB", (_LEFT + width + _RIGHT, total), _PAPER)
    canvas.paste(_on_backdrop(drawn), (_LEFT, _TOP))
    # Drawn in RGBA onto an RGB picture: each translucent line is blended into
    # the pixels it covers, and no other pixel is touched.
    draw = ImageDraw.Draw(canvas, "RGBA")
    _graticule(draw, scene, transform, (height, width), label)
    draw.rectangle(
        (_LEFT - 1, _TOP - 1, _LEFT + width, _TOP + height), outline=_INK, width=1
    )
    _north(draw, _LEFT + width + _RIGHT // 2, _TOP, label)
    right = _LEFT + width
    bar_top = below + _LINE_HEIGHT
    draw.text(
        (right - ruler.pixels / 2, bar_top - 4),
        ruler.label,
        font=small,
        fill=_INK,
        anchor="mb",
    )
    draw.rectangle((right - ruler.pixels, bar_top, right, bar_top + 5), fill=_INK)
    for x in (right - ruler.pixels, right):
        draw.line((x, bar_top - 4, x, bar_top + 5), fill=_INK, width=1)
    for index, text in enumerate(credit):
        draw.text((_LEFT, below + index * _LINE_HEIGHT), text, font=small, fill=_INK)
    return png_bytes(np.moveaxis(np.asarray(canvas, dtype=np.uint8), -1, 0))


def _on_backdrop(drawn: Rgba) -> Image.Image:
    """The drawn scene over a flat grey wherever no source pixel reached."""
    alpha = drawn[3:4].astype(np.uint16)
    backdrop = np.array(_NO_DATA, dtype=np.uint16).reshape(3, 1, 1)
    rgb = (drawn[:3] * alpha + backdrop * (255 - alpha) + 127) // 255
    return Image.fromarray(
        np.ascontiguousarray(np.moveaxis(rgb.astype(np.uint8), 0, -1)), "RGB"
    )


def _graticule(
    draw: ImageDraw.ImageDraw,
    scene: Scene,
    transform: Affine6,
    shape: tuple[int, int],
    font: ImageFont.FreeTypeFont,
) -> None:
    """Each line across the drawn scene, a tick outside it, and its label."""
    height, width = shape
    bottom, right = _TOP + height, _LEFT + width
    for line in graticule(scene.bounds):
        if line.axis == "lat":
            row = row_of(transform, line.value_deg)
            if not 0 <= row < height:
                continue
            y = _TOP + row
            draw.line((_LEFT, y, right - 1, y), fill=_LINE_DARK, width=3)
            draw.line((_LEFT, y, right - 1, y), fill=_LINE_LIGHT, width=1)
            draw.line((_LEFT - _TICK, y, _LEFT - 1, y), fill=_INK, width=1)
            draw.text(
                (_LEFT - _TICK - 4, y), line.label, font=font, fill=_INK, anchor="rm"
            )
        else:
            col = column_of(transform, line.value_deg)
            if not 0 <= col < width:
                continue
            x = _LEFT + col
            draw.line((x, _TOP, x, bottom - 1), fill=_LINE_DARK, width=3)
            draw.line((x, _TOP, x, bottom - 1), fill=_LINE_LIGHT, width=1)
            draw.line((x, bottom, x, bottom + _TICK - 1), fill=_INK, width=1)
            draw.text(
                (x, bottom + _TICK + 2), line.label, font=font, fill=_INK, anchor="mt"
            )


def _north(
    draw: ImageDraw.ImageDraw, x: int, top: int, font: ImageFont.FreeTypeFont
) -> None:
    """An arrow pointing up the picture, which is north on a north-up grid."""
    draw.text((x, top), "N", font=font, fill=_INK, anchor="mt")
    tip = top + 22
    draw.polygon(
        ((x, tip), (x - 8, tip + 26), (x, tip + 19), (x + 8, tip + 26)), fill=_INK
    )


def _wrapped(text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    """``text`` broken between words into lines no wider than ``width``."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if current and font.getlength(trial) > width:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines
