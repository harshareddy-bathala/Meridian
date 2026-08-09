"""Computed pass windows — the SQL layer under scheduling.

Reads and writes ``passes`` (``deploy/migrations/sql/0004_passes.sql``, keyed by
``0010_pass_identity.sql``). Like the rest of ``meridian.store``, this module
makes no decision about *which* passes are worth computing or which a station
should be given — it stores what the orbit module predicted and reads it back by
station and time.

**A pass is not an observation.** A row here says the geometry allowed a
reception, whether or not anyone attempted one. That is what makes it usable as
the denominator of the completeness ratio in docs/EVALUATION.md §4.1: the number
of opportunities is computed by us and never inferred from what an archive
happens to contain.

Storage only, no propagation and no scheduling: the windows written here come
from ``meridian.orbit`` and are read by the scheduler.

Reference: docs/DATA-MODEL.md; docs/DECISIONS.md D-013 (provenance is copied
from the station), D-063 (what makes two computed passes the same one).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = [
    "NewPass",
    "StoredPass",
    "find_passes_in_horizon",
    "insert_pass",
]


@dataclass(frozen=True, slots=True)
class NewPass:
    """One computed pass window in insertable form.

    ``computed_at`` is absent on purpose — it is the platform's own clock and is
    left to the column default, the same reasoning ``insert_heartbeat`` applies
    to ``received_at``.

    ``simulated`` is present and required. It is the station's provenance, read
    from its registration record by the caller and copied here; a pass computed
    for a simulated station is simulated data at every layer (D-013, CLAUDE.md
    rule 5). There is no default, so a caller cannot forget it and get ``false``.
    """

    satellite_id: str
    station_id: str

    aos: datetime
    los: datetime
    max_elevation_deg: float
    max_elevation_at: datetime
    aos_azimuth_deg: float
    los_azimuth_deg: float

    element_set_id: int
    """Which archived element set produced this window.

    Carried so a timing error measured later can be attributed to the age of the
    set that predicted it rather than to the satellite or the station."""

    min_elevation_deg: float
    """The elevation floor this window was computed against.

    Without it a stored pass is uninterpretable: a 5-degree pass is *absent*
    from a 10-degree search rather than nonexistent, and a completeness ratio
    would silently pool windows computed against different floors."""

    simulated: bool


@dataclass(frozen=True, slots=True)
class StoredPass:
    """One row of ``passes`` as read back.

    Carries ``id`` because ``assignments.pass_id`` references it: an assignment
    names the exact predicted window it was issued against, so a schedule can be
    re-read against the prediction that produced it rather than against whatever
    the current best prediction happens to be.
    """

    id: int
    satellite_id: str
    station_id: str
    aos: datetime
    los: datetime
    max_elevation_deg: float
    max_elevation_at: datetime
    aos_azimuth_deg: float
    los_azimuth_deg: float
    element_set_id: int
    min_elevation_deg: float
    computed_at: datetime
    simulated: bool


def insert_pass(conn: Connection, computed: NewPass) -> bool:
    """Store one computed pass, or report that it was already held.

    Args:
        conn: An open connection.
        computed: The window to store.

    Returns:
        True when a row was written, False when this exact prediction — same
        station, satellite, element set, floor and acquisition — was already
        stored.

    Note:
        **The return value carries the only signal that anything happened.** A
        repeat is not an error and raises nothing, so a job reporting how many
        passes it generated, or deciding whether a horizon needs rescheduling,
        reads that from this boolean or not at all. ``insert_element_set``
        reports a repeat the same way and for the same reason.

        Re-running generation over an unchanged horizon is therefore free, which
        is what makes the job safe to run on a timer and safe to re-run after a
        crash. A *newer element set* is a different prediction and does write a
        second row: the same rise, predicted better, and the difference between
        the two is the measurement that makes element-set age a usable feature
        (D-063).
    """
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            insert into passes
                (satellite_id, station_id, aos, los,
                 max_elevation_deg, max_elevation_at,
                 aos_azimuth_deg, los_azimuth_deg,
                 element_set_id, min_elevation_deg, simulated)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict on constraint pass_prediction_unique do nothing
            """,
            (
                computed.satellite_id,
                computed.station_id,
                computed.aos,
                computed.los,
                computed.max_elevation_deg,
                computed.max_elevation_at,
                computed.aos_azimuth_deg,
                computed.los_azimuth_deg,
                computed.element_set_id,
                computed.min_elevation_deg,
                computed.simulated,
            ),
        )
        return cur.rowcount > 0


def find_passes_in_horizon(
    conn: Connection, station_id: str, start: datetime, end: datetime
) -> list[StoredPass]:
    """Every pass for one station whose acquisition falls in ``[start, end)``.

    Args:
        conn: An open connection. Read-only; opens no transaction of its own.
        station_id: The station the windows were computed for.
        start: Inclusive lower bound on acquisition, timezone-aware UTC.
        end: Exclusive upper bound on acquisition, timezone-aware UTC.

    Returns:
        The matching windows in ascending acquisition order, empty when there
        are none.

    Note:
        **Selected on acquisition, not on overlap**, and half-open — the same
        rule ``OrbitService.pass_windows`` uses to decide which search a pass
        belongs to (D-059). Two adjacent horizons therefore partition the passes
        between them, so a scheduler run per horizon considers each pass exactly
        once rather than re-deciding one that a neighbouring run already placed.

        A pass whose window runs past ``end`` is still returned when it rose
        inside: the window is the pass, and truncating it to the horizon would
        hand the scheduler a shorter reception than the station will actually
        get.
    """
    with conn.cursor(row_factory=class_row(StoredPass)) as cur:
        cur.execute(
            """
            select id, satellite_id, station_id, aos, los,
                   max_elevation_deg, max_elevation_at,
                   aos_azimuth_deg, los_azimuth_deg,
                   element_set_id, min_elevation_deg, computed_at, simulated
            from passes
            where station_id = %s and aos >= %s and aos < %s
            order by aos asc, id asc
            """,
            (station_id, start, end),
        )
        return cur.fetchall()
