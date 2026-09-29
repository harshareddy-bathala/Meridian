"""The ground beneath a satellite, sampled over an interval.

Skyfield's ``wgs84.subpoint_of`` does the geodesy: the geocentric position from
SGP4 is rotated into the Earth-fixed frame at each instant and dropped along the
ellipsoid normal. Nothing here is our own geodesy, for rule 1's reason. Kept
apart from :mod:`meridian.orbit.skyfield_service`, which calls it, so that
module stays one reviewable file.

Reference: docs/DECISIONS.md D-230.
"""

from __future__ import annotations

from datetime import datetime

from skyfield.api import EarthSatellite, wgs84
from skyfield.timelib import Timescale

from meridian.orbit.time_sampling import half_open_sample_times
from meridian.orbit.types import ElementSet, SubPoint, require_utc

__all__ = ["sub_satellite_track"]


def sub_satellite_track(
    timescale: Timescale,
    element_set: ElementSet,
    start: datetime,
    end: datetime,
    step_s: float,
) -> list[SubPoint]:
    """The sub-satellite point every ``step_s`` seconds over ``[start, end)``.

    Raises:
        ValueError: ``step_s`` is not positive, or a bound is not UTC.
    """
    if step_s <= 0:
        raise ValueError(f"step_s must be positive, got {step_s}")
    sample_times = half_open_sample_times(
        require_utc(start, "start"), require_utc(end, "end"), step_s
    )
    satellite = EarthSatellite(
        element_set.line1, element_set.line2, element_set.satellite_id, timescale
    )
    track = []
    for t in sample_times:
        below = wgs84.subpoint_of(satellite.at(timescale.from_datetime(t)))
        track.append(
            SubPoint(
                t=t,
                lat_deg=float(below.latitude.degrees),
                lon_deg=float(below.longitude.degrees),
            )
        )
    return track
