"""The rows the platform's scrape-time metrics are computed from.

Prometheus asks the API for its metrics every fifteen seconds, and the numbers
that describe the network — stations by liveness, assignments by state,
assignments whose report is overdue — are read here when it asks, rather than kept
as gauges that go stale the moment nobody updates them (D-109).

**Read-only, and nothing here classifies anything.** Liveness is derived from the
heartbeat instants this module returns by ``meridian.registry.liveness``, the one
place that rule lives (D-054). An overdue assignment is a report that has not
arrived; it is not a miss, which only ``meridian.reliability`` may decide (D-111).

Every count is split by ``simulated``, because a series that mixed simulated and
measured stations would be the failure CLAUDE.md's fifth rule exists to prevent.

Reference: docs/DECISIONS.md D-054, D-109, D-111.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = [
    "ASSIGNMENT_STATES",
    "MonitoringSnapshot",
    "StationHeartbeat",
    "read_monitoring_snapshot",
]

ASSIGNMENT_STATES = ("issued", "held", "in_progress", "reported", "expired")
"""The five values ``assignments.state`` may hold, from migration 0004's check.

Listed so a state with no rows is reported as zero rather than left out: an alert
on ``held`` must be able to tell "none held" from "not measured".
"""


@dataclass(frozen=True, slots=True)
class StationHeartbeat:
    """One registered station's last heartbeat instant and its provenance."""

    last_heartbeat_at: datetime | None
    simulated: bool


@dataclass(frozen=True, slots=True)
class MonitoringSnapshot:
    """Everything one scrape reads, taken in one transaction.

    Attributes:
        stations: Every station not deleted.
        assignments: Scheduled assignments counted by ``(state, simulated)``;
            every state in :data:`ASSIGNMENT_STATES` is present for both values
            of ``simulated``, zero where no row has it.
        overdue: ``held`` or ``in_progress`` assignments whose window ended before
            the cutoff and that have no observation, by ``simulated``; both keys
            are present.
    """

    stations: tuple[StationHeartbeat, ...]
    assignments: dict[tuple[str, bool], int]
    overdue: dict[bool, int]


@dataclass(frozen=True, slots=True)
class _StateCount:
    """One ``group by`` row of assignments."""

    state: str
    simulated: bool
    count: int


@dataclass(frozen=True, slots=True)
class _ProvenanceCount:
    """One ``group by simulated`` row."""

    simulated: bool
    count: int


def _read_stations(conn: Connection) -> tuple[StationHeartbeat, ...]:
    """Every station that has not been deleted."""
    with conn.cursor(row_factory=class_row(StationHeartbeat)) as cur:
        cur.execute(
            """
            select last_heartbeat_at, simulated
            from stations
            where deleted_at is null
            """
        )
        return tuple(cur.fetchall())


def _count_assignments(conn: Connection) -> dict[tuple[str, bool], int]:
    """Scheduled assignments by state and provenance, zero-filled.

    Skipped decisions are rows too, and carry the default state ``issued``
    although nothing was ever issued; counting them would report work nobody
    was given, so only ``scheduled`` rows are read.
    """
    counts = {
        (state, simulated): 0
        for state in ASSIGNMENT_STATES
        for simulated in (False, True)
    }
    with conn.cursor(row_factory=class_row(_StateCount)) as cur:
        cur.execute(
            """
            select state, simulated, count(*) as count
            from assignments
            where decision = 'scheduled'
            group by state, simulated
            """
        )
        for row in cur.fetchall():
            counts[(row.state, row.simulated)] = row.count
    return counts


def _count_overdue(conn: Connection, *, ended_before: datetime) -> dict[bool, int]:
    """Taken or started work whose window closed with no report, by provenance.

    A station that is executing an assignment or holds it has said it will
    report; MSP §6 lets it queue that report through an outage. So this counts
    reports that have not arrived — it says nothing about whether the pass was
    received, and nothing about whether the station was listening.
    """
    counts = {False: 0, True: 0}
    with conn.cursor(row_factory=class_row(_ProvenanceCount)) as cur:
        cur.execute(
            """
            select a.simulated, count(*) as count
            from assignments a
            where a.decision = 'scheduled'
              and a.state in ('held', 'in_progress')
              and a.end_at < %s
              and not exists (
                  select 1 from observations o
                  where o.assignment_id = a.assignment_id
              )
            group by a.simulated
            """,
            (ended_before,),
        )
        for row in cur.fetchall():
            counts[row.simulated] = row.count
    return counts


def read_monitoring_snapshot(
    conn: Connection, *, overdue_ended_before: datetime
) -> MonitoringSnapshot:
    """Read what the scrape-time metrics report.

    Args:
        conn: An open connection. The three reads run in the connection's
            current transaction, so they describe one instant.
        overdue_ended_before: Assignments whose window ended before this
            timezone-aware instant, still unreported, count as overdue.

    Returns:
        The stations, assignment counts and overdue counts.
    """
    return MonitoringSnapshot(
        stations=_read_stations(conn),
        assignments=_count_assignments(conn),
        overdue=_count_overdue(conn, ended_before=overdue_ended_before),
    )
