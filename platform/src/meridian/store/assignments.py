"""Assignments — the SQL layer MSP §4.2's reconciliation and delivery need.

Reads and writes ``assignments`` (``deploy/migrations/sql/0004_passes.sql``).
Like the rest of ``meridian.store``, this module performs no reconciliation
decision itself — it offers mechanical, single-purpose operations for a caller
to compose. D-008's state machine (``issued`` to ``held`` to ``in_progress`` to
``reported``, or ``issued``/``held`` straight to ``expired``) is enforced by
callers choosing which of these functions to call, not by anything in this file.

``meridian.api.msp`` composes most of this file into one transaction per
heartbeat: it reconciles what the station reported, applies the transitions
:mod:`meridian.registry.heartbeat_reconciliation` derived, expires what the
station has stopped naming, and reads back what is due.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row, scalar_row

from meridian.store.stations import Connection

__all__ = [
    "DueAssignment",
    "NewAssignment",
    "expire_overdue_assignments",
    "find_assignment_ids_by_state",
    "find_due_assignments",
    "insert_assignments",
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


def expire_overdue_assignments(
    conn: Connection, station_id: str, *, still_held: Sequence[str]
) -> int:
    """``(issued|held) -> expired`` for overdue rows the station no longer names.

    Args:
        conn: An open connection. This function owns its transaction.
        station_id: Whose assignments to sweep.
        still_held: The ``held_assignments`` from the heartbeat driving this
            call. Rows named here are exempt however overdue they are.

    Returns:
        How many rows expired.

    Note:
        **Being overdue is not enough** (D-067). MSP §4.2 defines this row of its
        table as *absent, window has passed*, and D-008 defines ``expired`` as
        the station never having taken the work. A station still naming an
        assignment after its window closed took the work and is finishing with
        it — expiring that row would both misreport what happened and strand the
        observation, because D-008 has no arc from ``expired`` to ``reported``.

        Scoped to one station and called as part of that station's own heartbeat.
        A station that stops heartbeating altogether therefore never has its
        overdue rows swept; Phase 1 has no periodic job independent of a
        heartbeat, and D-067 records that sweep as owed by the reliability stage.
    """
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            update assignments
            set state = 'expired'
            where station_id = %s
              and state in ('issued', 'held')
              and end_at < now()
              and not (assignment_id = any(%s))
            """,
            (station_id, list(still_held)),
        )
        return cur.rowcount


def find_due_assignments(
    conn: Connection, station_id: str, *, horizon_end: datetime
) -> list[DueAssignment]:
    """Every assignment eligible for delivery to ``station_id`` (D-026, D-035, D-067).

    ``state in ('issued', 'held', 'in_progress') and end_at >= now() and
    start_at <= horizon_end``, ordered by ``start_at``. The lower bound is
    ``end_at``, not ``start_at`` — an assignment whose window has opened is the
    station's *current* work and must still be returned.

    ``in_progress`` is in the predicate for the same reason (D-067). Without it
    an assignment disappears from the response the moment the station reports
    listening on it, so a station that reboots mid-pass is told it has nothing to
    do — the failure D-035 fixed by moving the lower bound, arriving again
    through a different column. MSP §4.2's prose is the arbiter: a station sees
    an assignment on every heartbeat until it is reported or its window has
    passed, and executing it is neither.

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
              and a.state in ('issued', 'held', 'in_progress')
              and a.end_at >= now()
              and a.start_at <= %s
            order by a.start_at asc
            """,
            (station_id, horizon_end),
        )
        return cur.fetchall()


@dataclass(frozen=True, slots=True)
class NewAssignment:
    """One scheduling decision in insertable form — taken or skipped.

    A skip is a row here, not an absence. The scheduler considered the pass and
    decided against it, and docs/PROJECT.md §13 calls the screen that explains
    those decisions "the entire project" — so a skipped pass that left no row
    would be unrecoverable the moment the run ended.

    ``issued_at`` is absent on purpose: it is the platform's own clock and is
    left to the column default, the same reasoning ``insert_heartbeat`` applies
    to ``received_at``.
    """

    assignment_id: str
    pass_id: int
    station_id: str

    start_at: datetime
    end_at: datetime
    """The *assignment's* window, wider than the pass (D-021).

    Opened out by the platform's stated timing uncertainty, because a station
    recording from exactly the predicted acquisition starts after a pass whose
    element set was stale has already begun.
    """

    centre_freq_hz: int
    mode: str
    timing_uncertainty_s: float

    decision: str
    """``scheduled`` or ``skipped`` — what the scheduler wanted."""

    reason: str
    model_config: str
    score: float
    """What the ranking function returned. Read with ``model_config`` or not at
    all: the unit differs by configuration (D-065)."""

    conflicts_with_assignment_id: str | None
    """For a skip, the assignment that took the slot. ``None`` otherwise."""

    priority: float
    simulated: bool
    """Copied from the pass, which copied it from the station (D-013)."""


def insert_assignments(conn: Connection, decisions: Sequence[NewAssignment]) -> int:
    """Write a whole run's decisions in one transaction, returning how many.

    Args:
        conn: An open connection. This function manages its own transaction,
            which nests as a savepoint when the caller already has one open.
        decisions: Every selection and every skip from one scheduler run, in an
            order where each ``conflicts_with_assignment_id`` names a row
            already in the sequence or already stored.

    Returns:
        The number of rows written.

    Raises:
        psycopg.errors.ForeignKeyViolation: A ``conflicts_with_assignment_id``
            names no assignment, or a ``pass_id`` names no pass.
        psycopg.errors.UniqueViolation: An ``assignment_id`` already exists.

    Note:
        **One transaction for the whole run, unlike ``insert_pass``.** A pass is
        a standalone prediction and a half-finished generation run leaves
        correct rows; a schedule is not. Its rows reference each other — a skip
        names the selection that displaced it — so a run that stopped halfway
        could leave a skip whose winner was never written, or a set of
        selections that silently under-uses a station. Either is a schedule that
        reads as complete and is not.
    """
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            """
            insert into assignments
                (assignment_id, pass_id, station_id, start_at, end_at,
                 centre_freq_hz, mode, timing_uncertainty_s, decision, reason,
                 model_config, score, conflicts_with_assignment_id, priority,
                 simulated)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict on constraint assignment_decision_unique do nothing
            """,
            [
                (
                    one.assignment_id,
                    one.pass_id,
                    one.station_id,
                    one.start_at,
                    one.end_at,
                    one.centre_freq_hz,
                    one.mode,
                    one.timing_uncertainty_s,
                    one.decision,
                    one.reason,
                    one.model_config,
                    one.score,
                    one.conflicts_with_assignment_id,
                    one.priority,
                    one.simulated,
                )
                for one in decisions
            ],
        )
        return cur.rowcount
