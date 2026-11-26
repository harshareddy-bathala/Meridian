"""Pictures as bytes: PNG, WebP and the world file, each with fixed options.

Nothing an encoder writes carries a time or a machine's name, so the same
pixels give the same bytes, and a published directory can be named by its
contents (D-373).

Reference: docs/DECISIONS.md D-373.
"""

from __future__ import annotations

import io

import numpy as np
from numpy.typing import NDArray
from PIL import Image

__all__ = ["TILE_QUALITY", "png_bytes", "webp_bytes", "world_file"]

TILE_QUALITY = 82
"""The WebP quality of every tile: small enough that a pyramid of a few
hundred tiles is a few megabytes, high enough that streets stay streets."""


_MODES = {3: "RGB", 4: "RGBA"}


def _image(bands: NDArray[np.uint8]) -> Image.Image:
    mode = _MODES[int(bands.shape[0])]
    return Image.fromarray(np.ascontiguousarray(np.moveaxis(bands, 0, -1)), mode)


def png_bytes(bands: NDArray[np.uint8]) -> bytes:
    """A ``(3 or 4, rows, columns)`` array as a PNG with no time chunk."""
    buffer = io.BytesIO()
    _image(bands).save(buffer, format="PNG", compress_level=9)
    return buffer.getvalue()


def webp_bytes(rgba: NDArray[np.uint8]) -> bytes:
    """A ``(4, rows, columns)`` array as a lossy WebP with its alpha kept."""
    buffer = io.BytesIO()
    _image(rgba).save(buffer, format="WEBP", quality=TILE_QUALITY, method=6)
    return buffer.getvalue()


def world_file(transform: tuple[float, float, float, float, float, float]) -> str:
    """The six lines of an ESRI world file for the picture on ``transform``.

    A world file names the centre of the top-left pixel, where the affine
    transform names its corner, so the origin moves in by half a pixel.
    """
    a, b, c, d, e, f = transform
    terms = (a, d, b, e, c + a / 2.0 + b / 2.0, f + d / 2.0 + e / 2.0)
    return "".join(f"{term!r}\n" for term in terms)
