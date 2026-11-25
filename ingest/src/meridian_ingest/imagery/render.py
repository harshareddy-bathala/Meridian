"""A scene's published files, written from the raw store into one directory.

``render_scene`` takes raw paths and a box, and writes:

- ``scene.tif``, a cloud-optimised GeoTIFF in EPSG:4326, red, green, blue and
  alpha, which a GIS tool opens in the right place;
- ``scene.png`` and its world file ``scene.pgw``, for tools that read a
  picture and a world file rather than a GeoTIFF;
- ``tiles/<z>/<x>/<y>.webp``, a Web Mercator pyramid for the viewer, its zoom
  range from the scene's own resolution (:mod:`.webmercator`);
- ``display.png``, the picture people look at, with our own latitude and
  longitude lines, a scale bar, a north arrow and the credit (:mod:`.display`).

**The same inputs write the same bytes.** Every encoder is given fixed options
and nothing that carries a time: the GeoTIFF has no date tag, the PNGs no time
chunk, the tiles one quality. That is what lets a directory be named by its
contents and checked by rendering it again (rule 8).

**It never reaches the network.** It takes no address and no client; what it
reads is the raw store, which is where every network read already ended.

Reference: docs/DECISIONS.md D-141, D-371, D-373.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import rasterio
import rasterio.shutil
from rasterio.enums import ColorInterp, Resampling
from rasterio.io import MemoryFile
from rasterio.transform import Affine, from_bounds
from rasterio.warp import reproject

from meridian_ingest.imagery.display import display_png
from meridian_ingest.imagery.encode import png_bytes, webp_bytes, world_file
from meridian_ingest.imagery.model import Annotation, Bounds, PublishedFile
from meridian_ingest.imagery.scene import WGS84, Scene, compose
from meridian_ingest.imagery.sources import ImagerySource
from meridian_ingest.imagery.webmercator import (
    TILE_PX,
    tile_bounds,
    tiles_covering,
    zoom_range,
)
from meridian_ingest.raw_store import RawStore

__all__ = ["RenderedScene", "geotiff_bytes", "render_scene", "tile_bytes"]

WEB_MERCATOR = "EPSG:3857"
_COG_OPTIONS = {
    "COMPRESS": "DEFLATE",
    "LEVEL": "9",
    "PREDICTOR": "2",
    "BLOCKSIZE": "256",
    "OVERVIEWS": "AUTO",
    "OVERVIEW_RESAMPLING": "AVERAGE",
    "NUM_THREADS": "1",
}
"""One thread, because a parallel encoder may order its blocks differently."""


@dataclass(frozen=True)
class RenderedScene:
    """What was written, and the facts about the picture a manifest states."""

    source: ImagerySource
    acquired_at: datetime
    bounds: Bounds
    pixel_size_deg: tuple[float, float]
    resolution_m: float
    zoom_min: int
    zoom_max: int
    raw_paths: tuple[str, ...]
    files: tuple[PublishedFile, ...]


def render_scene(  # noqa: PLR0913 — what is drawn, where, and what is marked on it
    store: RawStore,
    raw_paths: Sequence[str],
    bounds: Bounds,
    directory: Path,
    *,
    markers: Sequence[Annotation] = (),
    credits: Sequence[str] = (),
) -> RenderedScene:
    """Write a scene's files into ``directory``, which must not exist yet.

    ``markers`` and ``credits`` reach the display picture alone; the GeoTIFF,
    the PNG and the tiles are the scene's pixels and nothing drawn over them,
    so a GIS tool or the viewer draws markers as layers of its own (D-374).

    Raises:
        RenderError: The raw paths cannot be made into one picture.
        FileExistsError: ``directory`` is already there.
    """
    scene = compose(store, raw_paths, bounds)
    directory.mkdir(parents=True)
    centre = (bounds.south + bounds.north) / 2.0
    zoom_min, zoom_max = zoom_range(scene.resolution_m, centre)
    files = {
        "scene.tif": geotiff_bytes(scene),
        "scene.png": png_bytes(scene.rgba),
        "scene.pgw": world_file(scene.transform).encode("ascii"),
        "display.png": display_png(scene, markers, credits),
    }
    files.update(tile_bytes(scene, zoom_min, zoom_max))
    written = []
    for name in sorted(files):
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(files[name])
        written.append(
            PublishedFile(
                name, hashlib.sha256(files[name]).hexdigest(), len(files[name])
            )
        )
    return RenderedScene(
        source=scene.source,
        acquired_at=scene.acquired_at,
        bounds=scene.bounds,
        pixel_size_deg=scene.pixel_size_deg,
        resolution_m=scene.resolution_m,
        zoom_min=zoom_min,
        zoom_max=zoom_max,
        raw_paths=scene.raw_paths,
        files=tuple(written),
    )


def geotiff_bytes(scene: Scene) -> bytes:
    """The scene as a cloud-optimised GeoTIFF: RGBA, deflated, no date tag."""
    profile = {
        "driver": "GTiff",
        "width": scene.width,
        "height": scene.height,
        "count": 4,
        "dtype": "uint8",
        "crs": WGS84,
        "transform": Affine(*scene.transform),
        "photometric": "RGB",
        "alpha": "YES",
    }
    with MemoryFile() as staged:
        with staged.open(**profile) as target:
            target.write(scene.rgba)
            target.colorinterp = (
                ColorInterp.red,
                ColorInterp.green,
                ColorInterp.blue,
                ColorInterp.alpha,
            )
        with staged.open() as source, MemoryFile() as cog:
            rasterio.shutil.copy(source, cog.name, driver="COG", **_COG_OPTIONS)
            return bytes(cog.read())


def tile_bytes(scene: Scene, zoom_min: int, zoom_max: int) -> dict[str, bytes]:
    """Every tile of the pyramid that shows part of the scene, by its path."""
    tiles = {}
    bounds = scene.bounds
    for z in range(zoom_min, zoom_max + 1):
        for x, y in tiles_covering(bounds, z):
            tile = np.zeros((4, TILE_PX, TILE_PX), dtype=np.uint8)
            reproject(
                source=scene.rgba,
                destination=tile,
                src_transform=Affine(*scene.transform),
                src_crs=WGS84,
                src_alpha=4,
                dst_transform=from_bounds(*tile_bounds(z, x, y), TILE_PX, TILE_PX),
                dst_crs=WEB_MERCATOR,
                dst_alpha=4,
                resampling=Resampling.bilinear,
            )
            if not tile[3].any():
                continue
            tiles[f"tiles/{z}/{x}/{y}.webp"] = webp_bytes(tile)
    return tiles
