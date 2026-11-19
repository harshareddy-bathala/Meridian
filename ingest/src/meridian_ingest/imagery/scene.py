"""A scene as one picture in EPSG:4326, made from what the raw store holds.

Rendering starts here, and it starts from the raw store alone. Its input is a
list of raw paths — the display windows a search kept, or GIBS's tiles — and
the area's box. It takes no address and no client, so a published picture can
always be made again from what was kept, months later and with every archive
offline (rule 8).

**How each archive becomes a picture** is its source's ``rendering``, stated in
every manifest:

- Sentinel‑2: the ``visual`` asset as published, reprojected;
- Landsat: surface reflectance with one fixed stretch, reprojected;
- GIBS: its tiles side by side on their own grid, cut to the box, never
  resampled.

**The picture's grid.** A projected scene is reprojected onto a grid whose
north-west corner is the box's, with pixels as long as the source's own on the
ground in each direction at the box's centre latitude, so they stay square on
the ground and the scene's resolution is the one stated. Several granules of a
pass are placed in item-id order, the first valid pixel winning, as the cloud
count placed them (D-372). A pixel no granule covers is transparent.

Reference: docs/DECISIONS.md D-133, D-141, D-372, D-373.
"""

from __future__ import annotations

import io
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import rasterio
from numpy.typing import NDArray
from PIL import Image
from rasterio.enums import Resampling
from rasterio.transform import Affine
from rasterio.warp import reproject

from meridian_ingest.imagery.graticule import metres_per_degree
from meridian_ingest.imagery.model import Bounds, MalformedImageryError, SceneWindow
from meridian_ingest.imagery.sources import (
    LANDSAT_WHITE,
    GibsSource,
    ImagerySource,
    StacSource,
    imagery_source,
)
from meridian_ingest.imagery.stored import WINDOW_TAG
from meridian_ingest.raw_store import RawStore, StoredArtefact

__all__ = ["RenderError", "Rgba", "Scene", "compose"]

Rgba = NDArray[np.uint8]
"""``(4, rows, columns)``: red, green, blue and alpha."""

Affine6 = tuple[float, float, float, float, float, float]

WGS84 = "EPSG:4326"
_LANDSAT_SCALE = 0.0000275
_LANDSAT_OFFSET = -0.2
_GIBS_TILE_PX = 512
"""The side of a tile in GIBS's geographic ``250m`` tile matrix set."""
_SPAN_SLACK = 1e-9
_OPAQUE = 255
"""Full coverage in an alpha band, which GDAL reads as 0 to 255 for any type."""
_HALF_COVERED = 128
"""A picture pixel is drawn when at least half of what it was warped from is
covered; the first granule to cover it so wins it."""


class RenderError(ValueError):
    """The raw paths given cannot be made into one picture."""


@dataclass(frozen=True)
class Scene:
    """One picture of an area in EPSG:4326, and what it was made from."""

    rgba: Rgba
    transform: Affine6
    """North-up: ``(dx, 0, west, 0, -dy, north)`` in degrees."""

    source: ImagerySource
    acquired_at: datetime
    resolution_m: float
    """The source's own pixel on the ground, north to south, as drawn."""

    raw_paths: tuple[str, ...]

    @property
    def height(self) -> int:
        """Rows."""
        return int(self.rgba.shape[1])

    @property
    def width(self) -> int:
        """Columns."""
        return int(self.rgba.shape[2])

    @property
    def bounds(self) -> Bounds:
        """What the picture covers: the box, cut outward to whole pixels."""
        dx, _, west, _, e, north = self.transform
        return Bounds(west, north + self.height * e, west + self.width * dx, north)

    @property
    def pixel_size_deg(self) -> tuple[float, float]:
        """A pixel's width and height, in degrees."""
        return self.transform[0], -self.transform[4]


def compose(store: RawStore, raw_paths: Sequence[str], bounds: Bounds) -> Scene:
    """The picture the raw paths make over ``bounds``.

    Raises:
        RenderError: The paths are of more than one source, are not pictures
            (``tile`` payloads), or do not together cover any of the box.
    """
    if not raw_paths:
        message = "a picture needs at least one stored window or tile"
        raise RenderError(message)
    stored = [store.read(path) for path in sorted(raw_paths)]
    sources = {one.manifest.provenance.source_id for one in stored}
    if len(sources) != 1:
        message = f"one picture is made from one source, not {sorted(sources)}"
        raise RenderError(message)
    for one in stored:
        if one.manifest.provenance.payload_kind != "tile":
            message = (
                f"{one.raw_path} is a record, not a picture; only display windows "
                "and tiles are drawn"
            )
            raise RenderError(message)
    source = imagery_source(sources.pop())
    if isinstance(source, GibsSource):
        return _gibs(source, stored, bounds)
    return _projected(source, stored, bounds)


