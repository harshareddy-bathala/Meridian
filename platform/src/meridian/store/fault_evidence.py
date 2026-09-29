"""The rows a fault verdict is judged from — what the platform itself stored.

Seven reads, each deciding nothing. What a fault was is the run's ledger's, and
never enters the database (D-189); what the platform did about it is here:

* when a station's heartbeats arrived, over a window;
* the work decided for it, with each piece's revocation and whether its pass
  was decided again;
* when each scheduling round judged liveness;
* how each touched assignment's pass was classified;
* and, for a fault done to the platform, the first heartbeat and the first
  round after it, and the confirmed misses whose windows met it.

A decision's instant is its run's ``decided_at`` — the ``now`` the scheduler
judged liveness at (D-170) — and only a decision no run recorded falls back to
``issued_at``.

Reference: docs/DECISIONS.md D-170, D-171, D-182, D-192.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = [
    "FaultWork",
    "find_confirmed_misses_between",
    "find_first_heartbeat_after",
    "find_first_round_after",
    "find_heartbeat_times",
    "find_pass_classes",
    "find_rounds_between",
    "find_station_work",
]


@dataclass(frozen=True, slots=True)
class FaultWork:
    """One scheduled assignment of a station, as a fault verdict reads it."""

    assignment_id: str
    pass_id: int
    decided_at: datetime
    start_at: datetime
    end_at: datetime
    state: str
    revoked_reason: str | None
    revoked_at: datetime | None
    redecided_at: datetime | None


@dataclass(frozen=True, slots=True)
class _Instant:
    at: datetime


@dataclass(frozen=True, slots=True)
class _MaybeInstant:
    at: datetime | None


@dataclass(frozen=True, slots=True)
class _PassClass:
    assignment_id: str
    classification: str


def find_heartbeat_times(
    conn: Connection, station_id: str, *, start: datetime, end: datetime
) -> tuple[datetime, ...]:
    """When the station's heartbeats were received in ``[start, end)``, oldest first."""
    with conn.cursor(row_factory=class_row(_Instant)) as cur:
        cur.execute(
            """
            select received_at as at from heartbeats
            where station_id = %s and received_at >= %s and received_at < %s
            order by received_at
            """,
            (station_id, start, end),
        )
        return tuple(one.at for one in cur.fetchall())


def find_station_work(
    conn: Connection, station_id: str, *, start: datetime, end: datetime
) -> tuple[FaultWork, ...]:
    """Scheduled work decided before ``end`` whose window ends after ``start``.

    Each with when a later revision of its pass was decided, if one was.
    """
    with conn.cursor(row_factory=class_row(FaultWork)) as cur:
        cur.execute(
            """
            select a.assignment_id, a.pass_id,
                   coalesce(r.decided_at, a.issued_at) as decided_at,
                   a.start_at, a.end_at, a.state, a.revoked_reason, a.revoked_at,
                   (select min(coalesce(rb.decided_at, b.issued_at))
                      from assignments b
                      left join schedule_runs rb on rb.run_id = b.schedule_run_id
                     where b.pass_id = a.pass_id
                       and b.model_config is not distinct from a.model_config
                       and b.revision > a.revision
                       and b.decision = 'scheduled') as redecided_at
            from assignments a
            left join schedule_runs r on r.run_id = a.schedule_run_id
            where a.station_id = %s and a.decision = 'scheduled'
              and a.end_at > %s and coalesce(r.decided_at, a.issued_at) < %s
            order by a.start_at, a.assignment_id
            """,
            (station_id, start, end),
        )
        return tuple(cur.fetchall())


def find_rounds_between(
    conn: Connection, *, start: datetime, end: datetime
) -> tuple[datetime, ...]:
    """When each scheduling round in ``[start, end)`` judged liveness."""
    with conn.cursor(row_factory=class_row(_Instant)) as cur:
        cur.execute(
            """
            select decided_at as at from schedule_runs
            where decided_at >= %s and decided_at < %s order by decided_at
            """,
            (start, end),
        )
        return tuple(one.at for one in cur.fetchall())


def find_pass_classes(
    conn: Connection, assignment_ids: Sequence[str]
) -> dict[str, str]:
    """How the pass of each named assignment was classified, where it was.

    A pass classified under several methods or configurations reads as
    ``confirmed_miss`` if any of them says so: a verdict asking whether a fault
    produced a false miss must not be answered by whichever row came last.
    """
    if not assignment_ids:
        return {}
    wanted = list(assignment_ids)
    with conn.cursor(row_factory=class_row(_PassClass)) as cur:
        cur.execute(
            """
            select one.assignment_id, c.classification
            from pass_classifications c
            cross join lateral unnest(c.assignment_ids) as one (assignment_id)
            where c.assignment_ids && %s::text[]
              and one.assignment_id = any(%s::text[])
            order by c.classification = 'confirmed_miss', c.classification_id
            """,
            (wanted, wanted),
        )
        return {one.assignment_id: one.classification for one in cur.fetchall()}


def find_first_heartbeat_after(conn: Connection, at: datetime) -> datetime | None:
    """The first heartbeat from any station received at or after ``at``."""
    with conn.cursor(row_factory=class_row(_MaybeInstant)) as cur:
        cur.execute(
            "select min(received_at) as at from heartbeats where received_at >= %s",
            (at,),
        )
        row = cur.fetchone()
        return None if row is None else row.at


def find_first_round_after(conn: Connection, at: datetime) -> datetime | None:
    """The first scheduling round that judged liveness at or after ``at``."""
    with conn.cursor(row_factory=class_row(_MaybeInstant)) as cur:
        cur.execute(
            "select min(decided_at) as at from schedule_runs where decided_at >= %s",
            (at,),
        )
        row = cur.fetchone()
        return None if row is None else row.at


def find_confirmed_misses_between(
    conn: Connection, *, start: datetime, end: datetime
) -> tuple[str, ...]:
    """Assignments classified ``confirmed_miss`` whose window met ``[start, end)``."""
    with conn.cursor(row_factory=class_row(_PassClass)) as cur:
        cur.execute(
            """
            select assignment_id, classification from pass_classifications
            where classification = 'confirmed_miss'
              and window_start < %s and window_end > %s
            order by assignment_id
            """,
            (end, start),
        )
        return tuple(one.assignment_id for one in cur.fetchall())
