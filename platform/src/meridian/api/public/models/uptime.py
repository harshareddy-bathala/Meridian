"""How much of each recent hour a station was heard from, as the public API says it.

Built from ``store.heartbeat_coverage`` rows, which come from the
``heartbeats_hourly`` continuous aggregate (D-178). Coverage is heartbeats
received over heartbeats expected at the platform's configured interval, capped
at 1: a station that sends faster is not more than fully covered.

This is coverage, not evidence. Whether a station was listening for one pass is
decided from raw heartbeats by ``Registry.was_listening`` (rule 7), and nothing
here stands in for it.

This module builds no SQL and opens no connection.
"""

from __future__ import annotations

from datetime import datetime
from typing import Self

from pydantic import BaseModel

from meridian.store.heartbeat_coverage import HourlyCoverage

__all__ = ["PublicHourlyUptime", "PublicStationUptime"]


class PublicHourlyUptime(BaseModel):
    """One hour of one station's heartbeats."""

    hour: datetime
    heartbeats: int
    listening: int
    """Heartbeats that reported listening to an assignment."""
    coverage: float
    """Heartbeats received over those expected, from 0 to 1."""

    @classmethod
    def from_row(cls, row: HourlyCoverage, *, expected: int) -> Self:
        """Publish one hour against ``expected`` heartbeats an hour."""
        return cls(
            hour=row.hour,
            heartbeats=row.heartbeats,
            listening=row.listening,
            coverage=round(min(1.0, row.heartbeats / expected), 3),
        )


class PublicStationUptime(BaseModel):
    """A station's recent hours, oldest first, the current hour left out."""

    station_id: str
    simulated: bool
    heartbeat_interval_s: int
    hours: list[PublicHourlyUptime]
