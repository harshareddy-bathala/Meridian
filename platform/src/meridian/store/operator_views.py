"""The two operators' views: how schedule runs fared, and pass timing error.

Reads ``scheduler_performance`` and ``timing_error``
(``deploy/migrations/sql/0022_views_and_heartbeat_aggregate.sql``). They answer
an operator at a prompt. They are not reported numbers: a view over live tables
answers differently each time, and every published figure comes from a snapshot
(rule 8, D-177).

Reference: docs/DECISIONS.md D-025, D-170, D-177.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = ["RunPerformance", "TimingError", "find_recent_runs", "find_timing_errors"]


@dataclass(frozen=True, slots=True)
class RunPerformance:
    """One schedule run and population, and what became of its assignments.

    The run's own counts are the whole run's; the rest are this population's.
    """

    run_id: str
    decided_at: datetime
    model_config: str
    yield_source: str
    solver_status: str
    fell_back: bool
    runtime_s: float
    candidates: int
    scheduled: int
    skipped: int
    revoked: int
    expired: int
    decoded: int
    signal_no_decode: int
    no_signal: int
    aborted: int
    not_attempted: int
    outstanding: int
    frames_decoded: int
    assignments: int
    """This population's decisions in the run."""
    simulated: bool | None
    """Null for a run that decided nothing."""


@dataclass(frozen=True, slots=True)
class TimingError:
    """One detected pass's timing error, and whether §6.1 keeps it."""

    assignment_id: str
    station_id: str
    satellite_id: str
    aos: datetime
    uncorrected_error_s: float
    timing_error_s: float | None
    clock_uncertainty_s: float | None
    element_set_age_days: float
    excluded: str | None
    simulated: bool


def find_recent_runs(conn: Connection, *, limit: int) -> list[RunPerformance]:
    """The most recent schedule runs, newest first."""
    with conn.cursor(row_factory=class_row(RunPerformance)) as cur:
        cur.execute(
            "select run_id, decided_at, model_config, yield_source, solver_status,"
            " fell_back, runtime_s, candidates, scheduled, skipped, revoked,"
            " expired, decoded, signal_no_decode, no_signal, aborted,"
            " not_attempted, outstanding, frames_decoded, assignments, simulated"
            " from scheduler_performance"
            " order by decided_at desc, run_id desc, simulated nulls first"
            " limit %s",
            (limit,),
        )
        return cur.fetchall()


def find_timing_errors(
    conn: Connection, *, station_id: str | None, limit: int
) -> list[TimingError]:
    """The most recent timing errors, newest pass first."""
    with conn.cursor(row_factory=class_row(TimingError)) as cur:
        cur.execute(
            "select assignment_id, station_id, satellite_id, aos,"
            " uncorrected_error_s::float8 as uncorrected_error_s,"
            " timing_error_s::float8 as timing_error_s, clock_uncertainty_s,"
            " element_set_age_days::float8 as element_set_age_days, excluded,"
            " simulated from timing_error"
            " where %(station)s::text is null or station_id = %(station)s"
            " order by aos desc, assignment_id limit %(limit)s",
            {"station": station_id, "limit": limit},
        )
        return cur.fetchall()
