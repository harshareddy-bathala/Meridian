"""What a loss diagnosis reads beside the loss itself, from Meridian's own tables.

Every read is the station's own, or the catalogue's, and **as of the loss**
where the table keeps history: a profile built after the pass is not evidence
about it. Nothing here reads an archive (D-102, D-108).

* the station's registered site, which places its passes in its sky;
* its heartbeats near the window, which carry its clock (D-277);
* the median floor of its own observations at one gain (D-275);
* its own earlier receptions, for its loss map (D-274);
* its declared horizon, and the learned interference cell the pass fell in;
* whether the catalogue holds the transmitter active (D-276).

Reference: docs/DECISIONS.md D-102, D-173, D-174, D-272 to D-277.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = [
    "ClockTraces",
    "DeclaredFloor",
    "HistoryRow",
    "InterferenceCellRow",
    "Site",
    "find_clock_traces",
    "find_declared_floors",
    "find_interference_cell",
    "find_noise_baseline",
    "find_site",
    "find_station_history",
    "find_transmitter_active",
]


@dataclass(frozen=True, slots=True)
class Site:
    """Where a station stands, as it registered."""

    lat_deg: float
    lon_deg: float
    alt_m: float


def find_site(conn: Connection, station_id: str) -> Site | None:
    """The station's registered site, or ``None`` for no such station."""
    with conn.cursor(row_factory=class_row(Site)) as cur:
        cur.execute(
            "select lat_deg, lon_deg, alt_m from stations where station_id = %s",
            (station_id,),
        )
        return cur.fetchone()


@dataclass(frozen=True, slots=True)
class ClockTraces:
    """Every trace of a station's clock near one window (D-277)."""

    listening_span: tuple[datetime, datetime] | None
    skews_s: tuple[float, ...]
    reported_offsets: tuple[tuple[float, float | None], ...]


def find_clock_traces(
    conn: Connection,
    *,
    station_id: str,
    assignment_id: str,
    between: tuple[datetime, datetime],
    listening_between: tuple[datetime, datetime],
) -> ClockTraces:
    """The station's heartbeats near a window, as its clock left them.

    Args:
        conn: An open connection.
        station_id: The station.
        assignment_id: The assignment whose listening is looked for, at any
            time: a station whose clock is wrong listens outside the window.
        between: The span heartbeats are read over, by the platform's clock.
        listening_between: The span the assignment's listening is looked for
            in. Wider than ``between``, since a wrong clock listens elsewhere,
            and bounded, so the read stays inside a few chunks of heartbeats.

    Returns:
        When the platform first and last heard the station listening to the
        assignment; ``sent_at − received_at`` of each heartbeat in the span;
        and each reported clock offset there, with its uncertainty.
    """
    with conn.cursor(row_factory=class_row(_Span)) as cur:
        cur.execute(
            "select min(received_at) as first, max(received_at) as last"
            " from heartbeats where station_id = %s and listening_assignment_id = %s"
            " and received_at between %s and %s",
            (station_id, assignment_id, *listening_between),
        )
        span = cur.fetchone()
    with conn.cursor(row_factory=class_row(_Clock)) as cur:
        cur.execute(
            "select extract(epoch from (sent_at - received_at))::float8 as skew_s,"
            " clock_offset_s, clock_uncertainty_s"
            " from heartbeats where station_id = %s"
            " and received_at between %s and %s order by received_at, id",
            (station_id, *between),
        )
        rows = cur.fetchall()
    return ClockTraces(
        listening_span=(
            (span.first, span.last)
            if span is not None and span.first is not None and span.last is not None
            else None
        ),
        skews_s=tuple(row.skew_s for row in rows),
        reported_offsets=tuple(
            (row.clock_offset_s, row.clock_uncertainty_s)
            for row in rows
            if row.clock_offset_s is not None
        ),
    )


@dataclass(frozen=True, slots=True)
class _Span:
    first: datetime | None
    last: datetime | None


@dataclass(frozen=True, slots=True)
class _Clock:
    skew_s: float
    clock_offset_s: float | None
    clock_uncertainty_s: float | None


@dataclass(frozen=True, slots=True)
class _Baseline:
    median: float | None
    count: int


def find_noise_baseline(
    conn: Connection,
    *,
    station_id: str,
    gain_db: float,
    between: tuple[datetime, datetime],
    excluding: str,
) -> tuple[float | None, int]:
    """The median floor of the station's other receptions at this gain.

    Each reception counts once, by its latest revision.

    Returns:
        The median in dBFS, ``None`` with no readings, and how many there were.
    """
    with conn.cursor(row_factory=class_row(_Baseline)) as cur:
        cur.execute(
            # One reading a reception, its latest revision: a resubmitted
            # reception writes a row a revision, and is still one reading.
            "select percentile_cont(0.5) within group (order by noise_floor_dbfs)"
            " as median, count(*) as count from ("
            "  select distinct on (assignment_id) noise_floor_dbfs, receiver_gain_db"
            "  from noise_measurements"
            "  where station_id = %s and source = 'observation'"
            "  and measured_at >= %s and measured_at < %s and assignment_id <> %s"
            "  order by assignment_id, revision desc) latest"
            " where receiver_gain_db = %s",
            (station_id, *between, excluding, gain_db),
        )
        found = cur.fetchone()
    return (None, 0) if found is None else (found.median, found.count)


