"""Horizon and interference profiles — stored, versioned, and read back.

Writes and reads ``horizon_profiles`` and ``interference_profiles``
(``deploy/migrations/sql/0023_deferred_storage.sql``). What goes in them is
decided elsewhere: the learned rows by :mod:`meridian.prediction.profiles`, the
declared rows by a capability's mask. This module persists and retrieves.

**A learned profile is identified by the dataset it was built from**, so
:func:`learned_profiles_built` is how a build knows it has nothing to do, and a
unique index refuses a second copy regardless (D-174). **A declared profile is
written when its mask changes**, and the earlier one stays, as every row here
does.

``simulated`` is copied from the station's registry record in the insert
itself, never passed in, so no caller can label a profile other than its
station is labelled (rule 5).

Reference: docs/DECISIONS.md D-031, D-174, D-175.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = [
    "DeclaredBin",
    "DeclaredProfile",
    "HorizonBin",
    "InterferenceRow",
    "LearnedBuild",
    "LearnedHorizonProfile",
    "LearnedInterferenceProfile",
    "StationProfileRead",
    "StoredDeclaredCapability",
    "find_declared_capabilities",
    "find_newest_declared_bins",
    "find_station_profiles",
    "insert_declared_bins",
    "insert_learned_horizon",
    "insert_learned_interference",
    "learned_profiles_built",
]


@dataclass(frozen=True, slots=True)
class LearnedBuild:
    """What every learned row of one build shares."""

    station_id: str
    method: str
    dataset_sha256: bytes
    trained_from: datetime
    trained_until: datetime


@dataclass(frozen=True, slots=True)
class HorizonBin:
    """One bin of a horizon profile: a start, a width and a floor."""

    azimuth_deg: float
    azimuth_width_deg: float
    min_elevation_deg: float
    sample_count: int | None
    """Detections behind a learned bin; ``None`` for a declared one."""


DeclaredBin = HorizonBin
"""A declared bin is a horizon bin with no sample count."""


@dataclass(frozen=True, slots=True)
class InterferenceRow:
    """One interference cell, as it is stored."""

    azimuth_deg: float
    azimuth_width_deg: float
    hour_start: int
    hour_width: int
    noise_lift_db: float
    station_median_dbfs: float | None
    sample_count: int
    gain_min_db: float | None
    gain_max_db: float | None


@dataclass(frozen=True, slots=True)
class StoredDeclaredCapability:
    """A live capability that declares a mask, and the mask as stored."""

    capability_id: int
    station_id: str
    horizon_mask: list[dict[str, float]]


def learned_profiles_built(conn: Connection, dataset_sha256: bytes) -> bool:
    """Whether any learned profile has been built from this dataset."""
    with conn.cursor() as cur:
        cur.execute(
            "select exists (select 1 from horizon_profiles"
            " where source = 'learned' and dataset_sha256 = %(sha)s)"
            " or exists (select 1 from interference_profiles"
            " where dataset_sha256 = %(sha)s)",
            {"sha": dataset_sha256},
        )
        row = cur.fetchone()
    return bool(row and row[0])


def insert_learned_horizon(
    conn: Connection, build: LearnedBuild, bins: Sequence[HorizonBin]
) -> int:
    """Write one station's learned horizon. Returns the rows written.

    A station no longer in the registry gets none: the snapshot outlived it.
    """
    written = 0
    with conn.cursor() as cur:
        for one in bins:
            cur.execute(
                "insert into horizon_profiles (station_id, source, method,"
                " dataset_sha256, trained_from, trained_until, azimuth_deg,"
                " azimuth_width_deg, min_elevation_deg, sample_count, simulated)"
                " select station_id, 'learned', %s, %s, %s, %s, %s, %s, %s, %s,"
                " simulated from stations where station_id = %s",
                (
                    build.method,
                    build.dataset_sha256,
                    build.trained_from,
                    build.trained_until,
                    one.azimuth_deg,
                    one.azimuth_width_deg,
                    one.min_elevation_deg,
                    one.sample_count,
                    build.station_id,
                ),
            )
            written += max(cur.rowcount, 0)
    return written


def insert_learned_interference(
    conn: Connection, build: LearnedBuild, cells: Sequence[InterferenceRow]
) -> int:
    """Write one station's interference cells. Returns the rows written."""
    written = 0
    with conn.cursor() as cur:
        for one in cells:
            cur.execute(
                "insert into interference_profiles (station_id, method,"
                " dataset_sha256, trained_from, trained_until, azimuth_deg,"
                " azimuth_width_deg, hour_start, hour_width, noise_lift_db,"
                " station_median_dbfs, sample_count, gain_min_db, gain_max_db,"
                " simulated)"
                " select station_id, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
                " %s, %s, simulated from stations where station_id = %s",
                (
                    build.method,
                    build.dataset_sha256,
                    build.trained_from,
                    build.trained_until,
                    one.azimuth_deg,
                    one.azimuth_width_deg,
                    one.hour_start,
                    one.hour_width,
                    one.noise_lift_db,
                    one.station_median_dbfs,
                    one.sample_count,
                    one.gain_min_db,
                    one.gain_max_db,
                    build.station_id,
                ),
            )
            written += max(cur.rowcount, 0)
    return written


