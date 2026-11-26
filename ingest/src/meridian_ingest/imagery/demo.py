"""The demonstration's scene: a synthetic archive, searched and drawn like a real one.

A rehearsal runs with no network, and still has to show the Imagery page doing
what it does for a real scene (D-378). So ``imagery demo`` writes a small
archive of its own into a scratch directory — a catalogue answer naming one
item, and that item's ``visual`` and ``scl`` rasters on a UTM grid around the
area's box — and then searches it through the same :func:`search_area` a real
search uses, with the catalogue and the pixels served from those files. What
is kept, counted, rendered and published is then made by the real pipeline,
step for step.

**Labelled at every step.** The archive is :data:`SIMULATED_SOURCE`'s, which
no network holds and ``search`` refuses. Its credit, its caption, its manifest,
the index and the API all say the scene is simulated, and the dashboard badges
it (rule 5). The pixels are a plain pattern of bands, which no reader could
take for a place.

Reference: docs/DECISIONS.md D-142, D-372, D-375, D-378.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, time, timedelta
from pathlib import Path

import numpy as np
import rasterio
from numpy.typing import NDArray
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds

from meridian_ingest.imagery.areas import PublishedArea
from meridian_ingest.imagery.outcome import SceneSearch, SearchContext
from meridian_ingest.imagery.pixels import LocalPixels
from meridian_ingest.imagery.search import search_area
from meridian_ingest.imagery.sources import SIMULATED_SOURCE
from meridian_ingest.imagery.stac import search_artefact
from meridian_ingest.politeness import RequestBudget
from meridian_ingest.raw_store import RawStore
from meridian_ingest.retrieval import MappedFixtureRetriever

__all__ = ["demo_search", "utm_epsg"]

_MARGIN_M = 500.0
_VISUAL_M = 10.0
_SCL_M = 20.0
_VEGETATION = 4
"""SCL's class for vegetation: clear ground, so the scene is chosen."""
_ACQUIRED = time(5, 30)
"""A morning pass, as Sentinel-2's over India is."""


def utm_epsg(lon: float, lat: float) -> int:
    """The WGS 84 UTM zone a point lies in, as an EPSG code."""
    zone = min(60, int((lon + 180.0) // 6.0) + 1)
    return (32600 if lat >= 0 else 32700) + zone


def demo_search(
    store: RawStore, area: PublishedArea, now: datetime, scratch: Path
) -> SceneSearch:
    """Write the synthetic archive under ``scratch`` and search it for ``area``.

    The item is dated the previous UTC day at 05:30, so a refresh on the same
    day finds the same scene and publishes nothing new.
    """
    epsg = utm_epsg(
        (area.bounds.west + area.bounds.east) / 2,
        (area.bounds.south + area.bounds.north) / 2,
    )
    acquired = datetime.combine((now - timedelta(days=1)).date(), _ACQUIRED, now.tzinfo)
    item_id = f"SIMULATED_{area.area_id}_{acquired:%Y%m%d}"
    base = f"https://simulated.invalid/{item_id}"
    files = {
        f"{base}/visual.tif": _raster(
            scratch / f"{item_id}-visual.tif", area, epsg, _VISUAL_M, bands=3
        ),
        f"{base}/scl.tif": _raster(
            scratch / f"{item_id}-scl.tif", area, epsg, _SCL_M, bands=1
        ),
    }
    answer = scratch / "search.json"
    answer.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [_item(item_id, base, epsg, area, acquired)],
            }
        ),
        encoding="utf-8",
    )
    context = SearchContext(
        retriever=MappedFixtureRetriever(
            {search_artefact(SIMULATED_SOURCE, area.bounds, now).url: answer}
        ),
        pixels=LocalPixels(files),
        store=store,
        budget=RequestBudget(10),
        now=now,
    )
    return search_area(SIMULATED_SOURCE, area, context)


def _item(
    item_id: str, base: str, epsg: int, area: PublishedArea, acquired: datetime
) -> dict[str, object]:
    box = area.bounds
    return {
        "type": "Feature",
        "id": item_id,
        "bbox": [box.west - 0.1, box.south - 0.1, box.east + 0.1, box.north + 0.1],
        "properties": {
            "datetime": f"{acquired:%Y-%m-%dT%H:%M:%SZ}",
            "platform": "simulated",
            "proj:epsg": epsg,
            "s2:datatake_id": item_id,
            "eo:cloud_cover": 0.0,
            "updated": f"{acquired:%Y-%m-%dT%H:%M:%SZ}",
        },
        "assets": {name: {"href": f"{base}/{name}.tif"} for name in ("visual", "scl")},
    }


def _raster(
    path: Path, area: PublishedArea, epsg: int, metres: float, *, bands: int
) -> Path:
    """A GeoTIFF on a grid aligned to its pixel size, a margin around the box."""
    crs = f"EPSG:{epsg}"
    left, bottom, right, top = transform_bounds(
        "EPSG:4326", crs, *area.bounds.to_list(), densify_pts=21
    )
    x0 = math.floor((left - _MARGIN_M) / metres) * metres
    y0 = math.ceil((top + _MARGIN_M) / metres) * metres
    width = math.ceil((right + _MARGIN_M - x0) / metres)
    height = math.ceil((y0 - (bottom - _MARGIN_M)) / metres)
    if bands == 1:
        pixels = np.full((1, height, width), _VEGETATION, dtype=np.uint8)
    else:
        pixels = _bands(height, width)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=width,
        height=height,
        count=bands,
        dtype="uint8",
        crs=crs,
        transform=from_origin(x0, y0, metres, metres),
        nodata=0,
        tiled=True,
        blockxsize=256,
        blockysize=256,
    ) as target:
        target.write(pixels)
    return path


def _bands(height: int, width: int) -> NDArray[np.uint8]:
    """Soft diagonal bands of green and brown: plainly not a place."""
    rows, cols = np.mgrid[0:height, 0:width]
    wave = np.sin((rows + cols) / 90.0)
    red = 120 + 40 * wave
    green = 135 + 30 * np.cos(rows / 140.0)
    blue = 90 + 25 * wave
    return np.stack([red, green, blue]).clip(1, 255).astype(np.uint8)
