"""Assignments — the SQL layer MSP §4.2's reconciliation and delivery need.

Reads and writes ``assignments`` (``deploy/migrations/sql/0004_passes.sql``).
Like the rest of ``meridian.store``, this module performs no reconciliation
decision itself — it offers mechanical, single-purpose operations that a
later session's orchestration composes. D-008's state machine (``issued``
to ``held`` to ``in_progress`` to ``reported``, or ``issued``/``held``
straight to ``expired``) is enforced by callers choosing which of these
functions to call, not by anything in this file.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row, scalar_row

from meridian.store.stations import Connection

__all__ = [
    "DueAssignment",
    "expire_overdue_assignments",
    "find_assignment_ids_by_state",
    "find_due_assignments",
    "mark_assignment_in_progress",
    "mark_assignments_held",
]


@dataclass(frozen=True, slots=True)
class DueAssignment:
    """Everything MSP §4.3's assignment message needs, one row per assignment.

    The element set is carried inline (``element_set_epoch``, ``_line1``,
    ``_line2``) rather than nested, matching this codebase's convention of
    dataclasses mirroring a query's columns — the wire nesting MSP §4.3
    actually wants is the API layer's job, not this one's.
    """

    assignment_id: str
    satellite_id: str
    start_at: datetime
    end_at: datetime
    centre_freq_hz: int
    mode: str
    expected_max_elevation_deg: float
    predicted_yield: float | None
    element_set_epoch: datetime
    element_set_line1: str
    element_set_line2: str
    timing_uncertainty_s: float
    priority: float

    simulated: bool
    """Carried because CLAUDE.md's fifth rule says simulated data is labelled
    at *every* layer, and an assignment is a layer: the column exists on
    ``assignments`` (``0004_passes.sql``), so a query that omitted it would
    hand the API a message with no way to mark itself."""


def find_assignment_ids_by_state(
    conn: Connection, station_id: str, states: Sequence[str]
) -> set[str]:
    """Every ``assignment_id`` issued to ``station_id`` currently in one of ``states``.

    What a caller diffs a heartbeat's ``held_assignments`` against — MSP
    §4.2's "presented, but never issued to this station" protocol-error
    case is a set difference against this result, not a query of its own.
    """
    with conn.cursor(row_factory=scalar_row) as cur:
        cur.execute(
            """
            select assignment_id from assignments
            where station_id = %s and state = any(%s)
            """,
            (station_id, list(states)),
        )
        return set(cur.fetchall())


def mark_assignments_held(
    conn: Connection, station_id: str, assignment_ids: Sequence[str]
) -> int:
    """``issued -> held`` for exactly the ids named that are currently ``issued``.

    Matching ``state = 'issued'`` in the ``WHERE`` makes this idempotent —
    an id already ``held`` is left untouched, not re-written.

    Returns:
        How many rows transitioned.
    """
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            update assignments
            set state = 'held'
            where station_id = %s and state = 'issued' and assignment_id = any(%s)
            """,
            (station_id, list(assignment_ids)),
        )
        return cur.rowcount


def mark_assignment_in_progress(
    conn: Connection, *, station_id: str, assignment_id: str
) -> bool:
    """``held -> in_progress``.

    The transition D-008 ties to a heartbeat's ``listening`` block
    referencing this assignment.

    Returns:
        Whether the row was ``held`` and transitioned. ``False`` covers an
        unknown id, a foreign one, and one not currently ``held`` alike —
        the caller decides what, if anything, that is worth logging.
    """
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            update assignments
            set state = 'in_progress'
            where station_id = %s and assignment_id = %s and state = 'held'
            """,
            (station_id, assignment_id),
        )
        return cur.rowcount > 0


def expire_overdue_assignments(conn: Connection, station_id: str) -> int:
    """``(issued|held) -> expired`` where ``end_at`` has passed.

    Scoped to one station and called as part of that station's own
    heartbeat — MSP §4.2 describes reconciliation per-heartbeat, and Phase
    1 has no periodic sweep job independent of one.

    Returns:
        How many rows expired.
    """
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            update assignments
            set state = 'expired'
            where station_id = %s and state in ('issued', 'held') and end_at < now()
            """,
            (station_id,),
        )
        return cur.rowcount


def find_due_assignments(
    conn: Connection, station_id: str, *, horizon_end: datetime
) -> list[DueAssignment]:
    """Every assignment eligible for delivery to ``station_id`` (D-026, D-035).

    ``state in ('issued', 'held') and end_at >= now() and start_at <=
    horizon_end``, ordered by ``start_at``. The lower bound is ``end_at``,
    not ``start_at`` — an assignment whose window has opened is the
    station's *current* work and must still be returned.

    Applies **no limit and does no logging**. Capping at 8 and logging
    D-035's "more than 8 eligible" warning belongs to whoever is making the
    delivery decision, not to this query. ``horizon_end`` arrives as a
    parameter rather than being computed here (``now() + 2 hours``) — the
    two-hour figure is D-026's reasoning, which this function does not need
    to know to do its job.
    """
    with conn.cursor(row_factory=class_row(DueAssignment)) as cur:
        cur.execute(
            """
            select
                a.assignment_id,
                p.satellite_id,
                a.start_at,
                a.end_at,
                a.centre_freq_hz,
                a.mode,
                p.max_elevation_deg as expected_max_elevation_deg,
                a.predicted_yield,
                es.epoch as element_set_epoch,
                es.line1 as element_set_line1,
                es.line2 as element_set_line2,
                a.timing_uncertainty_s,
                a.priority,
                a.simulated
            from assignments a
            join passes p on p.id = a.pass_id
            join element_sets es on es.id = p.element_set_id
            where a.station_id = %s
              and a.state in ('issued', 'held')
              and a.end_at >= now()
              and a.start_at <= %s
            order by a.start_at asc
            """,
            (station_id, horizon_end),
        )
        return cur.fetchall()
