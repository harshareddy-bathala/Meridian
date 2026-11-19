"""The one door GDAL reaches a remote scene through, and the window it reads.

A scene is a cloud-optimised GeoTIFF of a hundred megabytes or more; an area is
a few kilometres of it. So a scene is never downloaded. GDAL opens it over
``/vsicurl/`` and reads the window over the area's box with range requests,
and only those pixels are kept (D-372).

**One door.** This is the only module allowed to write a ``/vsicurl/`` path,
and rasterio is called as ``rasterio.open`` so the guard in ``tests/conftest.py``
stands where every network read enters (D-371). Which reader is used is a
seam: :class:`RemotePixels` in production, :class:`LocalPixels` over files a
test wrote, so the window arithmetic is exercised with nothing reachable
(D-142).

**Nothing is resampled.** A window is the source's own pixels at native
resolution, or at one of the source's own overviews when the native window's
longer side would exceed :data:`~.model.MAX_SIDE_PX`. Pixels are cut on the
source's grid, outward to whole pixels, so the window covers the box and a
little more, never less.

**A token is never written.** Planetary Computer signs its addresses with a
read token whose terms forbid passing it on (D-371). It is appended at the
door and nowhere else, and every message GDAL raises has it withheld before
anyone sees it.

Reference: docs/DECISIONS.md D-133, D-142, D-371, D-372.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np
import rasterio
from numpy.typing import NDArray
from rasterio.errors import RasterioError
from rasterio.warp import transform_bounds
from rasterio.windows import Window

from meridian_ingest.imagery.model import MAX_SIDE_PX, Bounds
from meridian_ingest.imagery.stac import strip_query

__all__ = [
    "REDACTED",
    "LocalPixels",
    "OffSceneError",
    "PixelReader",
    "Pixels",
    "ReadWindow",
    "RemotePixels",
    "WindowError",
    "read_window",
]

REDACTED = "<token withheld>"
Pixels = NDArray[np.uint8 | np.uint16]
"""A window's pixels: bytes for Sentinel‑2's visual and SCL assets, sixteen bits
for Landsat's reflectance and QA_PIXEL."""

_GEOGRAPHIC = "EPSG:4326"
_DENSIFY = 21
"""Points along each side when a box is projected: a UTM box of a geographic
one bows outward, and its corners alone would cut the window short."""

_EDGE = 1e-6
"""Pixels of slack when a box edge falls on a pixel edge, so float error does
not add a whole column of somebody else's ground."""


class _Affine(Protocol):
    """The six terms of a grid's affine transform, as rasterio's carries them."""

    a: float
    b: float
    c: float
    d: float
    e: float
    f: float


class _Crs(Protocol):
    """A grid's coordinate reference system, as far as an EPSG code goes."""

    def to_epsg(self) -> int | None:
        """The EPSG code, or None when there is none."""
        ...


class WindowError(ValueError):
    """A window that cannot be read as asked: off the scene, or too large."""


class OffSceneError(WindowError):
    """A granule whose pixels do not reach the box at all.

    Its own type because it is not a failure: a catalogue footprint is a
    rectangle around a granule's data, and a box near its corner can fall in
    the rectangle and outside the data. The other granules of the pass may
    still cover it.
    """


class PixelReader(Protocol):
    """Where ``rasterio.open`` is pointed for an asset's address."""

    def dataset_path(self, href: str) -> str:
        """The path GDAL opens for ``href``."""
        ...

    def options(self) -> Mapping[str, str]:
        """GDAL configuration for the read."""
        ...

    def redact(self, text: str) -> str:
        """``text`` with anything secret withheld."""
        ...

    def with_token(self, token: str) -> PixelReader:
        """The same reader, signing each address with ``token``."""
        ...


