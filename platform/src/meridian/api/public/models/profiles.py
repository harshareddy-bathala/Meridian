"""A station's horizon and interference, as the public API states them.

Turns ``store.profile_reads.StationProfileRead`` into a response body. The declared
horizon is a claim the operator published and the learned one is evidence, so
they are two fields and never one (D-031). Only the declared one constrains
scheduling (D-175).

**What is coarsened, and why.** A capability's mask is published as declared,
as ``capabilities`` already publishes it. A learned horizon was never chosen by
the operator, and a skyline can in principle be matched against terrain, so its
floors are published to whole degrees, as D-093 publishes every other angle.
Interference is published to a tenth of a decibel. ``docs/THREAT-MODEL.md`` §7
reviews both.

Every profile states its provenance: the station's ``simulated`` flag, and for a
learned one the method and the dataset it was built from, by hash (D-174).

This module builds no SQL and opens no connection.
"""

from __future__ import annotations

from datetime import datetime
from typing import Self

from pydantic import BaseModel

from meridian.store.profile_reads import (
    DeclaredProfile,
    LearnedHorizonProfile,
    LearnedInterferenceProfile,
    StationProfileRead,
)
from meridian.store.profiles import HorizonBin, InterferenceRow

__all__ = [
    "PublicDeclaredHorizon",
    "PublicHorizonBin",
    "PublicInterference",
    "PublicInterferenceCell",
    "PublicLearnedHorizon",
    "PublicStationProfiles",
]


class PublicHorizonBin(BaseModel):
    """A floor held from ``azimuth_deg`` for ``azimuth_width_deg``, clockwise."""

    azimuth_deg: float
    azimuth_width_deg: float
    min_elevation_deg: float
    sample_count: int | None
    """Detections behind a learned bin; null for a declared one."""

    @classmethod
    def from_row(cls, row: HorizonBin, *, whole_degrees: bool = False) -> Self:
        """Publish one bin; a learned one's floor to whole degrees."""
        floor = row.min_elevation_deg
        return cls(
            azimuth_deg=row.azimuth_deg,
            azimuth_width_deg=row.azimuth_width_deg,
            min_elevation_deg=float(round(floor)) if whole_degrees else floor,
            sample_count=row.sample_count,
        )


class PublicDeclaredHorizon(BaseModel):
    """What the operator declared for one receiving chain, as last written."""

    built_at: datetime
    bins: list[PublicHorizonBin]

    @classmethod
    def from_row(cls, row: DeclaredProfile) -> Self:
        """Publish one declared profile."""
        return cls(
            built_at=row.built_at,
            bins=[PublicHorizonBin.from_row(one) for one in row.bins],
        )


class PublicLearnedHorizon(BaseModel):
    """What the station's detections say, from one labelled dataset."""

    method: str
    dataset_sha256: str
    trained_from: datetime
    trained_until: datetime
    built_at: datetime
    bins: list[PublicHorizonBin]

    @classmethod
    def from_row(cls, row: LearnedHorizonProfile) -> Self:
        """Publish one learned horizon."""
        return cls(
            method=row.method,
            dataset_sha256=row.dataset_sha256.hex(),
            trained_from=row.trained_from,
            trained_until=row.trained_until,
            built_at=row.built_at,
            bins=[
                PublicHorizonBin.from_row(one, whole_degrees=True) for one in row.bins
            ],
        )


class PublicInterferenceCell(BaseModel):
    """One sector and band of local solar hour: the floor over the median."""

    azimuth_deg: float
    azimuth_width_deg: float
    hour_start: int
    hour_width: int
    noise_lift_db: float
    sample_count: int
    gain_min_db: float | None
    gain_max_db: float | None

    @classmethod
    def from_row(cls, row: InterferenceRow) -> Self:
        """Publish one cell."""
        return cls(
            azimuth_deg=row.azimuth_deg,
            azimuth_width_deg=row.azimuth_width_deg,
            hour_start=row.hour_start,
            hour_width=row.hour_width,
            noise_lift_db=round(row.noise_lift_db, 1),
            sample_count=row.sample_count,
            gain_min_db=row.gain_min_db,
            gain_max_db=row.gain_max_db,
        )


class PublicInterference(BaseModel):
    """A station's noise floor by sky sector and time of day, in dBFS terms."""

    method: str
    dataset_sha256: str
    trained_from: datetime
    trained_until: datetime
    built_at: datetime
    station_median_dbfs: float | None
    """Relative to full scale at the stated gains, not dBm (D-103)."""
    cells: list[PublicInterferenceCell]

    @classmethod
    def from_row(cls, row: LearnedInterferenceProfile) -> Self:
        """Publish one interference profile."""
        medians = {one.station_median_dbfs for one in row.cells}
        return cls(
            method=row.method,
            dataset_sha256=row.dataset_sha256.hex(),
            trained_from=row.trained_from,
            trained_until=row.trained_until,
            built_at=row.built_at,
            station_median_dbfs=_tenth(medians.pop()) if len(medians) == 1 else None,
            cells=[PublicInterferenceCell.from_row(one) for one in row.cells],
        )


class PublicStationProfiles(BaseModel):
    """One station's declared horizon, learned horizon and interference."""

    station_id: str
    simulated: bool
    declared: list[PublicDeclaredHorizon]
    """One per capability that declares a mask now; empty when none does."""
    learned: PublicLearnedHorizon | None
    """Null until a labelled dataset has been built into a profile."""
    interference: PublicInterference | None

    @classmethod
    def from_row(
        cls, station_id: str, simulated: bool, row: StationProfileRead
    ) -> Self:
        """Publish what is held about one station's sky."""
        return cls(
            station_id=station_id,
            simulated=simulated,
            declared=[PublicDeclaredHorizon.from_row(one) for one in row.declared],
            learned=None
            if row.learned is None
            else PublicLearnedHorizon.from_row(row.learned),
            interference=None
            if row.interference is None
            else PublicInterference.from_row(row.interference),
        )


def _tenth(value: float | None) -> float | None:
    """A decibel figure to a tenth, as it is published."""
    return None if value is None else round(value, 1)
