"""What a scheduler run writes: the run itself, and every decision it made.

``meridian.store.assignments`` serves delivery and reconciliation; this module
is the scheduler's side. Both writes happen in one transaction, the run first,
because every decision names its run (D-170) and a skip names the selection that
displaced it (D-065): a schedule half-written is not a partial result but a
wrong one.

Reference: docs/DECISIONS.md D-065, D-066, D-165, D-170.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from psycopg.types.json import Jsonb

from meridian.store.stations import Connection

__all__ = [
    "NewAssignment",
    "NewScheduleRun",
    "insert_assignments",
    "insert_schedule",
]


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
    """The value the scheduler weighed. Read with ``model_config`` or not at
    all: the unit differs by configuration (D-065, D-168)."""

    conflicts_with_assignment_id: str | None
    """For a skip, the assignment that took the slot. ``None`` otherwise."""

    priority: float
    simulated: bool
    """Copied from the pass, which copied it from the station (D-013)."""

    predicted_yield: float | None = None
    """A model's probability of a decode, or ``None`` where no model gave one:
    the elevation proxy is not a prediction, and is kept in ``explanation``."""

    schedule_run_id: str | None = None
    model_sha256: bytes | None = None
    explanation: Mapping[str, object] | None = None
    """Why this decision (D-170). ``None`` only for decisions made by no
    recorded run."""

    revision: int = 0
    """Which decision about the pass under this configuration (D-171)."""


@dataclass(frozen=True, slots=True)
class NewScheduleRun:
    """One scheduler run, as ``schedule_runs`` records it (D-170)."""

    run_id: str
    decided_at: datetime
    horizon_start: datetime
    horizon_end: datetime
    model_config: str
    config_sha256: bytes
    parameters: Mapping[str, object]
    yield_source: str
    model_sha256: bytes | None
    history_sha256: bytes | None
    history_as_of: datetime | None
    solver: str
    solver_version: str
    status: str
    objective: float
    bound: float | None
    time_limit_s: float
    runtime_s: float
    detail: str | None
    stations: int
    candidates: int
    scheduled: int
    skipped: int


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
            names no assignment, a ``pass_id`` no pass, or a
            ``schedule_run_id`` no run.

    Note:
        **A decision already made is left as it was**, under
        ``assignment_decision_unique`` (D-066). Runs do not race: the scheduler
        run holds an advisory lock for its whole transaction.
    """
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            """
            insert into assignments
                (assignment_id, pass_id, station_id, start_at, end_at,
                 centre_freq_hz, mode, timing_uncertainty_s, decision, reason,
                 model_config, score, conflicts_with_assignment_id, priority,
                 simulated, predicted_yield, schedule_run_id, model_sha256,
                 explanation, revision)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s)
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
                    one.predicted_yield,
                    one.schedule_run_id,
                    one.model_sha256,
                    None if one.explanation is None else Jsonb(one.explanation),
                    one.revision,
                )
                for one in decisions
            ],
        )
        return cur.rowcount


def insert_schedule(
    conn: Connection, run: NewScheduleRun, decisions: Sequence[NewAssignment]
) -> int:
    """Write a run and its decisions together, returning how many decisions landed.

    Raises:
        psycopg.errors.UniqueViolation: The run id is already recorded.
    """
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            insert into schedule_runs
                (run_id, decided_at, horizon_start, horizon_end, model_config,
                 config_sha256, parameters, yield_source, model_sha256,
                 history_sha256, history_as_of, solver, solver_version, status,
                 objective, bound, time_limit_s, runtime_s, detail, stations,
                 candidates, scheduled, skipped)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                run.run_id,
                run.decided_at,
                run.horizon_start,
                run.horizon_end,
                run.model_config,
                run.config_sha256,
                Jsonb(run.parameters),
                run.yield_source,
                run.model_sha256,
                run.history_sha256,
                run.history_as_of,
                run.solver,
                run.solver_version,
                run.status,
                run.objective,
                run.bound,
                run.time_limit_s,
                run.runtime_s,
                run.detail,
                run.stations,
                run.candidates,
                run.scheduled,
                run.skipped,
            ),
        )
        return insert_assignments(conn, decisions)
