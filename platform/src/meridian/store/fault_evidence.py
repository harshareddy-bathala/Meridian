"""The rows a fault verdict is judged from — what the platform itself stored.

Eight reads, each deciding nothing. What a fault was is the run's ledger's, and
never enters the database (D-189); what the platform did about it is here:

* when a station's heartbeats arrived, over a window;
* the work decided for it, with each piece's revocation and whether its pass
  was decided again;
* when each scheduling round judged liveness;
* how each touched assignment's pass was classified;
* which of them have a stored report;
* and, for a fault done to the platform, the first heartbeat and the first
  round after it, and any pass it met classified a miss although reported.

A decision's instant is its run's ``decided_at`` — the ``now`` the scheduler
judged liveness at (D-170) — and only a decision no run recorded falls back to
``issued_at``.

Reference: docs/DECISIONS.md D-170, D-171, D-182, D-192, D-196.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = [
    "FaultWork",
    "Round",
    "find_first_heartbeat_after",
    "find_first_round_after",
    "find_heartbeat_times",
    "find_pass_classes",
    "find_reported",
    "find_reported_misses_between",
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
    offline_revocations: list[datetime]
    """Every time it was revoked because its station was offline, oldest first,
    from ``assignment_revocations``: a reinstatement does not erase one (D-196)."""

    declined_at: datetime | None
    """The first time it was revoked as declined, from the same history."""

    revocations: list[datetime]
    """Every time it was revoked, for any reason, oldest first."""

    reinstatements: list[datetime]
    """Every time it was given back, oldest first."""


@dataclass(frozen=True, slots=True)
class _Instant:
    at: datetime


@dataclass(frozen=True, slots=True)
class _MaybeInstant:
    at: datetime | None


@dataclass(frozen=True, slots=True)
class _Id:
    assignment_id: str


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
                       and b.decision = 'scheduled') as redecided_at,
                   array(select e.at from assignment_revocations e
                     where e.assignment_id = a.assignment_id and e.event = 'revoked'
                       and e.reason = 'offline' order by e.at) as offline_revocations,
                   (select min(e.at) from assignment_revocations e
                     where e.assignment_id = a.assignment_id and e.event = 'revoked'
                       and e.reason = 'declined') as declined_at,
                   array(select e.at from assignment_revocations e
                     where e.assignment_id = a.assignment_id and e.event = 'revoked'
                     order by e.at) as revocations,
                   array(select e.at from assignment_revocations e
                     where e.assignment_id = a.assignment_id
                       and e.event = 'reinstated' order by e.at) as reinstatements
            from assignments a
            left join schedule_runs r on r.run_id = a.schedule_run_id
            where a.station_id = %s and a.decision = 'scheduled'
              and a.end_at > %s and coalesce(r.decided_at, a.issued_at) < %s
            order by a.start_at, a.assignment_id
            """,
            (station_id, start, end),
        )
        return tuple(cur.fetchall())


@dataclass(frozen=True, slots=True)
class Round:
    """One recorded scheduling round: when it judged liveness, and when it wrote."""

    began: datetime
    """The round's ``now``: the instant it judged every station by (D-170)."""

    wrote: datetime | None
    """When its run was written, so its reads were done; ``None`` for a round
    known only by the revocations it made, which records no run."""


def find_rounds_between(
    conn: Connection, *, start: datetime, end: datetime
) -> tuple[Round, ...]:
    """Every recorded scheduling round that began in ``[start, end)``.

    A round leaves a record when it decides something — a run (D-170) — or when
    it revokes an offline station's work (D-196). A round that did neither
    wrote nothing, and a verdict cannot hold the platform to a round it cannot
    see. A run's ``created_at`` says when its reads were done: a round reads
    liveness after it begins, and Stage 21's rehearsal saw ten seconds between.
    """
    with conn.cursor(row_factory=class_row(Round)) as cur:
        cur.execute(
            """
            select began, max(wrote) as wrote from (
                select decided_at as began, created_at as wrote from schedule_runs
                union all
                select at, null from assignment_revocations
                where event = 'revoked' and reason = 'offline'
            ) rounds
            where began >= %s and began < %s
            group by began order by began
            """,
            (start, end),
        )
        return tuple(cur.fetchall())


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


def find_reported(conn: Connection, assignment_ids: Sequence[str]) -> frozenset[str]:
    """Which of the named assignments have a stored report."""
    if not assignment_ids:
        return frozenset()
    with conn.cursor(row_factory=class_row(_Id)) as cur:
        cur.execute(
            "select distinct assignment_id from observations"
            " where assignment_id = any(%s::text[])",
            (list(assignment_ids),),
        )
        return frozenset(one.assignment_id for one in cur.fetchall())


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


def find_reported_misses_between(
    conn: Connection, *, start: datetime, end: datetime
) -> tuple[str, ...]:
    """Passes classified ``confirmed_miss`` although a report of them is stored.

    Only those whose window met ``[start, end)``. A miss with a report is a
    miss the classification made wrongly, whatever else happened.
    """
    with conn.cursor(row_factory=class_row(_PassClass)) as cur:
        cur.execute(
            """
            select c.assignment_id, c.classification from pass_classifications c
            where c.classification = 'confirmed_miss'
              and c.window_start < %s and c.window_end > %s
              and exists (
                select 1 from observations o
                where o.assignment_id = any(c.assignment_ids)
              )
            order by c.assignment_id
            """,
            (end, start),
        )
        return tuple(one.assignment_id for one in cur.fetchall())
