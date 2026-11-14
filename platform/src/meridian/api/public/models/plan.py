"""The station planner's answer: the next two days of passes over a place.

Someone deciding whether a station is worth building gives a place, and is
told which of the satellites Meridian receives would pass over it in the next
48 hours, how high each would climb, and how likely configuration A's model
says each is to decode (D-370).

**The place is the reader's own, rounded to a tenth of a degree before it is
used**, about 11 km: MSP's coarsest declared precision (D-082), and the grain
``/conditions`` publishes a point at. It is echoed rounded, never as typed.
Nothing about it is stored: no row is written, the front keeps no copy and the
log line withholds it.

**Windows and angles are published as every other pass is** (D-093): a
window widened to whole minutes, the culmination's time floored to its
minute, angles to whole degrees. The place is not a station's, so this is
for one rule everywhere, not for a station's privacy.

**A predicted yield is a figure or a stated absence**, never a null (D-086).
Configuration A reads a pass's peak elevation and nothing else, so it says how
a typical station of the network would do with that pass, not how a station
on that roof, with its horizon and its interference, would.

No ``simulated`` field: the orbits are real, the place is the reader's, and
nothing here was received. The model's training population is named instead,
and simulated receptions never train one (D-078).

This module builds no SQL and propagates nothing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from meridian.api.public.models.not_yet_computed import NotYetComputed
from meridian.api.public.models.reliability import NotMeasuredFigure
from meridian.api.public.models.tracks import OmittedSatellite

__all__ = [
    "PlanDownlink",
    "PlanPass",
    "PlanPlace",
    "PlanSatellite",
    "PlanYield",
    "PlanYieldModel",
    "PublicPlan",
]


class PlanPlace(BaseModel):
    """Where the passes were computed for."""

    lat_deg: float
    """Geodetic latitude, rounded to a tenth of a degree."""

    lon_deg: float
    """Longitude east, -180 to 180, rounded to a tenth of a degree."""

    alt_m: int
    """Always 0: sea level. A site's height moves a pass by seconds, which the
    published minutes do not show."""


class PlanYieldModel(BaseModel):
    """The model the yields come from, named so it can be looked up."""

    status: Literal["computed"]
    configuration: Literal["A"]
    """Peak elevation only (D-160)."""

    population: Literal["own", "archive"]
    """Whose receptions it was fitted on: Meridian's own stations, or a public
    archive's (D-156, D-322)."""

    model_sha256: str
    """The fitted model's hash; ``meridian model show`` prints it (D-163)."""

    as_of: datetime
    """When the dataset it was fitted on was taken."""


class PlanYield(BaseModel):
    """Configuration A's probability that the pass decodes."""

    status: Literal["computed"]
    probability: float
    """To a hundredth."""


class PlanPass(BaseModel):
    """One pass over the place."""

    satellite_id: str
    aos: datetime
    """Acquisition, floored to the minute."""

    los: datetime
    """Loss, ceiled to the minute, so the window holds the real one."""

    max_elevation_deg: int
    max_elevation_at: datetime
    """The culmination, floored to the minute."""

    aos_azimuth_deg: int
    los_azimuth_deg: int
    predicted_yield: Annotated[
        PlanYield | NotMeasuredFigure, Field(discriminator="status")
    ]


class PlanDownlink(BaseModel):
    """One live downlink a station would tune to."""

    centre_freq_hz: int
    mode: str


class PlanSatellite(BaseModel):
    """A satellite searched over the place, and the element set it was from."""

    satellite_id: str
    name: str
    downlinks: list[PlanDownlink]
    element_set_epoch: datetime
    element_set_age_s: float
    """Seconds from the set's epoch to ``at``. A pass two days out is
    predicted from a set two days older than it is now."""

    element_set_source: str


class PublicPlan(BaseModel):
    """Every pass over a place in the next 48 hours."""

    status: Literal["computed"]
    at: datetime
    """The minute the plan starts from, floored from the platform's clock. A
    pass under way at ``at`` is included."""

    until: datetime
    """``at`` plus 48 hours. A pass rising before it is included whole."""

    place: PlanPlace
    min_elevation_deg: int
    """The floor every pass was searched against: a window is the time spent
    above it."""

    yield_model: Annotated[
        PlanYieldModel | NotYetComputed, Field(discriminator="status")
    ]
    satellites: list[PlanSatellite]
    passes: list[PlanPass]
    """In order of acquisition."""

    omitted: list[OmittedSatellite]
    """Satellites with a live downlink that were not searched, and why."""
