"""One station's newest horizon and interference profiles, read back.

Reads ``horizon_profiles`` and ``interference_profiles`` for the public profiles
endpoint (D-174). Split from :mod:`meridian.store.profiles`, which writes them,
and sharing its row types.

**Newest means the latest dataset**, by what it reaches, not by when it was
built. A declared profile is the newest written for each live capability that
declares a mask now; a mask since cleared is not shown, though its rows stay.

Reference: docs/DECISIONS.md D-031, D-174, D-175.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.profiles import DeclaredBin, HorizonBin, InterferenceRow
from meridian.store.stations import Connection

__all__ = [
    "DeclaredProfile",
    "LearnedHorizonProfile",
    "LearnedInterferenceProfile",
    "StationProfileRead",
    "find_station_profiles",
]


@dataclass(frozen=True, slots=True)
class DeclaredProfile:
    """The newest declared profile of one live capability."""

    capability_id: int
    built_at: datetime
    bins: tuple[DeclaredBin, ...]


@dataclass(frozen=True, slots=True)
class LearnedHorizonProfile:
    """One station's newest learned horizon."""

    method: str
    dataset_sha256: bytes
    trained_from: datetime
    trained_until: datetime
    built_at: datetime
    bins: tuple[HorizonBin, ...]


@dataclass(frozen=True, slots=True)
class LearnedInterferenceProfile:
    """One station's newest interference profile."""

    method: str
    dataset_sha256: bytes
    trained_from: datetime
    trained_until: datetime
    built_at: datetime
    cells: tuple[InterferenceRow, ...]


@dataclass(frozen=True, slots=True)
class StationProfileRead:
    """What is held about one station's sky, newest of each kind."""

    declared: tuple[DeclaredProfile, ...]
    """One per live capability whose mask declares something now. A mask since
    cleared is not shown, though its rows stay."""
    learned: LearnedHorizonProfile | None
    interference: LearnedInterferenceProfile | None


@dataclass(frozen=True, slots=True)
class _Build:
    method: str
    dataset_sha256: bytes
    trained_from: datetime
    trained_until: datetime
    built_at: datetime


def find_station_profiles(conn: Connection, station_id: str) -> StationProfileRead:
    """The newest declared, learned and interference profiles of one station."""
    return StationProfileRead(
        declared=_newest_declared(conn, station_id),
        learned=_newest_learned(conn, station_id),
        interference=_newest_interference(conn, station_id),
    )


@dataclass(frozen=True, slots=True)
class _DeclaredRow:
    capability_id: int
    built_at: datetime
    azimuth_deg: float
    azimuth_width_deg: float
    min_elevation_deg: float


def _newest_declared(conn: Connection, station_id: str) -> tuple[DeclaredProfile, ...]:
    with conn.cursor(row_factory=class_row(_DeclaredRow)) as cur:
        cur.execute(
            "select h.capability_id, h.built_at, h.azimuth_deg,"
            " h.azimuth_width_deg, h.min_elevation_deg"
            " from horizon_profiles h"
            " join station_capabilities c on c.id = h.capability_id"
            " where h.station_id = %(station)s and h.source = 'declared'"
            " and c.deleted_at is null and c.horizon_mask_json <> '[]'::jsonb"
            " and h.built_at = (select max(built_at) from horizon_profiles"
            "  where source = 'declared' and capability_id = h.capability_id)"
            " order by h.capability_id, h.azimuth_deg",
            {"station": station_id},
        )
        rows = cur.fetchall()
    grouped: dict[int, tuple[datetime, list[DeclaredBin]]] = {}
    for row in rows:
        held = grouped.setdefault(row.capability_id, (row.built_at, []))
        held[1].append(
            HorizonBin(
                row.azimuth_deg, row.azimuth_width_deg, row.min_elevation_deg, None
            )
        )
    return tuple(
        DeclaredProfile(capability_id=key, built_at=built_at, bins=tuple(bins))
        for key, (built_at, bins) in grouped.items()
    )


def _newest_build(conn: Connection, table: str, station_id: str) -> _Build | None:
    """The learned build of one station in ``table`` from the latest dataset.

    Newest by what the dataset reaches, its ``as_of``, not by when it was
    built: an older dataset built late is not a newer profile. The build time
    and the hash only break ties, so the choice never depends on row order.
    """
    learned_only = " and source = 'learned'" if table == "horizon_profiles" else ""
    with conn.cursor(row_factory=class_row(_Build)) as cur:
        cur.execute(
            "select method, dataset_sha256, trained_from, trained_until, built_at"
            f" from {table} where station_id = %s{learned_only}"
            " order by trained_until desc, built_at desc, dataset_sha256 desc"
            " limit 1",
            (station_id,),
        )
        return cur.fetchone()


def _newest_learned(conn: Connection, station_id: str) -> LearnedHorizonProfile | None:
    build = _newest_build(conn, "horizon_profiles", station_id)
    if build is None:
        return None
    with conn.cursor(row_factory=class_row(HorizonBin)) as cur:
        cur.execute(
            "select azimuth_deg, azimuth_width_deg, min_elevation_deg, sample_count"
            " from horizon_profiles where station_id = %s and source = 'learned'"
            " and method = %s and dataset_sha256 = %s order by azimuth_deg",
            (station_id, build.method, build.dataset_sha256),
        )
        bins = tuple(cur.fetchall())
    return LearnedHorizonProfile(
        method=build.method,
        dataset_sha256=bytes(build.dataset_sha256),
        trained_from=build.trained_from,
        trained_until=build.trained_until,
        built_at=build.built_at,
        bins=bins,
    )


def _newest_interference(
    conn: Connection, station_id: str
) -> LearnedInterferenceProfile | None:
    build = _newest_build(conn, "interference_profiles", station_id)
    if build is None:
        return None
    with conn.cursor(row_factory=class_row(InterferenceRow)) as cur:
        cur.execute(
            "select azimuth_deg, azimuth_width_deg, hour_start, hour_width,"
            " noise_lift_db, station_median_dbfs, sample_count, gain_min_db,"
            " gain_max_db from interference_profiles"
            " where station_id = %s and method = %s and dataset_sha256 = %s"
            " order by azimuth_deg, hour_start",
            (station_id, build.method, build.dataset_sha256),
        )
        cells = tuple(cur.fetchall())
    return LearnedInterferenceProfile(
        method=build.method,
        dataset_sha256=bytes(build.dataset_sha256),
        trained_from=build.trained_from,
        trained_until=build.trained_until,
        built_at=build.built_at,
        cells=cells,
    )
