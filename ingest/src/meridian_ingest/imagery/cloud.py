"""How cloudy an area was in one scene, counted from the product's own verdicts.

A scene is chosen by the share of the area its own classification layer calls
cloud. That layer is a data product: each pixel is the processor's verdict,
made from every band before any colour was chosen for display. Reading it is
reading a record. Reading the picture for cloud would be measuring a
rendering, which D-133 forbids, so the visual asset is never consulted here.

**What counts.**

- Sentinel‑2's SCL: classes 8 and 9 (cloud, medium and high probability) and
  10 (thin cirrus) are cloud; 0 (no data) and 1 (saturated or defective) are
  not valid; every other class is clear.
- Landsat's QA_PIXEL: bit 0 (fill) is not valid; bits 1 (dilated cloud),
  2 (cirrus) and 3 (cloud) are cloud; every other valid pixel is clear.

Shadow is not cloud here. The fraction answers "how much of the area is
behind cloud", the same question ``eo:cloud_cover`` answers for a granule.

**Over what.** The window around the area's box, on the scene's own grid:
the box projected and cut outward to whole pixels, so a few pixels beyond the
box are counted too. The granules of one pass are placed on one grid first,
the first valid pixel in item-id order winning where they overlap, so an
overlap is counted once.

Reference: docs/DECISIONS.md D-133, D-372.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from meridian_ingest.imagery.pixels import Pixels, ReadWindow, WindowError

__all__ = [
    "CLASSIFIERS",
    "CLOUD_METHODS",
    "MAX_NODATA_SHARE",
    "CloudCount",
    "count_cloud",
    "placed",
]

MAX_NODATA_SHARE = 0.01
"""The most of a box's window a scene may leave without data and still count
as covering it. A swath edge clipping one corner of the window's margin is not
worth losing a clear scene over; a tenth of the area missing is."""

_SCL_CLOUD = (8, 9, 10)
_SCL_INVALID = (0, 1)
_QA_FILL = 1 << 0
_QA_CLOUD = (1 << 1) | (1 << 2) | (1 << 3)
_ALIGNED = 1e-6
"""Pixels of disagreement allowed between two granules' grid origins."""

CLOUD_METHODS = {
    "scl": (
        "Sentinel-2 L2A scene classification (SCL): classes 8, 9 and 10 (cloud "
        "medium and high probability, thin cirrus) as a share of valid pixels "
        "in the window around the area's box"
    ),
    "qa_pixel": (
        "Landsat Collection 2 QA_PIXEL: bits 1, 2 and 3 (dilated cloud, cirrus, "
        "cloud) as a share of non-fill pixels in the window around the area's box"
    ),
}
"""What each classifier counted, in the words a manifest states (D-372)."""


_Masks = tuple[NDArray[np.bool_], NDArray[np.bool_]]
"""Which pixels are valid, and which of them are cloud."""


@dataclass(frozen=True, slots=True)
class CloudCount:
    """Pixels in a box's window: cloud, clear, and without data."""

    cloud: int
    clear: int
    nodata: int

    @property
    def total(self) -> int:
        """Every pixel in the window."""
        return self.cloud + self.clear + self.nodata

    @property
    def fraction(self) -> float | None:
        """Cloud as a share of valid pixels, or None when none are valid."""
        valid = self.cloud + self.clear
        return self.cloud / valid if valid else None

    @property
    def nodata_share(self) -> float:
        """The share of the window the scene left without data."""
        return self.nodata / self.total if self.total else 1.0

    @property
    def covers(self) -> bool:
        """Whether the scene covers the box, within :data:`MAX_NODATA_SHARE`."""
        return self.nodata_share <= MAX_NODATA_SHARE


def _scl(layer: Pixels) -> _Masks:
    valid = ~np.isin(layer, _SCL_INVALID)
    return valid, valid & np.isin(layer, _SCL_CLOUD)


def _qa_pixel(layer: Pixels) -> _Masks:
    bits = layer.astype(np.uint32)
    valid = (bits & _QA_FILL) == 0
    return valid, valid & ((bits & _QA_CLOUD) != 0)


Classifier = Callable[[Pixels], _Masks]

CLASSIFIERS: dict[str, Classifier] = {"scl": _scl, "qa_pixel": _qa_pixel}
"""Each classifier, from one band, gives which pixels are valid and which cloud."""


def count_cloud(classifier: str, windows: Sequence[ReadWindow]) -> CloudCount:
    """Count cloud over a box from the classification windows of one pass.

    Raises:
        WindowError: The windows are not on one grid, so they cannot be
            placed together without resampling.
        KeyError: ``classifier`` is not one of :data:`CLASSIFIERS`.
    """
    classify = CLASSIFIERS[classifier]
    valid, cloud = placed(windows, classify)
    cloud_count = int(np.count_nonzero(cloud))
    valid_count = int(np.count_nonzero(valid))
    return CloudCount(
        cloud=cloud_count,
        clear=valid_count - cloud_count,
        nodata=int(valid.size) - valid_count,
    )


def placed(windows: Sequence[ReadWindow], classify: Classifier) -> _Masks:
    """The valid and cloud masks of a pass, on the grid of the box's window.

    The first window's box window is the grid. Every other window must share
    its CRS, its pixel size and its box window, which on an aligned grid it
    does; pixels already valid are not overwritten.

    Raises:
        WindowError: No windows, or windows on different grids.
    """
    if not windows:
        message = "a pass with no windows has nothing to count"
        raise WindowError(message)
    first = windows[0]
    rows, cols = first.box_shape
    valid = np.zeros((rows, cols), dtype=bool)
    cloud = np.zeros((rows, cols), dtype=bool)
    for window in windows:
        _same_grid(first, window)
        a, _, c, _, e, f = window.transform
        col = round((c - first.box_transform[2]) / a)
        row = round((f - first.box_transform[5]) / e)
        here_valid, here_cloud = classify(window.array[0])
        target = (slice(row, row + window.height), slice(col, col + window.width))
        fresh = here_valid & ~valid[target]
        cloud[target] |= fresh & here_cloud
        valid[target] |= fresh
    return valid, cloud


def _same_grid(first: ReadWindow, other: ReadWindow) -> None:
    if other.crs != first.crs:
        message = f"{other.href} is in {other.crs}, not {first.crs}"
        raise WindowError(message)
    if other.box_shape != first.box_shape or any(
        abs(mine - theirs) > _ALIGNED * abs(first.box_transform[0])
        for mine, theirs in zip(other.box_transform, first.box_transform, strict=True)
    ):
        message = (
            f"{other.href} is not on {first.href}'s pixel grid, so the two cannot "
            "be placed together without resampling (D-372)"
        )
        raise WindowError(message)
