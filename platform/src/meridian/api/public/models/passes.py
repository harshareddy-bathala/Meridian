"""An upcoming pass, as the public API states it.

Built from ``store.pass_queue.QueuedPass``. The window is widened to whole
minutes and every angle rounded to a whole degree on the way here (D-093), so
this model has no path from a stored pass to a body that skips the rule.

Reference: docs/DECISIONS.md D-063, D-093.
"""

from __future__ import annotations

from datetime import datetime
from typing import Self

from pydantic import BaseModel

from meridian.api.public.window_privacy import (
    floor_minute,
    publish_angle,
    publish_window,
)
from meridian.store.pass_queue import QueuedPass

__all__ = ["PublicPass"]


class PublicPass(BaseModel):
    """One predicted pass of one satellite over one station."""

    pass_id: int
    satellite_id: str
    station_id: str

    aos: datetime
    """Acquisition, floored to the minute: the real one is at or after this."""
    los: datetime
    """Loss of signal, ceiled to the minute: the real one is at or before this."""

    max_elevation_deg: int
    max_elevation_at: datetime
    aos_azimuth_deg: int
    los_azimuth_deg: int

    min_elevation_deg: float
    """The station's own horizon for this pass — configuration, not geometry, so
    published as the station declared it."""

    element_set_epoch: datetime
    simulated: bool
    """True when the pass was computed for a virtual station (CLAUDE.md rule 5)."""

    @classmethod
    def from_row(cls, row: QueuedPass) -> Self:
        """Coarsen one stored pass for publication."""
        window = publish_window(row.aos, row.los)
        return cls(
            pass_id=row.id,
            satellite_id=row.satellite_id,
            station_id=row.station_id,
            aos=window.start,
            los=window.end,
            max_elevation_deg=publish_angle(row.max_elevation_deg),
            max_elevation_at=floor_minute(row.max_elevation_at),
            aos_azimuth_deg=publish_angle(row.aos_azimuth_deg),
            los_azimuth_deg=publish_angle(row.los_azimuth_deg),
            min_elevation_deg=row.min_elevation_deg,
            element_set_epoch=row.element_set_epoch,
            simulated=row.simulated,
        )
