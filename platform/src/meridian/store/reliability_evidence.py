"""The rows a pass's classification is decided from.

``meridian.reliability.accounting`` classifies each settled, scheduled pass
from its assignments, their latest reports, the station's heartbeats, and
other receptions of the same satellite nearby in time. This module reads
those rows and decides nothing: the rules are
``meridian.reliability.classification``'s, and listening is
``Registry.was_listening``'s (D-180).

**Only ``scheduled`` decisions are read.** A skip is a row in ``assignments``
too, and nothing was ever delivered for it (D-165).

**Our own receptions only.** An archive is training input and never a runtime
dependency (CLAUDE.md's independence test), so the live accounting judges a
satellite by what Meridian's own stations heard (D-182).

Reference: docs/DECISIONS.md D-146, D-165, D-180, D-182.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = [
    "LatestReport",
    "NearbyReception",
    "SettledAssignment",
    "find_latest_reports",
    "find_receptions_near",
    "find_unclassified_settled",
    "heard_during",
]


@dataclass(frozen=True, slots=True)
class SettledAssignment:
    """A scheduled assignment whose window has settled, with its pass's keys."""

    assignment_id: str
    pass_id: int
    station_id: str
    satellite_id: str
    start_at: datetime
    end_at: datetime
    centre_freq_hz: int
    mode: str
    state: str
    aos: datetime
    los: datetime
    simulated: bool
    """True if the assignment or its pass is simulated."""

    revoked_reason: str | None = None
    """``declined`` or ``offline`` if the platform took it back (D-171)."""


@dataclass(frozen=True, slots=True)
class LatestReport:
    """An assignment's latest observation revision."""

    assignment_id: str
    observation_id: str
    revision: int
    outcome: str
    simulated: bool


@dataclass(frozen=True, slots=True)
class NearbyReception:
    """Another scheduled pass of a satellite that reported, and what it heard."""

    assignment_id: str
    station_id: str
    satellite_id: str
    start_at: datetime
    end_at: datetime
    centre_freq_hz: int
    mode: str
    outcome: str
    simulated: bool
    """True if the pass, the assignment or the report is simulated."""

    max_elevation_deg: float
    """How high the pass climbed, which says how much its silence tells."""


def find_unclassified_settled(
    conn: Connection, *, settled_by: datetime, method: str, config_sha256: bytes
) -> list[SettledAssignment]:
    """Unclassified scheduled assignments that began before ``settled_by``.

    A pass settles when its window, pooled over all of its assignments, closed
    before ``settled_by``. That is the caller's decision, made after pooling:
    an assignment that ended in time may share its rise with one that has not,
    and the two must be classified together or the one reception counts twice.
    Every assignment that could belong to a settled pass began before it
    closed, so this returns those and the caller keeps the pools that closed.

    Args:
        conn: An open connection. Read-only.
        settled_by: The instant a pass's window must have closed before.
        method: The classification method being run.
        config_sha256: The configuration being run.

    Returns:
        Every such assignment, in ``(station, satellite, start, id)`` order,
        so overlapping ones arrive together for pooling.

    Note:
        An assignment is classified if it is among any stored classification's
        ``assignment_ids`` under this method and configuration, not only if it
        is the representative. The containment test ``@>`` is what the GIN
        index on ``assignment_ids`` serves; ``= any(...)`` would scan.
    """
    with conn.cursor(row_factory=class_row(SettledAssignment)) as cur:
        cur.execute(
            """
            select a.assignment_id, a.pass_id, a.station_id, p.satellite_id,
                   a.start_at, a.end_at, a.centre_freq_hz, a.mode, a.state,
                   p.aos, p.los, (a.simulated or p.simulated) as simulated,
                   a.revoked_reason
            from assignments a
            join passes p on p.id = a.pass_id
            where a.decision = 'scheduled'
              and a.start_at < %(settled_by)s
              and not exists (
                  select 1
                  from pass_classifications c
                  where c.method = %(method)s
                    and c.config_sha256 = %(config)s
                    and c.assignment_ids @> array[a.assignment_id]
              )
            order by a.station_id, p.satellite_id, a.start_at, a.assignment_id
            """,
            {"settled_by": settled_by, "method": method, "config": config_sha256},
        )
        return cur.fetchall()


def find_latest_reports(
    conn: Connection, assignment_ids: Sequence[str]
) -> dict[str, LatestReport]:
    """Each assignment's latest observation revision, for those that reported.

    Args:
        conn: An open connection. Read-only.
        assignment_ids: The assignments to look up.

    Returns:
        The latest revision of each that has one, keyed by assignment id.
    """
    with conn.cursor(row_factory=class_row(LatestReport)) as cur:
        cur.execute(
            """
            select assignment_id, observation_id, revision, outcome, simulated
            from observations_current
            where assignment_id = any(%s)
            """,
            (list(assignment_ids),),
        )
        return {one.assignment_id: one for one in cur.fetchall()}


def heard_during(
    conn: Connection, station_id: str, windows: Sequence[tuple[datetime, datetime]]
) -> bool:
    """Whether any heartbeat from the station arrived inside any window.

    Windows are half-open, ``[start, end)``, on ``received_at``, as
    ``Registry.was_listening`` reads them: the platform's clock, never the
    station's (D-056).
    """
    with conn.cursor() as cur:
        for start, end in windows:
            cur.execute(
                """
                select exists (
                    select 1 from heartbeats
                    where station_id = %s and received_at >= %s and received_at < %s
                )
                """,
                (station_id, start, end),
            )
            row = cur.fetchone()
            if row is not None and row[0]:
                return True
    return False


def find_receptions_near(
    conn: Connection,
    *,
    satellite_id: str,
    between: tuple[datetime, datetime],
    excluding: Sequence[str],
    station_reported: bool = False,
) -> list[NearbyReception]:
    """Reported passes of a satellite that began and ended inside ``between``.

    Args:
        conn: An open connection. Read-only.
        satellite_id: The satellite whose other receptions are evidence.
        between: The closed interval a pass must begin and end in, as D-147's
            window reads a pass: its acquisition at or after the start, its
            loss of signal at or before the end.
        excluding: Assignments of the pass being judged, which are not
            evidence about themselves.
        station_reported: Only reports a station sent (``provenance =
            'station'``), never an archive's or a hand-entered one.

    Returns:
        Each reported, scheduled assignment's latest report, in id order.
    """
    start, end = between
    with conn.cursor(row_factory=class_row(NearbyReception)) as cur:
        cur.execute(
            """
            select a.assignment_id, a.station_id, p.satellite_id, a.start_at,
                   a.end_at, a.centre_freq_hz, a.mode, o.outcome,
                   (p.simulated or a.simulated or o.simulated) as simulated,
                   p.max_elevation_deg
            from assignments a
            join passes p on p.id = a.pass_id
            join observations_current o on o.assignment_id = a.assignment_id
            where a.decision = 'scheduled'
              and p.satellite_id = %s
              and p.aos >= %s and p.aos <= %s
              and p.los <= %s
              and not (a.assignment_id = any(%s))
              and (not %s or o.provenance = 'station')
            order by a.assignment_id
            """,
            (satellite_id, start, end, end, list(excluding), station_reported),
        )
        return cur.fetchall()
