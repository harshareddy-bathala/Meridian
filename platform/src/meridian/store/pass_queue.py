"""The passes still ahead, as the public API lists them.

A different read from ``meridian.store.passes``' ``find_passes_in_horizon``: that
one partitions passes by acquisition for the scheduler, one station and one
horizon at a time. This one answers "what is coming up" for a reader, across the
network or for one station, and pages through it.

Times and angles come back exact. The public model widens and rounds them on the
way out (D-093), for the same reason ``station_directory`` leaves coordinates to
D-082's serialiser: the rule lives in one place.

**Paged by pass id, not by time.** The keyset is ``(aos, id)``, but the cursor
names only the id and the query looks its ``aos`` up. A cursor carrying the
acquisition time would publish, in base64, the exact instant D-093 just rounded
off the response body.

Reference: docs/DECISIONS.md D-063, D-085, D-093.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = ["QueuedPass", "find_upcoming_passes"]


@dataclass(frozen=True, slots=True)
class QueuedPass:
    """One predicted pass that has not finished yet."""

    id: int
    satellite_id: str
    station_id: str
    aos: datetime
    los: datetime
    max_elevation_deg: float
    max_elevation_at: datetime
    aos_azimuth_deg: float
    los_azimuth_deg: float
    min_elevation_deg: float
    element_set_epoch: datetime
    """The epoch of the elements this pass was computed from. Age is a first-class
    feature everywhere (CLAUDE.md), so a reader can see how stale a prediction is."""
    simulated: bool


def find_upcoming_passes(
    conn: Connection,
    *,
    not_before: datetime,
    station_id: str | None,
    after_pass_id: int | None,
    limit: int,
) -> list[QueuedPass]:
    """One page of passes whose window has not closed, soonest first.

    Args:
        conn: An open connection. Read-only; opens no transaction of its own.
        not_before: Passes that lost signal at or before this instant are over
            and excluded. A pass already in progress is still listed.
        station_id: Only this station's passes, or every station's when ``None``.
        after_pass_id: The last pass on the previous page, or ``None`` for the
            first. A pass id that no longer exists yields an empty page rather
            than restarting from the top.
        limit: The most rows to return; callers ask for one more than they need.

    Returns:
        Passes in ascending ``(aos, id)`` order. Passes of a soft-deleted station
        are excluded, as the station itself is.
    """
    with conn.cursor(row_factory=class_row(QueuedPass)) as cur:
        cur.execute(
            """
            select p.id, p.satellite_id, p.station_id, p.aos, p.los,
                   p.max_elevation_deg, p.max_elevation_at,
                   p.aos_azimuth_deg, p.los_azimuth_deg, p.min_elevation_deg,
                   e.epoch as element_set_epoch, p.simulated
            from passes p
            join stations s on s.station_id = p.station_id
            join element_sets e on e.id = p.element_set_id
            where s.deleted_at is null
              and p.los > %(not_before)s
              and (%(station_id)s::text is null or p.station_id = %(station_id)s)
              and (
                %(after)s::bigint is null
                or (p.aos, p.id) > (select aos, id from passes where id = %(after)s)
              )
            order by p.aos asc, p.id asc
            limit %(limit)s
            """,
            {
                "not_before": not_before,
                "station_id": station_id,
                "after": after_pass_id,
                "limit": limit,
            },
        )
        return cur.fetchall()
