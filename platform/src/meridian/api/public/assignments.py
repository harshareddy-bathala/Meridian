"""``/api/v1/assignments`` — what the scheduler decided, and why.

Two endpoints: the upcoming decisions, scheduled and skipped, filterable by
station and by decision; and one decision by id, past or upcoming. Windows are
widened to the minute (D-093) and the cursor names only an assignment id.

Reference: docs/DECISIONS.md D-008, D-083, D-085, D-093.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from meridian.api import platform_clock
from meridian.api.dependencies import get_connection
from meridian.api.public.envelope import NOT_FOUND, PublicError
from meridian.api.public.models import Page
from meridian.api.public.models.assignments import (
    AssignmentDecision,
    PublicAssignment,
)
from meridian.api.public.pagination import (
    PageRequest,
    encode_cursor,
    page_request,
    single_cursor_value,
    trim_overfetch,
)
from meridian.store.assignment_log import find_assignment, find_assignments
from meridian.store.stations import Connection

__all__ = ["router"]

router = APIRouter()


@router.get("/assignments")
def list_assignments(
    conn: Connection = Depends(get_connection, scope="function"),
    page: PageRequest = Depends(page_request),
    station_id: Annotated[str | None, Query()] = None,
    decision: Annotated[AssignmentDecision | None, Query()] = None,
) -> Page[PublicAssignment]:
    """Decisions whose window has not closed, soonest first.

    Args:
        conn: A pooled connection, injected.
        page: ``limit`` and ``cursor``, already validated and decoded.
        station_id: Only this station's decisions.
        decision: ``scheduled`` or ``skipped`` only. Any other value is refused
            as ``invalid_query`` by validation.

    Returns:
        One page of decisions, each with its reason, and the next cursor or null.
    """
    fetched = find_assignments(
        conn,
        not_before=platform_clock.utc_now(),
        station_id=station_id,
        decision=decision,
        after_assignment_id=single_cursor_value(page.cursor_key),
        limit=page.limit + 1,
    )
    trimmed = trim_overfetch(fetched, page.limit)

    next_cursor = None
    if trimmed.has_more_rows and trimmed.rows:
        next_cursor = encode_cursor((trimmed.rows[-1].assignment_id,))

    return Page(
        items=[PublicAssignment.from_row(row) for row in trimmed.rows],
        next_cursor=next_cursor,
    )


@router.get("/assignments/{assignment_id}")
def get_assignment(
    assignment_id: str, conn: Connection = Depends(get_connection, scope="function")
) -> PublicAssignment:
    """One decision, including one whose window is long past.

    Raises:
        PublicError: ``not_found`` when there is no such assignment.
    """
    row = find_assignment(conn, assignment_id)
    if row is None:
        raise PublicError(NOT_FOUND, "No assignment with that id.")
    return PublicAssignment.from_row(row)
