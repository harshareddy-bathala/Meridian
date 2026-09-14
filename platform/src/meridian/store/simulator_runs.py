"""Simulator runs, as the public API lists them.

A run is not a table. Every virtual station records the ``simulator_run_id`` it
was brought up under (D-075), so a run is those stations grouped: how many, when
they registered, and when the latest of them last reported.

The seed is never selected. A station's seed regenerates its whole profile
(D-077) — including its exact position, which D-082 publishes only coarsened — so
a published seed would undo the precision the station declared.

Paged by run id, which is text and unique per group.

Reference: docs/DECISIONS.md D-075, D-077, D-085.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = ["SimulatorRun", "find_simulator_runs"]


@dataclass(frozen=True, slots=True)
class SimulatorRun:
    """The virtual stations one simulator run registered."""

    run_id: str
    station_count: int
    first_registered_at: datetime
    last_registered_at: datetime
    last_heartbeat_at: datetime | None


def find_simulator_runs(
    conn: Connection, *, after_run_id: str | None, limit: int
) -> list[SimulatorRun]:
    """One page of runs, ordered by run id.

    Args:
        conn: An open connection. Read-only; opens no transaction of its own.
        after_run_id: The last run on the previous page, or ``None``.
        limit: The most rows to return; callers ask for one more than they need.

    Returns:
        Runs with at least one live simulated station. A run whose stations were
        all soft-deleted is gone, as they are.
    """
    with conn.cursor(row_factory=class_row(SimulatorRun)) as cur:
        cur.execute(
            """
            select simulator_run_id as run_id,
                   count(*)::int as station_count,
                   min(registered_at) as first_registered_at,
                   max(registered_at) as last_registered_at,
                   max(last_heartbeat_at) as last_heartbeat_at
            from stations
            where simulated and deleted_at is null
              and (%(after)s::text is null or simulator_run_id > %(after)s)
            group by simulator_run_id
            order by simulator_run_id asc
            limit %(limit)s
            """,
            {"after": after_run_id, "limit": limit},
        )
        return cur.fetchall()
