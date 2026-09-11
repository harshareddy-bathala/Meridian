"""``/api/v1/stations`` — the directory, and what is known about one station.

Three read endpoints: the paged list, one station, and its liveness on its own.
Every one of them is thin by rule — it reads through ``meridian.store``, hands
the rows to a model in ``meridian.api.public.models``, and returns. No decision
about what a station *is* is made here.

The clock is read once per request and passed down, so every station in a page is
classified against the same instant (D-054). Paging is keyset: the route asks for
one row more than the page needs and trims, which is how "is there another page"
is answered by evidence rather than by guessing from a full page (D-085).

Reference: docs/DECISIONS.md D-082, D-083, D-084, D-085; docs/PROJECT.md §13.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from meridian.api import platform_clock
from meridian.api.dependencies import get_connection
from meridian.api.public.envelope import NOT_FOUND, PublicError
from meridian.api.public.models import (
    Page,
    PublicStation,
)
from meridian.api.public.models.stations import StationLiveness
from meridian.api.public.pagination import (
    PageRequest,
    encode_cursor,
    page_request,
    trim_overfetch,
)
from meridian.store.station_directory import (
    DirectoryStation,
    find_station,
    find_stations_after,
)
from meridian.store.stations import Connection

__all__ = ["router"]

router = APIRouter()


def _station_or_404(conn: Connection, station_id: str) -> DirectoryStation:
    """Load one station, or raise the error the detail routes share."""
    station = find_station(conn, station_id)
    if station is None:
        raise PublicError(NOT_FOUND, "No station with that id.")
    return station


@router.get("/stations")
def list_stations(
    conn: Connection = Depends(get_connection),
    page: PageRequest = Depends(page_request),
) -> Page[PublicStation]:
    """Every registered station, oldest id first.

    Args:
        conn: A pooled connection, injected.
        page: ``limit`` and ``cursor``, already validated and decoded.

    Returns:
        One page of stations, each carrying its coarsened location, its derived
        liveness and its ``simulated`` flag, plus the cursor for the next page or
        ``null`` when this was the last.

    Note:
        A station whose token was revoked is still listed. It existed, and every
        observation already attributed to it still names it — a directory that
        hid it would disagree with the rest of the API.
    """
    after = page.cursor_key[0] if page.cursor_key else None
    fetched = find_stations_after(conn, after, page.limit + 1)
    trimmed = trim_overfetch(fetched, page.limit)

    now = platform_clock.utc_now()
    items = [PublicStation.from_row(row, now=now) for row in trimmed.rows]

    next_cursor = None
    if trimmed.has_more_rows and trimmed.rows:
        next_cursor = encode_cursor((trimmed.rows[-1].station_id,))

    return Page(items=items, next_cursor=next_cursor)


@router.get("/stations/{station_id}")
def get_station(
    station_id: str, conn: Connection = Depends(get_connection)
) -> PublicStation:
    """One station, in the same shape the list serves.

    Args:
        station_id: The station to look up.
        conn: A pooled connection, injected.

    Returns:
        The station.

    Raises:
        PublicError: ``not_found`` when there is no such station, or when it was
            soft-deleted — the two are one answer, so an id that was once in use
            is not confirmed to have been.
    """
    row = _station_or_404(conn, station_id)
    return PublicStation.from_row(row, now=platform_clock.utc_now())


@router.get("/stations/{station_id}/liveness")
def get_station_liveness(
    station_id: str, conn: Connection = Depends(get_connection)
) -> StationLiveness:
    """Whether one station is reporting, without the rest of its record.

    Args:
        station_id: The station to look up.
        conn: A pooled connection, injected.

    Returns:
        Its liveness, the heartbeat it was derived from, and its provenance.

    Raises:
        PublicError: ``not_found`` when there is no such station.

    Note:
        Its own endpoint because a dashboard refreshing a status light every few
        seconds should not re-fetch a location that changes once in a station's
        lifetime.
    """
    row = _station_or_404(conn, station_id)
    return StationLiveness.from_row(row, now=platform_clock.utc_now())
