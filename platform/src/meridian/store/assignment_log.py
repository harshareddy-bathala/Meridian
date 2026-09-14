"""Assignments and the reasons behind them, as the public API lists them.

``meridian.store.assignments`` serves the MSP side: what to deliver to a station
and how its state moves. This module is the reader's side — every decision the
scheduler recorded, *including the passes it skipped*, with the reason, the score
and the pass it lost to. ``DATA-MODEL.md`` calls ``reason`` human-readable and
shown on the dashboard; this is the read that shows it.

A skipped pass is listed on purpose. "Why was this not scheduled" is the question
a schedule exists to answer, and hiding skips would publish only the half of each
decision that looks like success.

Windows come back exact; the public model widens them (D-093). Paging is keyset
on ``(start_at, assignment_id)`` with a cursor naming only the assignment id, so
the cursor cannot carry a time the body rounded.

Reference: docs/DATA-MODEL.md ``assignments``; docs/DECISIONS.md D-008, D-085,
D-093.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = ["LoggedAssignment", "find_assignment", "find_assignments"]

_LOG_COLUMNS = """
    a.assignment_id, a.pass_id, a.station_id, p.satellite_id,
    a.issued_at, a.start_at, a.end_at, a.centre_freq_hz, a.mode,
    a.timing_uncertainty_s, a.priority, a.predicted_yield,
    a.decision, a.reason, a.score, a.conflicts_with_assignment_id,
    a.model_config, a.state, a.simulated
"""
"""Shared by the list and the detail read, so the two cannot disagree."""


@dataclass(frozen=True, slots=True)
class LoggedAssignment:
    """One scheduling decision, scheduled or skipped."""

    assignment_id: str
    pass_id: int
    station_id: str
    satellite_id: str
    issued_at: datetime
    start_at: datetime
    end_at: datetime
    centre_freq_hz: int
    mode: str
    timing_uncertainty_s: float
    priority: float
    predicted_yield: float | None
    decision: str
    reason: str
    score: float | None
    conflicts_with_assignment_id: str | None
    model_config: str | None
    state: str
    simulated: bool


def find_assignments(  # noqa: PLR0913 — three filters, a cursor and a page size
    conn: Connection,
    *,
    not_before: datetime,
    station_id: str | None,
    decision: str | None,
    after_assignment_id: str | None,
    limit: int,
) -> list[LoggedAssignment]:
    """One page of decisions whose window has not closed, soonest first.

    Args:
        conn: An open connection. Read-only; opens no transaction of its own.
        not_before: Decisions whose window ended at or before this are history
            and excluded. One in progress is still listed.
        station_id: Only this station's decisions, or all when ``None``.
        decision: ``scheduled`` or ``skipped`` only, or both when ``None``.
        after_assignment_id: The last assignment on the previous page, or
            ``None`` for the first.
        limit: The most rows to return; callers ask for one more than they need.

    Returns:
        Decisions in ascending ``(start_at, assignment_id)`` order. A soft-deleted
        station's are excluded.
    """
    with conn.cursor(row_factory=class_row(LoggedAssignment)) as cur:
        cur.execute(
            f"""
            select {_LOG_COLUMNS}
            from assignments a
            join passes p on p.id = a.pass_id
            join stations s on s.station_id = a.station_id
            where s.deleted_at is null
              and a.end_at > %(not_before)s
              and (%(station_id)s::text is null or a.station_id = %(station_id)s)
              and (%(decision)s::text is null or a.decision = %(decision)s)
              and (
                %(after)s::text is null
                or (a.start_at, a.assignment_id) > (
                  select start_at, assignment_id from assignments
                  where assignment_id = %(after)s
                )
              )
            order by a.start_at asc, a.assignment_id asc
            limit %(limit)s
            """,
            {
                "not_before": not_before,
                "station_id": station_id,
                "decision": decision,
                "after": after_assignment_id,
                "limit": limit,
            },
        )
        return cur.fetchall()


def find_assignment(conn: Connection, assignment_id: str) -> LoggedAssignment | None:
    """One decision by id, past or upcoming, or ``None``.

    A soft-deleted station's assignment is ``None`` too, for the reason
    ``station_directory.find_station`` gives: an id that was once in use is not
    confirmed to have been.
    """
    with conn.cursor(row_factory=class_row(LoggedAssignment)) as cur:
        cur.execute(
            f"""
            select {_LOG_COLUMNS}
            from assignments a
            join passes p on p.id = a.pass_id
            join stations s on s.station_id = a.station_id
            where a.assignment_id = %s and s.deleted_at is null
            """,
            (assignment_id,),
        )
        return cur.fetchone()
