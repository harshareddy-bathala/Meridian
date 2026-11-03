"""``/api/v1/schedule`` and ``/api/v1/schedule-runs/latest`` — what is planned, and how.

``/schedule?station_id=…&hours=6`` is one station's decisions over the next
hours, scheduled and skipped, each with its explanation (D-170), its predicted
yield, its pass's peak and the epoch of the element set behind it. It is the
list ``/api/v1/assignments`` gives, for one station over a stated span, with
the orbit's age beside each decision, so a timeline is one request.

``/schedule-runs/latest`` is the newest round's own record: its configuration,
the solver's status and objective, and its decisions by population. Its gain
over a baseline is stated as not measured live, with the reason (D-172). Before
any round has decided anything, it is ``not_yet_computed`` rather than a 404, so
the front keeps a copy of that answer like any other (D-086, D-282).

Reference: docs/DECISIONS.md D-086, D-093, D-165, D-170, D-172, D-177, D-331.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from meridian.api import platform_clock
from meridian.api.dependencies import get_connection
from meridian.api.public.envelope import NOT_FOUND, PublicError
from meridian.api.public.models.not_yet_computed import NotYetComputed
from meridian.api.public.models.schedule import (
    PublicScheduleRun,
    PublicStationSchedule,
)
from meridian.store.assignment_log import find_station_schedule
from meridian.store.schedule_run_reads import find_latest_run
from meridian.store.station_directory import find_station
from meridian.store.stations import Connection

__all__ = ["MAX_HOURS", "SCHEDULE_RUNS_STAGE", "router"]

MAX_HOURS = 24
"""The longest span one schedule read covers: four of the jobs service's horizons."""

SCHEDULE_RUNS_STAGE = 18
"""The stage whose rounds write ``schedule_runs`` (D-170)."""

router = APIRouter()


@router.get("/schedule")
def get_station_schedule(
    station_id: Annotated[str, Query()],
    hours: Annotated[int, Query(ge=1, le=MAX_HOURS)] = 6,
    conn: Connection = Depends(get_connection, scope="function"),
) -> PublicStationSchedule:
    """One station's decisions whose window meets the next ``hours``.

    Args:
        station_id: The station; required.
        hours: How far ahead, 1 to 24, from now.
        conn: A pooled connection, injected.

    Raises:
        PublicError: ``not_found`` when there is no such station.
    """
    station = find_station(conn, station_id)
    if station is None:
        raise PublicError(NOT_FOUND, "No station with that id.")
    start = platform_clock.utc_now()
    end = start + timedelta(hours=hours)
    return PublicStationSchedule.from_rows(
        station.station_id,
        station.simulated,
        start,
        end,
        find_station_schedule(conn, station_id, start=start, end=end),
    )


@router.get("/schedule-runs/latest")
def get_latest_schedule_run(
    conn: Connection = Depends(get_connection, scope="function"),
) -> PublicScheduleRun | NotYetComputed:
    """The newest scheduling round's record, or ``not_yet_computed`` before one.

    A round leaves a record only once it has decided something (D-170).
    """
    row = find_latest_run(conn)
    if row is None:
        return NotYetComputed(
            reason="No scheduling round has decided anything yet.",
            available_from_stage=SCHEDULE_RUNS_STAGE,
        )
    return PublicScheduleRun.from_row(row)
