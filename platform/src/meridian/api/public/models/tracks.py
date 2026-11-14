"""Where each tracked satellite is, and where it goes next, as the public API states it.

The dashboard's live page draws every satellite over the map with the next
ninety minutes of its ground track. The browser runs no propagator: the orbit
service samples each track on the platform, from the element set pass
generation would use, and this is the body it is served in (D-361).

**Nothing here describes a station.** A sub-satellite point is a property of an
orbit and a public element set, so no field is coarsened for privacy. Points
are published to a hundredth of a degree, about a kilometre, because SGP4 from
a day-old element set is good to a few kilometres and a finer figure would
claim more than the propagation knows.

**Element-set age travels with every track** (CLAUDE.md: a first-class feature
everywhere). A track from a month-old set is drawn, and says how old it is.

No ``simulated`` field: satellites and their element sets are real, and a
simulator run invents stations and observations, not orbits (as
``models/satellites.py`` says of the catalogue). An element set's ``source``
is published, so a hand-entered set reads as one.

This module builds no SQL and propagates nothing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

__all__ = [
    "OmissionReason",
    "OmittedSatellite",
    "SatelliteTrack",
    "SatelliteTracks",
    "TrackPoint",
]

OmissionReason = Literal["no_element_set", "propagation_failed", "over_limit"]


class TrackPoint(BaseModel):
    """The ground beneath a satellite at one instant."""

    t: datetime
    lat_deg: float
    """Geodetic latitude on WGS84, to a hundredth of a degree."""

    lon_deg: float
    """Longitude east, -180 to 180, to a hundredth of a degree. A track that
    crosses the antimeridian jumps sign between two points; the reader splits
    the line there."""


class SatelliteTrack(BaseModel):
    """One satellite's sampled ground track, from the response's ``at``."""

    satellite_id: str
    name: str
    is_active: bool
    """Whether the satellite is still expected to transmit. A silent one is
    still drawn: it is still overhead."""

    element_set_epoch: datetime
    element_set_age_s: float
    """Seconds from the set's epoch to the response's ``at``. Negative for a set
    published ahead of its epoch, as ``/satellites`` reports it."""

    element_set_source: str
    """Where the set came from: ``celestrak``, ``manual``, and so on."""

    track: list[TrackPoint]
    """One point a step from ``at`` to ``at`` plus the horizon, both included.
    The first is where the satellite was at ``at``."""


class OmittedSatellite(BaseModel):
    """A tracked satellite with no track, and why: an absence said in words."""

    satellite_id: str
    name: str
    reason: OmissionReason
    """``no_element_set``: none held from before ``at``. ``propagation_failed``:
    SGP4 refused the set, as it does for one describing a decayed orbit.
    ``over_limit``: the catalogue holds more satellites than one response
    propagates. It names the first satellite past the limit, and says the
    response was cut there; those after it are not read."""


class SatelliteTracks(BaseModel):
    """Every tracked satellite's ground track, sampled at one instant."""

    status: Literal["computed"]
    at: datetime
    """The minute the tracks start from, floored from the platform's clock. One
    minute's answer is computed once and served to every reader in it."""

    step_s: int
    horizon_s: int
    satellites: list[SatelliteTrack]
    omitted: list[OmittedSatellite]
