"""Where in a virtual station's sky a pass is, at given instants.

Stage 25's obstruction and interference happen in a sector of the sky, so a
virtual station needs a pass's azimuth and elevation to know whether a fault
touched it. It computes them the way the platform does — Skyfield, from the
element set the assignment carries inline (MSP §4.3), at the station's own site
— so the two agree on where the satellite was without sharing code (D-138).

**The one module in the simulator allowed to import a propagation library**
(D-252, and the per-file exemption in ``pyproject.toml``). ARCHITECTURE.md rule 2
keeps propagation behind ``meridian.orbit`` inside the platform; the simulator is
a station, and a station that points an antenna has always had to know this.

Nothing here decides anything: instants and a site in, angles out. The timescale
comes from Skyfield's bundled leap-second data, so no file is fetched.

Reference: docs/MSP-SPEC.md §4.3; docs/DECISIONS.md D-138, D-252.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import cache

from skyfield.api import EarthSatellite, load, wgs84
from skyfield.timelib import Timescale

__all__ = ["Site", "SkyPoint", "track"]


@dataclass(frozen=True, slots=True)
class Site:
    """Where a virtual station stands, as it registered."""

    lat_deg: float
    lon_deg: float
    alt_m: float


@dataclass(frozen=True, slots=True)
class SkyPoint:
    """One direction in a station's sky: degrees from north, and above the horizon."""

    azimuth_deg: float
    elevation_deg: float


def track(
    line1: str, line2: str, site: Site, instants: Sequence[datetime]
) -> tuple[SkyPoint, ...]:
    """The satellite's direction from ``site`` at each instant.

    Args:
        line1: The element set's first line, as the assignment carried it.
        line2: Its second line.
        site: The station.
        instants: Timezone-aware instants, in any order.

    Returns:
        One point per instant, in the order given. Elevation is negative while
        the satellite is below the horizon, which the window's edges often are.
    """
    if not instants:
        return ()
    timescale = _timescale()
    satellite = EarthSatellite(line1, line2, "", timescale)
    observer = wgs84.latlon(site.lat_deg, site.lon_deg, elevation_m=site.alt_m)
    times = timescale.from_datetimes(list(instants))
    elevation, azimuth, _ = (satellite - observer).at(times).altaz()
    return tuple(
        SkyPoint(azimuth_deg=float(az), elevation_deg=float(el))
        for az, el in zip(azimuth.degrees, elevation.degrees, strict=True)
    )


@cache
def _timescale() -> Timescale:
    """Built once: immutable, and parsing its tables is the slow part."""
    return load.timescale(builtin=True)
