"""Every sector and cell of a station's profiles, for them to be stored (D-174).

:func:`station_profiles` gives each station's learned horizon, all 36 sectors,
and its interference, all 48 cells, as they stand at a dataset's ``as_of``, for
``meridian profiles build`` to write. It places reports and shrinks cells with
:mod:`meridian.prediction.profiles`'s own functions, imported rather than
restated, so a stored profile is the profile a feature read.

It differs from the features in one respect: **a simulated station's profile is
built from its own simulated reports**, and says so. Features never read a
simulated report (D-078), and the stored profiles never feed a feature.

Reference: docs/DECISIONS.md D-078, D-159, D-161, D-174.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median

from meridian.datasets.labels import LabelledPass
from meridian.prediction.feature_rows import FeatureRows, Reading
from meridian.prediction.geometry import sector
from meridian.prediction.profiles import (
    HORIZON_SECTOR_DEG,
    HOUR_BAND_H,
    NOISE_SECTOR_DEG,
    _Heard,
    _informativeness,
    _learned_floor,
    _place,
    _shrunk_lift,
)

__all__ = [
    "HorizonSector",
    "InterferenceCell",
    "StationProfiles",
    "station_profiles",
]


@dataclass(frozen=True, slots=True)
class HorizonSector:
    """One 10° sector of a station's learned horizon."""

    azimuth_deg: float
    width_deg: float
    floor_deg: float
    """The learned floor, at its prior of 0° where nothing was detected."""
    count: int


@dataclass(frozen=True, slots=True)
class InterferenceCell:
    """One sector and band of local solar hour of a station's noise floor."""

    azimuth_deg: float
    width_deg: float
    hour_start: int
    hour_width: int
    lift_db: float
    """Over the station's median, at its prior of 0 dB where nothing was read."""
    count: int
    gain_min_db: float | None
    gain_max_db: float | None
    """The gains the cell's floors were measured at; both null when it is empty."""


@dataclass(frozen=True, slots=True)
class StationProfiles:
    """One station's learned horizon and interference, every cell of each."""

    station_id: str
    simulated: bool
    horizon: tuple[HorizonSector, ...]
    interference: tuple[InterferenceCell, ...]
    median_dbfs: float | None
    """The station's median floor, which each cell's lift is relative to."""


def station_profiles(
    labelled: Iterable[LabelledPass],
    rows: FeatureRows,
    *,
    settle_margin_s: int,
    as_of: datetime,
) -> tuple[StationProfiles, ...]:
    """Every station's profiles from what had settled by ``as_of``, by station id.

    Args:
        labelled: A labelled dataset's passes, of both populations.
        rows: Its raw snapshot's feature rows.
        settle_margin_s: The dataset's settle margin.
        as_of: The dataset's ``as_of``. A report counts once it had settled.

    Returns:
        One entry per station with a labelled pass, whether or not anything
        was heard: a station with no reports has its profile at its prior.
    """
    margin = timedelta(seconds=settle_margin_s)
    heard: dict[str, list[_Heard]] = {}
    simulated: dict[str, bool] = {}
    for one in labelled:
        simulated.setdefault(one.station_id, one.simulated)
        held = heard.setdefault(one.station_id, [])
        report = _own_report(one, rows)
        geometry = rows.geometry.get(one.pass_id)
        if report is None or geometry is None or one.label is None:
            continue
        placed = _place(one, report, geometry, rows, one.los + margin)
        if placed.settled_at <= as_of:
            held.append(placed)
    return tuple(
        StationProfiles(
            station_id=station_id,
            simulated=simulated[station_id],
            horizon=_horizon_sectors(heard[station_id]),
            interference=_interference_cells(heard[station_id]),
            median_dbfs=_median_floor(heard[station_id]),
        )
        for station_id in sorted(heard)
    )


def _own_report(one: LabelledPass, rows: FeatureRows) -> Reading | None:
    """The most informative report of this rise from the pass's own population."""
    readings = [
        reading
        for member in one.pass_ids
        for reading in rows.readings.get(member, ())
        if reading.simulated == one.simulated
    ]
    return min(readings, key=_informativeness, default=None)


def _horizon_sectors(heard: Sequence[_Heard]) -> tuple[HorizonSector, ...]:
    by_sector: dict[int, list[float]] = {}
    for one in heard:
        if one.detection is not None:
            azimuth, elevation = one.detection
            by_sector.setdefault(sector(azimuth, HORIZON_SECTOR_DEG), []).append(
                elevation
            )
    return tuple(
        HorizonSector(
            azimuth_deg=index * HORIZON_SECTOR_DEG,
            width_deg=HORIZON_SECTOR_DEG,
            floor_deg=_learned_floor(by_sector.get(index, [])),
            count=len(by_sector.get(index, [])),
        )
        for index in range(int(360 // HORIZON_SECTOR_DEG))
    )


def _median_floor(heard: Sequence[_Heard]) -> float | None:
    floors = [one.noise_dbfs for one in heard if one.noise_dbfs is not None]
    return median(floors) if floors else None


def _interference_cells(heard: Sequence[_Heard]) -> tuple[InterferenceCell, ...]:
    overall = _median_floor(heard)
    cells = []
    for index in range(int(360 // NOISE_SECTOR_DEG)):
        for band in range(24 // HOUR_BAND_H):
            in_cell = [
                one
                for one in heard
                if one.noise_dbfs is not None and one.noise_cell == (index, band)
            ]
            floors = [one.noise_dbfs for one in in_cell if one.noise_dbfs is not None]
            gains = [one.gain_db for one in in_cell if one.gain_db is not None]
            lift, count = (
                (0.0, 0)
                if overall is None or not floors
                else _shrunk_lift(floors, overall)
            )
            cells.append(
                InterferenceCell(
                    azimuth_deg=index * NOISE_SECTOR_DEG,
                    width_deg=NOISE_SECTOR_DEG,
                    hour_start=band * HOUR_BAND_H,
                    hour_width=HOUR_BAND_H,
                    lift_db=lift,
                    count=count,
                    gain_min_db=min(gains) if count and gains else None,
                    gain_max_db=max(gains) if count and gains else None,
                )
            )
    return tuple(cells)
