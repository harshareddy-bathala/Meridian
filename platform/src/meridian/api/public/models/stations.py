"""What a station looks like once it is safe to publish.

Turns a ``store.station_directory.DirectoryStation`` — a raw row, carrying full
coordinates and a bare heartbeat timestamp — into the body a reader receives. Two
things happen on the way, and both happen here so that no route can forget one:

- the coordinates are coarsened to the precision the operator declared (D-082)
- liveness is derived from the heartbeat timestamp against a clock reading the
  caller supplies (D-054)

The clock is a parameter rather than read here, so a page of stations is
classified against one instant. Reading ``now()`` per row would measure the first
station and the last against slightly different moments, which is how a station
sitting exactly on a threshold flickers between two answers within one response.

This module builds no SQL and opens no connection.
"""

from __future__ import annotations

from datetime import datetime
from typing import Generic, Self, TypeVar

from pydantic import BaseModel

from meridian.api.public.coordinate_privacy import publish_location
from meridian.registry.liveness import Liveness, derive_liveness
from meridian.store.station_directory import DirectoryStation

__all__ = ["Page", "PublicStation", "PublishedLocation", "StationLiveness"]

ItemT = TypeVar("ItemT")


class PublishedLocation(BaseModel):
    """A station's position, at the precision its operator permitted.

    Never the stored position. The values here have been through
    :func:`meridian.api.public.coordinate_privacy.publish_location`, and how far
    they were coarsened is stated beside them on the station so a map does not
    imply precision the reader was never given.
    """

    lat_deg: float
    lon_deg: float
    alt_m: int


class PublicStation(BaseModel):
    """One station, as the list and the detail endpoint both serve it.

    One model for both on purpose: a reader who clicks a station should see the
    same fields they were just looking at, and two models drift into a detail
    page that is missing something the list had.
    """

    station_id: str
    name: str
    operator: str

    location: PublishedLocation
    location_precision_decimals: int
    """How coarse ``location`` is, in decimal places — 1 is roughly 11 km at the
    equator and 6 roughly 11 cm. Published so a reader can tell a station pinned
    to its rooftop from one pinned to its city."""

    simulated: bool
    """Whether this station is virtual. Present on every station, never optional:
    a reader must never have to infer that a result is not measured
    (CLAUDE.md rule 5)."""

    liveness: Liveness
    last_heartbeat_at: datetime | None
    registered_at: datetime

    @classmethod
    def from_row(cls, row: DirectoryStation, *, now: datetime) -> Self:
        """Build the response body for one stored station.

        Args:
            row: The station as the store returned it, coordinates unrounded.
            now: The instant liveness is measured against, timezone-aware UTC.
                Supplied by the route so every station in one response is
                classified against the same reading.

        Returns:
            The station, coarsened and classified.
        """
        published = publish_location(
            row.lat_deg, row.lon_deg, row.alt_m, row.location_precision_decimals
        )
        return cls(
            station_id=row.station_id,
            name=row.name,
            operator=row.operator,
            location=PublishedLocation(
                lat_deg=published.lat_deg,
                lon_deg=published.lon_deg,
                alt_m=published.alt_m,
            ),
            location_precision_decimals=row.location_precision_decimals,
            simulated=row.simulated,
            liveness=derive_liveness(row.last_heartbeat_at, now=now),
            last_heartbeat_at=row.last_heartbeat_at,
            registered_at=row.registered_at,
        )


class StationLiveness(BaseModel):
    """Just the liveness of one station, for a caller polling it.

    A separate, much smaller body than :class:`PublicStation` because a dashboard
    refreshing a status light every few seconds should not re-fetch a location
    that changes once in a station's lifetime.
    """

    station_id: str
    liveness: Liveness
    last_heartbeat_at: datetime | None
    simulated: bool
    """Carried even here. A liveness figure for a virtual station is not evidence
    about the network, and this body is small enough to be quoted on its own."""

    @classmethod
    def from_row(cls, row: DirectoryStation, *, now: datetime) -> Self:
        """Classify one stored station against ``now``."""
        return cls(
            station_id=row.station_id,
            liveness=derive_liveness(row.last_heartbeat_at, now=now),
            last_heartbeat_at=row.last_heartbeat_at,
            simulated=row.simulated,
        )


class Page(BaseModel, Generic[ItemT]):
    """One page of a list endpoint, and how to ask for the next.

    Every list endpoint answers in this shape, so a client writes the paging loop
    once. ``next_cursor`` is the whole of the contract: present means there is
    more, absent means this was the last page. A client never constructs one — it
    passes back what it was given (D-085).
    """

    items: list[ItemT]
    next_cursor: str | None = None
