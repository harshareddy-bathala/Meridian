"""The latest scheduling round, as the public API states it.

``schedule_runs`` holds one row per run that decided something (D-170). This
module reads the newest, and counts its decisions by population, so a round
that scheduled simulated and measured stations together says how many of each.

It reads the run's own record and nothing derived from what became of its
assignments. How a run's work fared is ``scheduler_performance``'s, an
operator's read over live tables, never a published figure (D-177).

Reference: docs/DECISIONS.md D-167, D-170, D-177, D-331.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = ["LatestRun", "find_latest_run"]


@dataclass(frozen=True, slots=True)
class LatestRun:
    """One schedule run's record, with its decisions counted by population."""

    run_id: str
    decided_at: datetime
    horizon_start: datetime
    horizon_end: datetime
    model_config: str
    yield_source: str
    model_sha256: bytes | None
    history_as_of: datetime | None
    solver: str
    solver_version: str
    status: str
    objective: float
    bound: float | None
    time_limit_s: float
    runtime_s: float
    stations: int
    candidates: int
    scheduled: int
    skipped: int
    simulated_decisions: int
    measured_decisions: int


def find_latest_run(conn: Connection) -> LatestRun | None:
    """The most recently decided run, or ``None`` before any round decided anything."""
    with conn.cursor(row_factory=class_row(LatestRun)) as cur:
        cur.execute(
            """
            with latest as (
                select * from schedule_runs
                order by decided_at desc, run_id desc
                limit 1
            )
            select r.run_id, r.decided_at, r.horizon_start, r.horizon_end,
                   r.model_config, r.yield_source, r.model_sha256, r.history_as_of,
                   r.solver, r.solver_version, r.status, r.objective, r.bound,
                   r.time_limit_s, r.runtime_s, r.stations, r.candidates,
                   r.scheduled, r.skipped,
                   count(a.assignment_id) filter (where a.simulated)::int
                       as simulated_decisions,
                   count(a.assignment_id) filter (where not a.simulated)::int
                       as measured_decisions
            from latest r
            left join assignments a on a.schedule_run_id = r.run_id
            group by r.run_id, r.decided_at, r.horizon_start, r.horizon_end,
                     r.model_config, r.yield_source, r.model_sha256,
                     r.history_as_of, r.solver, r.solver_version, r.status,
                     r.objective, r.bound, r.time_limit_s, r.runtime_s,
                     r.stations, r.candidates, r.scheduled, r.skipped
            """
        )
        return cur.fetchone()
