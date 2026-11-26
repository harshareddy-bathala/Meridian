"""A window as the raw store keeps it: a GeoTIFF that says where it came from.

The raw store keeps bytes and a manifest of provenance fields every source
shares (D-141). A window needs a little more than those fields can say — the
asset it was cut from, the overview level, the grid — so that record, a
:class:`~.model.SceneWindow`, travels inside the file as a GeoTIFF tag. The
artefact is then self-describing: a render months later needs the raw store
and nothing else (rule 8).

**The same pixels give the same bytes.** Deflate, fixed tiling, no timestamp
tag, and the record serialised canonically, so a window read twice publishes
once and its digest is a statement about the pixels.

Reference: docs/DECISIONS.md D-141, D-372.
"""

from __future__ import annotations

import json
from pathlib import Path

import rasterio
from rasterio.io import MemoryFile
from rasterio.transform import Affine

from meridian_ingest.imagery.model import MalformedImageryError, SceneWindow
from meridian_ingest.imagery.pixels import ReadWindow
from meridian_ingest.raw_manifest import canonical_bytes

__all__ = ["MEDIA_TYPE", "WINDOW_TAG", "read_scene_window", "window_bytes"]

MEDIA_TYPE = "image/tiff"
WINDOW_TAG = "MERIDIAN_SCENE_WINDOW"
"""The GeoTIFF metadata item a window's :class:`~.model.SceneWindow` is in."""

_BLOCK = 256


def window_bytes(window: ReadWindow, record: SceneWindow) -> bytes:
    """The window encoded as a GeoTIFF, its record inside.

    Raises:
        MalformedImageryError: The record does not describe these pixels.
    """
    if (record.width, record.height) != (window.width, window.height):
        message = "the record's size is not the window's"
        raise MalformedImageryError(message)
    if record.transform != window.transform or record.crs != window.crs:
        message = "the record's grid is not the window's"
        raise MalformedImageryError(message)
    bands, height, width = window.array.shape
    profile = {
        "driver": "GTiff",
        "width": width,
        "height": height,
        "count": bands,
        "dtype": str(window.array.dtype),
        "crs": window.crs,
        "transform": Affine(*window.transform),
        "nodata": window.nodata,
        "compress": "deflate",
        "tiled": True,
        "blockxsize": _BLOCK,
        "blockysize": _BLOCK,
    }
    with MemoryFile() as memory:
        with memory.open(**profile) as target:
            target.write(window.array)
            target.update_tags(
                **{WINDOW_TAG: canonical_bytes(record.to_json()).decode("utf-8")}
            )
        return bytes(memory.read())


def read_scene_window(path: Path) -> SceneWindow:
    """The record inside a stored window.

    Raises:
        MalformedImageryError: The file carries no record, or one this version
            cannot read.
    """
    with rasterio.open(path) as source:
        text = source.tags().get(WINDOW_TAG)
    if text is None:
        message = f"{path} carries no {WINDOW_TAG} record"
        raise MalformedImageryError(message)
    try:
        stored = json.loads(text)
    except json.JSONDecodeError as exc:
        message = f"{path}: its {WINDOW_TAG} record is not JSON: {exc}"
        raise MalformedImageryError(message) from exc
    return SceneWindow.from_json(stored)
