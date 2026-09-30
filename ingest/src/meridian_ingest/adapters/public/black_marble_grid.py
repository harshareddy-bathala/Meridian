"""The arithmetic half of the night-lights source: calendar months and block means.

Kept apart from the adapter so that the one module importing ``h5py`` and
``numpy`` is small, and so the resampling rule — which every number from this
source passes through — can be read on one screen.

Reference: docs/DECISIONS.md D-221, D-226.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import IO, TYPE_CHECKING

from meridian_ingest.normalise.records import NormalisationError
from meridian_ingest.normalise.samples import NormalisedSample

if TYPE_CHECKING:  # numpy arrives with the hdf5 extra, and is only annotated here
    import numpy as np
    from numpy.typing import NDArray

__all__ = ["BLOCK_DEG", "DATASET", "MIN_VALID_SHARE", "block_samples", "months"]

DATASET = "HDFEOS/GRIDS/VIIRS_Grid_DNB_2d/Data Fields/NearNadir_Composite_Snow_Free"
BLOCK_DEG = 0.1
MIN_VALID_SHARE = 0.5
TILE_DEG = 10.0
UNIT = "nW cm-2 sr-1"
PRODUCT = "VNP46A3.002 NearNadir_Composite_Snow_Free, 0.1 deg block mean"


def months(since: datetime, until: datetime) -> list[tuple[datetime, datetime]]:
    """Each calendar month ``[since, until)`` touches, as ``[start, next start)``."""
    first = since.astimezone(UTC)
    start = datetime(first.year, first.month, 1, tzinfo=UTC)
    out = []
    while start < until:
        following = (start + timedelta(days=32)).replace(day=1)
        out.append((start, following))
        start = following
    return out


def block_samples(
    handle: IO[bytes],
    tile: tuple[int, int],
    month: datetime,
    published: tuple[datetime, str],
) -> tuple[NormalisedSample, ...]:
    """One sample per 0.1° block of one granule's grid.

    Args:
        handle: The granule's bytes.
        tile: Its ``(h, v)`` from its name.
        month: The first instant of the month it composites.
        published: When it became available, and on what basis (D-222).

    Raises:
        NormalisationError: ``h5py`` is not installed, the grid is absent, or
            its shape is not a whole number of blocks.
    """
    grid, fill, scale = _read(handle)
    rows, cols = grid.shape
    per_block = round(rows * BLOCK_DEG / TILE_DEG)
    if per_block < 1 or rows % per_block or cols % per_block or rows != cols:
        message = f"a {rows} x {cols} grid is not whole {BLOCK_DEG} degree blocks"
        raise NormalisationError(message)
    valid = grid != fill
    blocks = rows // per_block
    shape = (blocks, per_block, blocks, per_block)
    counts = valid.reshape(shape).sum(axis=(1, 3))
    sums = (grid.astype("float64") * valid).reshape(shape).sum(axis=(1, 3))
    west, north = -180.0 + TILE_DEG * tile[0], 90.0 - TILE_DEG * tile[1]
    following = (month + timedelta(days=32)).replace(day=1)
    out = []
    for row in range(blocks):
        for col in range(blocks):
            count = int(counts[row, col])
            value = None
            if count >= MIN_VALID_SHARE * per_block * per_block:
                value = round(float(sums[row, col]) / count * scale, 4)
            out.append(
                NormalisedSample(
                    series_key=f"h{tile[0]:02d}v{tile[1]:02d}:{row},{col}",
                    quantity="night_lights_radiance",
                    value_unit=UNIT,
                    observed_from=month,
                    observed_to=following,
                    published_at=published[0],
                    published_basis=published[1],
                    product=PRODUCT,
                    value=value,
                    missing_reason=None
                    if value is not None
                    else f"{count} of {per_block * per_block} pixels valid",
                    lat_deg=round(north - (row + 0.5) * BLOCK_DEG, 6),
                    lon_deg=round(west + (col + 0.5) * BLOCK_DEG, 6),
                    footprint_m=BLOCK_DEG * 111_195.0,
                    quality=f"pixels={count}",
                )
            )
    return tuple(out)


def _read(handle: IO[bytes]) -> tuple[NDArray[np.uint16], float, float]:
    """The grid, its fill value and its scale, read with the ``hdf5`` extra."""
    try:
        import h5py  # noqa: PLC0415 — the optional extra, imported only here
    except ImportError as exc:
        message = (
            "night-time lights are HDF5; install meridian-ingest[hdf5] to "
            "normalise them (D-226)"
        )
        raise NormalisationError(message) from exc
    try:
        with h5py.File(handle, "r") as granule:
            dataset = granule[DATASET]
            grid = dataset[()]
            fill = float(dataset.attrs.get("_FillValue", 65535))
            scale = float(dataset.attrs.get("scale_factor", 1.0))
    except (KeyError, OSError) as exc:
        message = f"the granule has no {DATASET}: {exc}"
        raise NormalisationError(message) from exc
    return grid, fill, scale