@dataclass(frozen=True, slots=True)
class RemotePixels:
    """Reads over HTTPS, through GDAL's ``/vsicurl/``.

    ``token``, when set, is a query appended at the door; its ``repr`` is
    suppressed so a traceback cannot print it.
    """

    user_agent: str
    timeout_s: float = 30.0
    token: str | None = field(default=None, repr=False)

    def with_token(self, token: str) -> RemotePixels:
        """The same reader, signing each address with ``token``."""
        return RemotePixels(self.user_agent, self.timeout_s, token)

    def dataset_path(self, href: str) -> str:
        """``/vsicurl/`` and the address, signed if a token is held."""
        address = strip_query(href)
        if self.token:
            address = f"{address}?{self.token}"
        return f"/vsicurl/{address}"

    def options(self) -> Mapping[str, str]:
        """Who we are, how long to wait, and no directory listings.

        ``GDAL_DISABLE_READDIR_ON_OPEN`` stops GDAL listing the scene's folder
        for sidecar files: a request per open that tells it nothing a COG
        needs.
        """
        return {
            "GDAL_HTTP_USERAGENT": self.user_agent,
            "GDAL_HTTP_TIMEOUT": str(math.ceil(self.timeout_s)),
            "GDAL_HTTP_MAX_RETRY": "3",
            "GDAL_HTTP_RETRY_DELAY": "2",
            "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        }

    def redact(self, text: str) -> str:
        """``text`` without the token."""
        return text.replace(self.token, REDACTED) if self.token else text


@dataclass(frozen=True, slots=True)
class LocalPixels:
    """Reads each address from a file named for it, with nothing reachable.

    An address no file stands in for is refused rather than guessed at, so a
    test proves which assets were read, not merely that some were.
    """

    files: Mapping[str, Path]

    def dataset_path(self, href: str) -> str:
        """The file standing in for ``href``."""
        path = self.files.get(strip_query(href))
        if path is None:
            message = f"{strip_query(href)} has no stand-in file"
            raise WindowError(message)
        return str(path)

    def options(self) -> Mapping[str, str]:
        """Nothing to configure for a local file."""
        return {}

    def with_token(self, token: str) -> LocalPixels:  # noqa: ARG002 — the seam's shape
        """A local file needs no token; the same reader."""
        return self

    def redact(self, text: str) -> str:
        """Nothing is secret here."""
        return text


@dataclass(frozen=True, slots=True)
class ReadWindow:
    """Pixels read over a box, where they sit, and the box's whole window.

    ``array`` is ``(bands, rows, columns)`` over the part of the box the scene
    covers. ``box_transform`` and ``box_shape`` describe the window the whole
    box needs on the same grid, which is how granules of one pass are placed
    side by side without resampling.
    """

    href: str
    array: Pixels
    crs: str
    transform: tuple[float, float, float, float, float, float]
    nodata: float | None
    overview_level: int
    """0 for native resolution; ``n`` for the source's ``n``-th overview."""

    box_transform: tuple[float, float, float, float, float, float]
    box_shape: tuple[int, int]

    @property
    def height(self) -> int:
        """Rows read."""
        return int(self.array.shape[1])

    @property
    def width(self) -> int:
        """Columns read."""
        return int(self.array.shape[2])


def read_window(
    reader: PixelReader,
    href: str,
    bounds: Bounds,
    *,
    overview_level: int | None = None,
) -> ReadWindow:
    """Read the source's own pixels over ``bounds``.

    Args:
        reader: Where GDAL is pointed.
        href: The asset's address, as the catalogue gave it.
        bounds: The area's box, in degrees.
        overview_level: Force this level, so every granule of one pass is read
            on the same grid. Chosen from the box's size when None.

    Raises:
        WindowError: The scene does not reach the box, the box is too large
            for any overview, the grid is rotated, or GDAL could not read it.
            GDAL's own message is included with any token withheld.
    """
    path = reader.dataset_path(href)
    try:
        with rasterio.Env(**reader.options()):
            return _read(path, href, bounds, overview_level)
    except RasterioError as exc:
        message = f"{strip_query(href)} could not be read: {reader.redact(str(exc))}"
        raise WindowError(message) from None


def _read(path: str, href: str, bounds: Bounds, forced: int | None) -> ReadWindow:
    with rasterio.open(path) as base:
        crs = _epsg(base.crs, href)
        box = transform_bounds(
            _GEOGRAPHIC, base.crs, *bounds.to_list(), densify_pts=_DENSIFY
        )
        factors = [1, *base.overviews(1)]
        level = _level(base.transform, box, factors) if forced is None else forced
        if not 0 <= level < len(factors):
            message = f"{strip_query(href)} has no overview level {level}"
            raise WindowError(message)
    return _cut(path, href, crs, box, level)


def _cut(
    path: str, href: str, crs: str, box: Sequence[float], level: int
) -> ReadWindow:
    """Read the box's window at ``level``: 0 the full resolution, else an overview."""
    options = {} if level == 0 else {"overview_level": level - 1}
    with rasterio.open(path, **options) as source:
        transform = source.transform
        if transform.b != 0 or transform.d != 0:
            message = f"{strip_query(href)} is on a rotated grid"
            raise WindowError(message)
        col0, row0, col1, row1 = _box_pixels(transform, box)
        if max(col1 - col0, row1 - row0) > MAX_SIDE_PX:
            message = (
                f"{strip_query(href)}: the box is {col1 - col0} × {row1 - row0} px "
                f"at this level, over the {MAX_SIDE_PX} px cap (D-372)"
            )
            raise WindowError(message)
        left, top = max(col0, 0), max(row0, 0)
        right, bottom = min(col1, source.width), min(row1, source.height)
        if right <= left or bottom <= top:
            message = f"{strip_query(href)} does not reach the box"
            raise OffSceneError(message)
        window = Window(left, top, right - left, bottom - top)
        array = source.read(window=window)
        cut = source.window_transform(window)
        box_transform = source.window_transform(
            Window(col0, row0, col1 - col0, row1 - row0)
        )
        return ReadWindow(
            href=strip_query(href),
            array=array,
            crs=crs,
            transform=_terms(cut),
            nodata=source.nodata,
            overview_level=level,
            box_transform=_terms(box_transform),
            box_shape=(row1 - row0, col1 - col0),
        )


def _level(transform: _Affine, box: Sequence[float], factors: Sequence[int]) -> int:
    """The finest level at which the box's window fits the cap."""
    col0, row0, col1, row1 = _box_pixels(transform, box)
    side = max(col1 - col0, row1 - row0)
    for level, factor in enumerate(factors):
        if math.ceil(side / factor) <= MAX_SIDE_PX:
            return level
    message = (
        f"the box is {side} px across at native resolution, and no overview "
        f"brings it under {MAX_SIDE_PX} px (D-372)"
    )
    raise WindowError(message)


def _box_pixels(transform: _Affine, box: Sequence[float]) -> tuple[int, int, int, int]:
    """The whole-pixel window over a projected box, cut outward."""
    left, bottom, right, top = box
    a, c, e, f = transform.a, transform.c, transform.e, transform.f
    col0 = math.floor((left - c) / a + _EDGE)
    col1 = math.ceil((right - c) / a - _EDGE)
    row0 = math.floor((top - f) / e + _EDGE)
    row1 = math.ceil((bottom - f) / e - _EDGE)
    return col0, row0, col1, row1


def _epsg(crs: _Crs | None, href: str) -> str:
    code = crs.to_epsg() if crs is not None else None
    if code is None:
        message = f"{strip_query(href)} has no EPSG code for its grid"
        raise WindowError(message)
    return f"EPSG:{code}"


def _terms(affine: _Affine) -> tuple[float, float, float, float, float, float]:
    return (
        float(affine.a),
        float(affine.b),
        float(affine.c),
        float(affine.d),
        float(affine.e),
        float(affine.f),
    )
