"""The job that fills the profile tables: declared masks, and learned profiles.

Two independent halves, run together by ``meridian profiles build`` and by the
jobs service each round (D-174):

* **declared** — each live capability's mask is written as a declared horizon
  profile when it differs from the one last written for that capability, so a
  mask an operator changes appears within a round and the old one stays;
* **learned** — the newest labelled dataset's profiles are computed by
  :func:`meridian.prediction.profiles.station_profiles` and written once. Its
  manifest's hash identifies the build, so a round that finds the dataset
  already built reads one manifest and nothing else.

Neither half feeds prediction. Live scoring still computes its environment from
the dataset in memory (D-157, D-169). These rows are for showing, comparing
across time, and for Stage 27's loss diagnosis to cite.

Reference: docs/DECISIONS.md D-031, D-159, D-174, D-175.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from meridian.prediction.profile_source import newest_dataset, read_profiles
from meridian.prediction.profiles import PROFILE_METHOD, StationProfiles
from meridian.scheduler.declared_horizon import DeclaredMask
from meridian.store.profiles import (
    DeclaredBin,
    HorizonBin,
    InterferenceRow,
    LearnedBuild,
    find_declared_capabilities,
    find_newest_declared_bins,
    insert_declared_bins,
    insert_learned_horizon,
    insert_learned_interference,
    learned_profiles_built,
)
from meridian.store.stations import Connection

__all__ = [
    "DECLARED_METHOD",
    "ProfileBuildReport",
    "build_profiles",
    "declared_bins",
]

DECLARED_METHOD = "declared"
"""A declared profile's method: what the operator said, read as D-175 reads it."""


@dataclass(frozen=True, slots=True)
class ProfileBuildReport:
    """What one build wrote, and what it found already there."""

    declared_capabilities: int
    """Live capabilities that declare a mask."""
    declared_written: int
    """Of those, how many had changed and were written again."""

    dataset: Path | None
    """The newest labelled dataset, or ``None`` if there is none."""
    dataset_as_of: datetime | None
    already_built: bool
    """The newest dataset's profiles were already held, so none were written."""
    stations_built: int
    horizon_rows: int
    interference_rows: int


def declared_bins(points: list[dict[str, float]]) -> list[DeclaredBin]:
    """A stored mask as D-175 reads it: each point's floor to the next, clockwise."""
    mask = DeclaredMask.from_stored(points)
    starts = mask.azimuths
    bins = []
    for index, start in enumerate(starts):
        following = starts[(index + 1) % len(starts)]
        width = (following - start) % 360.0 or 360.0
        bins.append(
            HorizonBin(
                azimuth_deg=start,
                azimuth_width_deg=width,
                min_elevation_deg=mask.floors[index],
                sample_count=None,
            )
        )
    return bins


def build_profiles(conn: Connection, root: Path) -> ProfileBuildReport:
    """Write every changed declared mask and the newest dataset's learned profiles.

    Args:
        conn: An open connection. The caller commits.
        root: The datasets root, where labelled datasets are looked for.

    Raises:
        LiveScoringError: The newest dataset cannot be read or profiled. An
            older one is not built instead.
    """
    capabilities, written = _record_declared(conn)
    newest = newest_dataset(root)
    empty = ProfileBuildReport(
        declared_capabilities=capabilities,
        declared_written=written,
        dataset=None if newest is None else newest.path,
        dataset_as_of=None if newest is None else newest.as_of,
        already_built=False,
        stations_built=0,
        horizon_rows=0,
        interference_rows=0,
    )
    if newest is None:
        return empty
    if learned_profiles_built(conn, newest.sha256):
        return replace(empty, already_built=True)

    source = read_profiles(newest, root=root)
    horizon = interference = built = 0
    for one in source.profiles:
        build = LearnedBuild(
            station_id=one.station_id,
            method=PROFILE_METHOD,
            dataset_sha256=newest.sha256,
            trained_from=source.since,
            trained_until=source.as_of,
        )
        wrote_horizon = insert_learned_horizon(conn, build, _horizon_bins(one))
        interference += insert_learned_interference(
            conn, build, _interference_rows(one)
        )
        horizon += wrote_horizon
        built += 1 if wrote_horizon else 0
    return replace(
        empty,
        stations_built=built,
        horizon_rows=horizon,
        interference_rows=interference,
    )


def _record_declared(conn: Connection) -> tuple[int, int]:
    """Write each changed mask. Returns the masks seen and the ones written."""
    capabilities = find_declared_capabilities(conn)
    written = 0
    for capability in capabilities:
        bins = declared_bins(capability.horizon_mask)
        if bins != find_newest_declared_bins(conn, capability.capability_id):
            insert_declared_bins(conn, capability, bins, method=DECLARED_METHOD)
            written += 1
    return len(capabilities), written


def _horizon_bins(profile: StationProfiles) -> list[HorizonBin]:
    return [
        HorizonBin(
            azimuth_deg=one.azimuth_deg,
            azimuth_width_deg=one.width_deg,
            min_elevation_deg=one.floor_deg,
            sample_count=one.count,
        )
        for one in profile.horizon
    ]


def _interference_rows(profile: StationProfiles) -> list[InterferenceRow]:
    return [
        InterferenceRow(
            azimuth_deg=one.azimuth_deg,
            azimuth_width_deg=one.width_deg,
            hour_start=one.hour_start,
            hour_width=one.hour_width,
            noise_lift_db=one.lift_db,
            station_median_dbfs=profile.median_dbfs,
            sample_count=one.count,
            gain_min_db=one.gain_min_db,
            gain_max_db=one.gain_max_db,
        )
        for one in profile.interference
    ]