@dataclass(frozen=True, slots=True)
class HistoryRow:
    """One of the station's earlier receptions, with its samples."""

    assignment_id: str
    element_set_id: int
    started_at: datetime
    noise_floor_dbfs: float | None
    receiver_gain_db: float | None
    snr_samples: list[dict[str, object]]


def find_station_history(
    conn: Connection, *, station_id: str, between: tuple[datetime, datetime]
) -> list[HistoryRow]:
    """The station's current observations that heard something, with samples.

    Only a reception that heard the satellite can show where it lost signal it
    should have kept, so the rest are left in the database.
    """
    with conn.cursor(row_factory=class_row(HistoryRow)) as cur:
        cur.execute(
            "select o.assignment_id, p.element_set_id, o.started_at,"
            " o.noise_floor_dbfs, o.receiver_gain_db, o.snr_samples"
            " from observations_current o"
            " join assignments a on a.assignment_id = o.assignment_id"
            " join passes p on p.id = a.pass_id"
            " where o.station_id = %s and o.started_at >= %s and o.started_at < %s"
            " and o.outcome in ('decoded', 'signal_no_decode')"
            " and jsonb_typeof(o.snr_samples) = 'array'"
            " order by o.started_at, o.assignment_id",
            (station_id, *between),
        )
        return cur.fetchall()


@dataclass(frozen=True, slots=True)
class DeclaredFloor:
    """One declared horizon bin."""

    azimuth_deg: float
    azimuth_width_deg: float
    min_elevation_deg: float


def find_declared_floors(
    conn: Connection, *, station_id: str, at: datetime
) -> list[DeclaredFloor]:
    """The station's declared horizon as it stood at ``at``, every capability's."""
    with conn.cursor(row_factory=class_row(DeclaredFloor)) as cur:
        cur.execute(
            "select h.azimuth_deg, h.azimuth_width_deg, h.min_elevation_deg"
            " from horizon_profiles h"
            " where h.station_id = %(station)s and h.source = 'declared'"
            " and h.built_at = (select max(built_at) from horizon_profiles"
            "  where source = 'declared' and capability_id = h.capability_id"
            "  and built_at <= %(at)s)"
            " order by h.capability_id, h.azimuth_deg",
            {"station": station_id, "at": at},
        )
        return cur.fetchall()


@dataclass(frozen=True, slots=True)
class InterferenceCellRow:
    """The learned interference cell a pass fell in."""

    id: int
    azimuth_deg: float
    azimuth_width_deg: float
    hour_start: int
    hour_width: int
    noise_lift_db: float
    sample_count: int
    gain_min_db: float | None
    gain_max_db: float | None


def find_interference_cell(
    conn: Connection,
    *,
    station_id: str,
    at: datetime,
    azimuth_deg: float,
    hour: int,
) -> InterferenceCellRow | None:
    """The cell holding a direction and an hour, from the newest profile by ``at``.

    Only a profile trained on data that ended by ``at`` counts: one trained
    after the pass would have learned from it.
    """
    with conn.cursor(row_factory=class_row(InterferenceCellRow)) as cur:
        cur.execute(
            "select id, azimuth_deg, azimuth_width_deg, hour_start, hour_width,"
            " noise_lift_db, sample_count, gain_min_db, gain_max_db"
            " from interference_profiles"
            " where station_id = %(station)s and trained_until <= %(at)s"
            " and mod((%(azimuth)s - azimuth_deg + 360)::numeric, 360)"
            "  < azimuth_width_deg"
            " and mod(%(hour)s - hour_start + 24, 24) < hour_width"
            " order by trained_until desc, built_at desc, dataset_sha256 desc"
            " limit 1",
            {"station": station_id, "at": at, "azimuth": azimuth_deg, "hour": hour},
        )
        return cur.fetchone()


def find_transmitter_active(
    conn: Connection, *, satellite_id: str, centre_freq_hz: int, mode: str
) -> bool | None:
    """Whether the catalogue holds the satellite and its transmitter on, now.

    The catalogue keeps only the current state, so this is what it says today,
    and the diagnosis records the value it read. ``None`` for no such
    transmitter.
    """
    with conn.cursor() as cur:
        cur.execute(
            "select s.active and t.active from satellite_transmitters t"
            " join satellites s on s.satellite_id = t.satellite_id"
            " where t.satellite_id = %s and t.centre_freq_hz = %s and t.mode = %s"
            " order by t.deleted_at is not null, t.id limit 1",
            (satellite_id, centre_freq_hz, mode),
        )
        row = cur.fetchone()
    return None if row is None else bool(row[0])