# --- Sentinel-2 and Landsat: windows on a projected grid -------------------------


@dataclass(frozen=True)
class _Window:
    record: SceneWindow
    array: NDArray[np.uint8 | np.uint16]


def _read(one: StoredArtefact) -> _Window:
    with rasterio.open(one.artefact) as source:
        text = source.tags().get(WINDOW_TAG)
        array = source.read()
    if text is None:
        message = f"{one.raw_path} carries no {WINDOW_TAG} record"
        raise RenderError(message)
    try:
        record = SceneWindow.from_json(json.loads(text))
    except (json.JSONDecodeError, MalformedImageryError) as exc:
        message = f"{one.raw_path}: its {WINDOW_TAG} record is unreadable: {exc}"
        raise RenderError(message) from exc
    return _Window(record, array)


def _granules(
    source: StacSource, stored: Sequence[StoredArtefact]
) -> list[tuple[SceneWindow, NDArray[np.uint8 | np.uint16]]]:
    """Each item's display bands stacked in the source's order, by item id."""
    by_item: dict[str, dict[str, _Window]] = {}
    for one in stored:
        window = _read(one)
        by_item.setdefault(window.record.item_id, {})[window.record.asset] = window
    granules = []
    for item_id in sorted(by_item):
        assets = by_item[item_id]
        missing = [name for name in source.display_assets if name not in assets]
        if missing:
            message = f"{item_id} lacks its {', '.join(missing)} window"
            raise RenderError(message)
        windows = [assets[name] for name in source.display_assets]
        first = windows[0].record
        for other in windows[1:]:
            if (other.record.crs, other.record.transform) != (
                first.crs,
                first.transform,
            ) or other.array.shape[1:] != windows[0].array.shape[1:]:
                message = f"{item_id}'s display windows are not on one grid"
                raise RenderError(message)
        granules.append((first, np.concatenate([one.array for one in windows])))
    return granules


def _projected(
    source: StacSource, stored: Sequence[StoredArtefact], bounds: Bounds
) -> Scene:
    granules = _granules(source, stored)
    first = granules[0][0]
    resolution_m = max(abs(first.transform[0]), abs(first.transform[4]))
    transform, shape = _grid(bounds, resolution_m)
    bands = granules[0][1].shape[0]
    data = np.zeros((bands, *shape), dtype=granules[0][1].dtype)
    valid = np.zeros(shape, dtype=bool)
    for record, array in granules:
        warped = _warped(record, array, transform, shape)
        covered = warped[-1] >= _HALF_COVERED
        here = warped[:-1]
        fresh = covered & ~valid
        data[:, fresh] = here[:, fresh]
        valid |= fresh
    if not valid.any():
        message = "no window has a pixel over the box"
        raise RenderError(message)
    colour = _stretched(data) if source.colour == "surface_reflectance" else data
    rgba = np.zeros((4, *shape), dtype=np.uint8)
    rgba[:3] = np.where(valid, colour.astype(np.uint8), 0)
    rgba[3] = np.where(valid, 255, 0)
    return Scene(
        rgba=rgba,
        transform=transform,
        source=source,
        acquired_at=min(record.acquired_at for record, _ in granules),
        resolution_m=resolution_m,
        raw_paths=tuple(one.raw_path for one in stored),
    )


def _warped(
    record: SceneWindow,
    array: NDArray[np.uint8 | np.uint16],
    transform: Affine6,
    shape: tuple[int, int],
) -> NDArray[np.uint8 | np.uint16]:
    """One granule on the picture's grid, its last band how much of it is covered.

    Coverage travels as an alpha band rather than as a nodata value. A nodata
    value is per band, so a valid pixel with one band at 0 would lose that
    band, and GDAL moves a valid value off the nodata value as it writes.
    A source pixel is covered where any of its bands is not 0, the archives'
    own fill.
    """
    alpha = np.where(np.any(array != 0, axis=0), _OPAQUE, 0).astype(array.dtype)
    source = np.concatenate([array, alpha[np.newaxis]])
    warped = np.zeros((source.shape[0], *shape), dtype=array.dtype)
    reproject(
        source=source,
        destination=warped,
        src_transform=Affine(*record.transform),
        src_crs=record.crs,
        src_alpha=source.shape[0],
        dst_transform=Affine(*transform),
        dst_crs=WGS84,
        dst_alpha=source.shape[0],
        resampling=Resampling.bilinear,
    )
    return warped


