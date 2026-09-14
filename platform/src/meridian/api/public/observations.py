"""``/api/v1/observations`` and ``/api/v1/simulator-runs``.

Recent observations, newest first and filterable by station; and the simulator
runs whose virtual stations are registered. Both are keyset-paged with a
one-part cursor.

Reference: docs/DECISIONS.md D-083, D-085, D-093.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from meridian.api.dependencies import get_connection
from meridian.api.public.models import Page
from meridian.api.public.models.observations import (
    PublicObservation,
    PublicSimulatorRun,
)
from meridian.api.public.pagination import (
    PageRequest,
    encode_cursor,
    page_request,
    single_cursor_value,
    trim_overfetch,
)
from meridian.store.observation_history import find_recent_observations
from meridian.store.simulator_runs import find_simulator_runs
from meridian.store.stations import Connection

__all__ = ["router"]

router = APIRouter()


@router.get("/observations")
def list_observations(
    conn: Connection = Depends(get_connection),
    page: PageRequest = Depends(page_request),
    station_id: Annotated[str | None, Query()] = None,
) -> Page[PublicObservation]:
    """Observations, most recently started first, each at its latest revision.

    Args:
        conn: A pooled connection, injected.
        page: ``limit`` and ``cursor``, already validated and decoded.
        station_id: Only this station's observations.

    Returns:
        One page of observations, and the next cursor or null.
    """
    fetched = find_recent_observations(
        conn,
        station_id=station_id,
        before_assignment_id=single_cursor_value(page.cursor_key),
        limit=page.limit + 1,
    )
    trimmed = trim_overfetch(fetched, page.limit)

    next_cursor = None
    if trimmed.has_more_rows and trimmed.rows:
        next_cursor = encode_cursor((trimmed.rows[-1].assignment_id,))

    return Page(
        items=[PublicObservation.from_row(row) for row in trimmed.rows],
        next_cursor=next_cursor,
    )


@router.get("/simulator-runs")
def list_simulator_runs(
    conn: Connection = Depends(get_connection),
    page: PageRequest = Depends(page_request),
) -> Page[PublicSimulatorRun]:
    """Simulator runs with registered virtual stations, by run id.

    Args:
        conn: A pooled connection, injected.
        page: ``limit`` and ``cursor``, already validated and decoded.

    Returns:
        One page of runs, and the next cursor or null.
    """
    fetched = find_simulator_runs(
        conn, after_run_id=single_cursor_value(page.cursor_key), limit=page.limit + 1
    )
    trimmed = trim_overfetch(fetched, page.limit)

    next_cursor = None
    if trimmed.has_more_rows and trimmed.rows:
        next_cursor = encode_cursor((trimmed.rows[-1].run_id,))

    return Page(
        items=[PublicSimulatorRun.from_row(row) for row in trimmed.rows],
        next_cursor=next_cursor,
    )
