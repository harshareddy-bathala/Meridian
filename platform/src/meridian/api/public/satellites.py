"""``/api/v1/satellites`` — the catalogue, and the downlinks of one object.

Three read endpoints: the paged list, one satellite, and its transmitters. Each
is thin by rule — it reads through ``meridian.store``, hands the rows to a model
in ``meridian.api.public.models``, and returns.

The clock is read once per request and passed down, so every satellite in a page
has its element-set age measured against the same instant. Paging is keyset: the
route asks for one row more than the page needs and trims (D-085).

A satellite id looks like ``norad:25544``, so these paths carry a colon in a path
segment. That is a legal path character (RFC 3986 §3.3), needs no escaping, and
``curl https://dash.meridian.org.in/api/v1/satellites/norad:25544`` works as
typed — which is the property D-083 wanted when it kept the version in the path
and the surface header-free.

Reference: docs/DECISIONS.md D-021, D-066, D-083, D-084, D-085.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from meridian.api import platform_clock
from meridian.api.dependencies import get_connection
from meridian.api.public.envelope import NOT_FOUND, PublicError
from meridian.api.public.models import Page, PublicSatellite, PublicTransmitter
from meridian.api.public.pagination import (
    PageRequest,
    encode_cursor,
    page_request,
    trim_overfetch,
)
from meridian.store.satellite_catalogue import (
    CataloguedSatellite,
    find_satellite,
    find_satellites_after,
    find_transmitters_for_satellite,
)
from meridian.store.stations import Connection

__all__ = ["router"]

router = APIRouter()


def _satellite_or_404(conn: Connection, satellite_id: str) -> CataloguedSatellite:
    """Load one satellite, or raise the error the two detail routes share."""
    satellite = find_satellite(conn, satellite_id)
    if satellite is None:
        raise PublicError(NOT_FOUND, "No satellite with that id.")
    return satellite


@router.get("/satellites")
def list_satellites(
    conn: Connection = Depends(get_connection, scope="function"),
    page: PageRequest = Depends(page_request),
) -> Page[PublicSatellite]:
    """Every tracked object, in catalogue order.

    Args:
        conn: A pooled connection, injected.
        page: ``limit`` and ``cursor``, already validated and decoded.

    Returns:
        One page of satellites, each carrying its operator weighting, whether it
        is believed still transmitting, and how stale the newest element set we
        hold for it is.

    Note:
        A satellite believed silent is still listed, and says so. Excluding it
        would make a reader read its empty week as a prediction failure rather
        than as a spacecraft that was switched off — which is the confound
        EVALUATION.md §5 exists to keep visible.
    """
    after = page.cursor_key[0] if page.cursor_key else None
    fetched = find_satellites_after(conn, after, page.limit + 1)
    trimmed = trim_overfetch(fetched, page.limit)

    now = platform_clock.utc_now()
    items = [PublicSatellite.from_row(row, now=now) for row in trimmed.rows]

    next_cursor = None
    if trimmed.has_more_rows and trimmed.rows:
        next_cursor = encode_cursor((trimmed.rows[-1].satellite_id,))

    return Page(items=items, next_cursor=next_cursor)


@router.get("/satellites/{satellite_id}")
def get_satellite(
    satellite_id: str, conn: Connection = Depends(get_connection, scope="function")
) -> PublicSatellite:
    """One satellite, in the same shape the list serves.

    Args:
        satellite_id: The object to look up, ``norad:NNNNN``.
        conn: A pooled connection, injected.

    Returns:
        The satellite.

    Raises:
        PublicError: ``not_found`` when there is no such satellite, or when it
            was withdrawn from the catalogue — the two are one answer.
    """
    row = _satellite_or_404(conn, satellite_id)
    return PublicSatellite.from_row(row, now=platform_clock.utc_now())


@router.get("/satellites/{satellite_id}/transmitters")
def get_satellite_transmitters(
    satellite_id: str, conn: Connection = Depends(get_connection, scope="function")
) -> list[PublicTransmitter]:
    """The downlinks recorded for one satellite.

    Args:
        satellite_id: The object to look up, ``norad:NNNNN``.
        conn: A pooled connection, injected.

    Returns:
        Its transmitters in ascending frequency order, empty when the catalogue
        records none for it.

    Raises:
        PublicError: ``not_found`` when there is no such satellite. An empty list
            means a real satellite whose downlinks nobody has entered, which is a
            catalogue gap rather than a bad URL — and the two reach a reader as
            different answers.
    """
    _satellite_or_404(conn, satellite_id)
    return [
        PublicTransmitter.from_row(row)
        for row in find_transmitters_for_satellite(conn, satellite_id)
    ]