def _stretched(data: NDArray[np.uint8 | np.uint16]) -> NDArray[np.uint8]:
    """Surface reflectance, 0 to :data:`LANDSAT_WHITE`, as 0–255."""
    reflectance = data.astype(np.float64) * _LANDSAT_SCALE + _LANDSAT_OFFSET
    scaled = np.clip(reflectance / LANDSAT_WHITE, 0.0, 1.0) * 255.0
    return np.rint(scaled).astype(np.uint8)


def _grid(bounds: Bounds, resolution_m: float) -> tuple[Affine6, tuple[int, int]]:
    """A north-up EPSG:4326 grid over the box, its pixels square on the ground."""
    centre = (bounds.south + bounds.north) / 2.0
    east_m, north_m = metres_per_degree(centre)
    dx, dy = resolution_m / east_m, resolution_m / north_m
    width = math.ceil((bounds.east - bounds.west) / dx - _SPAN_SLACK)
    height = math.ceil((bounds.north - bounds.south) / dy - _SPAN_SLACK)
    return (dx, 0.0, bounds.west, 0.0, -dy, bounds.north), (height, width)


# --- GIBS: tiles on their own geographic grid -------------------------------------


def _gibs(
    source: GibsSource, stored: Sequence[StoredArtefact], bounds: Bounds
) -> Scene:
    span = 288.0 / 2**source.level
    pixel = span / _GIBS_TILE_PX
    first_col = math.floor((bounds.west + 180.0) / pixel + _SPAN_SLACK)
    last_col = math.ceil((bounds.east + 180.0) / pixel - _SPAN_SLACK)
    first_row = math.floor((90.0 - bounds.north) / pixel + _SPAN_SLACK)
    last_row = math.ceil((90.0 - bounds.south) / pixel - _SPAN_SLACK)
    shape = (last_row - first_row, last_col - first_col)
    rgba = np.zeros((4, *shape), dtype=np.uint8)
    days = set()
    for one in stored:
        provenance = one.manifest.provenance
        extent = provenance.spatial_extent
        if extent is None or provenance.valid_from is None:
            message = f"{one.raw_path} does not say where or when its tile is"
            raise RenderError(message)
        if abs(extent["east"] - extent["west"] - span) > _SPAN_SLACK:
            message = f"{one.raw_path} is not a level-{source.level} tile"
            raise RenderError(message)
        days.add(provenance.valid_from)
        tile = _decoded(one)
        col = round((extent["west"] + 180.0) / pixel) - first_col
        row = round((90.0 - extent["north"]) / pixel) - first_row
        _place(rgba, tile, row, col)
    if len(days) != 1:
        message = "one GIBS picture is made from one day's tiles"
        raise RenderError(message)
    if not rgba[3].all():
        message = "the tiles given do not cover the whole box"
        raise RenderError(message)
    west = -180.0 + (first_col * pixel)
    north = 90.0 - (first_row * pixel)
    return Scene(
        rgba=rgba,
        transform=(pixel, 0.0, west, 0.0, -pixel, north),
        source=source,
        acquired_at=days.pop(),
        resolution_m=pixel * metres_per_degree((bounds.south + bounds.north) / 2)[1],
        raw_paths=tuple(one.raw_path for one in stored),
    )


def _decoded(one: StoredArtefact) -> NDArray[np.uint8]:
    """A GIBS tile's pixels, ``(3, 512, 512)``."""
    with Image.open(io.BytesIO(one.read_bytes())) as image:
        rgb = image.convert("RGB")
    if rgb.size != (_GIBS_TILE_PX, _GIBS_TILE_PX):
        message = f"{one.raw_path} is {rgb.size}, not a {_GIBS_TILE_PX}-px tile"
        raise RenderError(message)
    return np.moveaxis(np.asarray(rgb, dtype=np.uint8), -1, 0)


def _place(rgba: Rgba, tile: NDArray[np.uint8], row: int, col: int) -> None:
    """Copy the part of one tile that falls inside the picture."""
    rows, cols = rgba.shape[1:]
    top, left = max(row, 0), max(col, 0)
    bottom, right = min(row + _GIBS_TILE_PX, rows), min(col + _GIBS_TILE_PX, cols)
    if top >= bottom or left >= right:
        return
    part = tile[:, top - row : bottom - row, left - col : right - col]
    rgba[:3, top:bottom, left:right] = part
    rgba[3, top:bottom, left:right] = 255
