"""``/api/v1/passes`` — the predicted passes still ahead.

One list endpoint, network-wide or filtered to one station, soonest first. Every
pass carries ``simulated``, and its window and angles are coarsened before they
leave (D-093).

Paging is keyset on ``(aos, id)`` with a cursor that names only the pass id, so
the cursor cannot carry the exact time the body rounded off.

Reference: docs/DECISIONS.md D-083, D-085, D-093.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from meridian.api import platform_clock
from meridian.api.dependencies import get_connection
from meridian.api.public.envelope import INVALID_QUERY, PublicError
from meridian.api.public.models import Page, PublicPass
from meridian.api.public.pagination import (
    PageRequest,
    encode_cursor,
    page_request,
    single_cursor_value,
    trim_overfetch,
)
from meridian.store.pass_queue import find_upcoming_passes
from meridian.store.stations import Connection

__all__ = ["router"]

router = APIRouter()


def _after_pass_id(page: PageRequest) -> int | None:
    value = single_cursor_value(page.cursor_key)
    if value is None:
        return None
    if not value.isdigit():
        raise PublicError(INVALID_QUERY, "cursor is not a valid cursor.")
    return int(value)


@router.get("/passes")
def list_upcoming_passes(
    conn: Connection = Depends(get_connection),
    page: PageRequest = Depends(page_request),
    station_id: Annotated[str | None, Query()] = None,
) -> Page[PublicPass]:
    """Passes whose window has not closed, soonest acquisition first.

    Args:
        conn: A pooled connection, injected.
        page: ``limit`` and ``cursor``, already validated and decoded.
        station_id: Only this station's passes. An unknown id is an empty page,
            not an error: a filter that matches nothing has matched nothing.

    Returns:
        One page of passes, and the cursor for the next or ``null``.
    """
    fetched = find_upcoming_passes(
        conn,
        not_before=platform_clock.utc_now(),
        station_id=station_id,
        after_pass_id=_after_pass_id(page),
        limit=page.limit + 1,
    )
    trimmed = trim_overfetch(fetched, page.limit)

    next_cursor = None
    if trimmed.has_more_rows and trimmed.rows:
        next_cursor = encode_cursor((str(trimmed.rows[-1].id),))

    return Page(
        items=[PublicPass.from_row(row) for row in trimmed.rows],
        next_cursor=next_cursor,
    )