def find_declared_capabilities(conn: Connection) -> list[StoredDeclaredCapability]:
    """Every live capability of a live station whose mask declares something."""
    with conn.cursor(row_factory=class_row(StoredDeclaredCapability)) as cur:
        cur.execute(
            "select c.id as capability_id, c.station_id,"
            " c.horizon_mask_json as horizon_mask"
            " from station_capabilities c"
            " join stations s on s.station_id = c.station_id"
            " where c.deleted_at is null and s.deleted_at is null"
            " and c.horizon_mask_json <> '[]'::jsonb"
            " order by c.id"
        )
        return cur.fetchall()


def find_newest_declared_bins(
    conn: Connection, capability_id: int
) -> list[DeclaredBin]:
    """The bins of the newest declared profile written for one capability."""
    with conn.cursor(row_factory=class_row(HorizonBin)) as cur:
        cur.execute(
            "select azimuth_deg, azimuth_width_deg, min_elevation_deg, sample_count"
            " from horizon_profiles"
            " where source = 'declared' and capability_id = %(id)s"
            " and built_at = (select max(built_at) from horizon_profiles"
            "  where source = 'declared' and capability_id = %(id)s)"
            " order by azimuth_deg",
            {"id": capability_id},
        )
        return cur.fetchall()


def insert_declared_bins(
    conn: Connection,
    capability: StoredDeclaredCapability,
    bins: Sequence[DeclaredBin],
    *,
    method: str,
) -> int:
    """Write one capability's declared mask as one profile, at one instant.

    The instant is ``clock_timestamp()``, not ``now()``: two masks written for
    one capability inside one transaction would otherwise share a ``built_at``
    and read back as a single profile.
    """
    written = 0
    with conn.cursor() as cur:
        cur.execute("select clock_timestamp()")
        row = cur.fetchone()
        built_at = None if row is None else row[0]
        for one in bins:
            cur.execute(
                "insert into horizon_profiles (station_id, source, method,"
                " capability_id, azimuth_deg, azimuth_width_deg,"
                " min_elevation_deg, built_at, simulated)"
                " select station_id, 'declared', %s, %s, %s, %s, %s, %s, simulated"
                " from stations where station_id = %s",
                (
                    method,
                    capability.capability_id,
                    one.azimuth_deg,
                    one.azimuth_width_deg,
                    one.min_elevation_deg,
                    built_at,
                    capability.station_id,
                ),
            )
            written += max(cur.rowcount, 0)
    return written


# --- reading one station's newest profiles -------------------------------------


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
