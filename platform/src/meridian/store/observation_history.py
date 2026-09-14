"""Recent observations, as the public API lists them.

Read from ``observations_current`` — one row per assignment, its latest revision
(D-015) — so a report corrected twice is listed once, as corrected.

**The projection leaves out what is not for publication.** ``first_detection_at``
and ``doppler_samples`` are exact functions of the pass geometry, and publishing
them would undo D-093's coarsening of the window they sit in. ``client_notes`` is
free text from the station and ``products_json`` names files on it; neither has
been reviewed for publication. None of them is selected, so none can leak.

Paged newest first on ``(started_at, assignment_id)``, with a cursor naming only
the assignment id.

Reference: docs/DATA-MODEL.md ``observations``; docs/DECISIONS.md D-015, D-085,
D-093.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = ["HistoricObservation", "find_recent_observations"]


@dataclass(frozen=True, slots=True)
class HistoricObservation:
    """The current revision of one observation, without its private parts."""

    observation_id: str
    assignment_id: str
    revision: int
    station_id: str
    satellite_id: str
    started_at: datetime
    ended_at: datetime
    outcome: str
    signal_detected: bool
    peak_snr_db: float | None
    provenance: str
    submitted_at: datetime
    simulated: bool


def find_recent_observations(
    conn: Connection,
    *,
    station_id: str | None,
    before_assignment_id: str | None,
    limit: int,
) -> list[HistoricObservation]:
    """One page of observations, most recently started first.

    Args:
        conn: An open connection. Read-only; opens no transaction of its own.
        station_id: Only this station's observations, or all when ``None``.
        before_assignment_id: The last observation's assignment on the previous
            page, or ``None`` for the first.
        limit: The most rows to return; callers ask for one more than they need.

    Returns:
        Observations in descending ``(started_at, assignment_id)`` order. A
        soft-deleted station's are excluded.
    """
    with conn.cursor(row_factory=class_row(HistoricObservation)) as cur:
        cur.execute(
            """
            select o.observation_id, o.assignment_id, o.revision, o.station_id,
                   o.satellite_id, o.started_at, o.ended_at, o.outcome,
                   o.signal_detected, o.peak_snr_db, o.provenance,
                   o.submitted_at, o.simulated
            from observations_current o
            join stations s on s.station_id = o.station_id
            where s.deleted_at is null
              and (%(station_id)s::text is null or o.station_id = %(station_id)s)
              and (
                %(before)s::text is null
                or (o.started_at, o.assignment_id) < (
                  select started_at, assignment_id from observations_current
                  where assignment_id = %(before)s
                )
              )
            order by o.started_at desc, o.assignment_id desc
            limit %(limit)s
            """,
            {"station_id": station_id, "before": before_assignment_id, "limit": limit},
        )
        return cur.fetchall()
