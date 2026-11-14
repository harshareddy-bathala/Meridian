"""The station planner's search and answer: what is searched, and what is said.

Pure: no database, no clock, no settings. :mod:`meridian.api.public.plan`
reads the catalogue and the model, keeps the windows, and serves the route;
this module decides which windows to search for, from where and against what,
and turns the hour's windows into the answer for one minute (D-370). Split
from the route so each can be read, and tested, on its own.

Reference: docs/DECISIONS.md D-082, D-086, D-093, D-160, D-361, D-370.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from meridian.api.public.models.not_yet_computed import NotYetComputed
from meridian.api.public.models.plan import (
    PlanDownlink,
    PlanPass,
    PlanPlace,
    PlanSatellite,
    PlanYield,
    PlanYieldModel,
    PublicPlan,
)
from meridian.api.public.models.reliability import NotMeasuredFigure
from meridian.api.public.models.tracks import OmissionReason, OmittedSatellite
from meridian.api.public.plan_model import HeldPlanModel
from meridian.orbit.types import (
    ElementSet,
    GroundSite,
    PassSearch,
    PassWindow,
    SubPoint,
)
from meridian.prediction.score import predict
from meridian.public_precision import ceil_minute, floor_minute, publish_angle
from meridian.store.element_sets import StoredElementSet
from meridian.store.satellite_catalogue import CataloguedSatellite
from meridian.store.satellites import StoredTransmitter

__all__ = [
    "ALT_M",
    "FLOOR_DEG",
    "MAX_SATELLITES",
    "NO_MODEL_REASON",
    "PLACE_DECIMALS",
    "PLAN_HOURS",
    "PLAN_STAGE",
    "Candidate",
    "PlanOrbit",
    "Windows",
    "build_plan",
    "round_place",
    "search_windows",
]

PLAN_HOURS = 48
"""Two days: enough to see a day's pattern twice, and short enough that the
element sets it is predicted from are still good to a minute."""

FLOOR_DEG = 10.0
"""The elevation floor every pass is searched against: the floor MSP-SPEC.md
§4.1's registration example declares. Below it a 137 MHz pass is mostly
terrain and noise, and a station's own floor is set there for that reason."""

PLACE_DECIMALS = 1
"""A tenth of a degree, about 11 km: MSP's coarsest declared precision (D-082).
The rounded place is the only one used, so an exact address never reaches the
search, the answer or the memory that keeps it."""

MAX_SATELLITES = 20
"""The most satellites one answer searches. The 137 MHz satellites Meridian
receives are a handful; a catalogue past this is not one a planner answers."""

PLAN_STAGE = 37
"""Stage 37 — the dashboard for everyone, which builds the planner."""

ALT_M = 0
"""Sea level. A site's height moves a pass by seconds, under a minute's grain."""

_SEARCH_LEAD = timedelta(minutes=20)
"""How far before the hour the search starts: past the longest low-orbit pass
(``PASS_SEARCH_MARGIN_S`` says 16 minutes at 850 km), so a pass already under
way at any minute of the hour was found rising."""

_SEARCH_SPAN = timedelta(hours=PLAN_HOURS + 1)
"""From the hour to an hour past the latest ``until`` of any of its minutes."""

NO_MODEL_REASON = (
    "No configuration A model is published for the planner on this platform."
)


class PlanOrbit(Protocol):
    """The two questions the planner asks the orbit service (``OrbitService``)."""

    def pass_windows(self, search: PassSearch) -> list[PassWindow]:
        """Every pass matching ``search``."""
        ...

    def ground_track(
        self, element_set: ElementSet, start: datetime, end: datetime, *, step_s: float
    ) -> list[SubPoint]:
        """The sub-satellite point every ``step_s`` seconds over ``[start, end)``."""
        ...


@dataclass(frozen=True, slots=True)
class Candidate:
    """A satellite with a live downlink, and the set current at the minute."""

    satellite: CataloguedSatellite
    downlinks: tuple[StoredTransmitter, ...]
    element_set: StoredElementSet | None


Windows = Mapping[str, Sequence[PassWindow] | None]
"""Each searched satellite's windows; ``None`` when SGP4 could not place it."""


def round_place(lat_deg: float, lon_deg: float) -> tuple[float, float]:
    """The place as the planner uses it: to a tenth of a degree, no ``-0.0``."""
    return (
        round(lat_deg, PLACE_DECIMALS) + 0.0,
        round(lon_deg, PLACE_DECIMALS) + 0.0,
    )


def search_windows(
    orbit: PlanOrbit,
    candidates: Sequence[Candidate],
    place: tuple[float, float],
    hour: datetime,
) -> dict[str, Sequence[PassWindow] | None]:
    """Every searched satellite's passes over ``place`` for the hour's minutes.

    Args:
        orbit: The propagator.
        candidates: The satellites to search, each with an element set.
        place: Latitude and longitude, already rounded.
        hour: The hour the minutes asked about fall in.

    Returns:
        The windows rising from twenty minutes before ``hour`` to 49 hours
        after it, by satellite; ``None`` for one SGP4 cannot place.
    """
    site = GroundSite(lat_deg=place[0], lon_deg=place[1], alt_m=ALT_M)
    start = hour - _SEARCH_LEAD
    found: dict[str, Sequence[PassWindow] | None] = {}
    for candidate in candidates:
        if candidate.element_set is None:
            continue
        element_set = _to_element_set(candidate.element_set)
        if not _placeable(orbit, element_set, hour):
            found[candidate.satellite.satellite_id] = None
            continue
        try:
            windows = orbit.pass_windows(
                PassSearch(
                    element_set=element_set,
                    site=site,
                    start=start,
                    end=hour + _SEARCH_SPAN,
                    min_elevation_deg=FLOOR_DEG,
                )
            )
        except ValueError:
            # A pass outlasting the search margin: an orbit far above the
            # low ones this network receives, which the planner does not place.
            windows = None
        found[candidate.satellite.satellite_id] = windows
    return found


def _placeable(orbit: PlanOrbit, element_set: ElementSet, at: datetime) -> bool:
    """Whether SGP4 gives the satellite a position at all, as for ``/tracks``."""
    points = orbit.ground_track(element_set, at, at + timedelta(seconds=1), step_s=1)
    return all(math.isfinite(p.lat_deg + p.lon_deg) for p in points)


def build_plan(
    candidates: Sequence[Candidate],
    windows: Windows,
    place: tuple[float, float],
    at: datetime,
    held: HeldPlanModel | None,
) -> PublicPlan:
    """The answer for ``at``, from the hour's windows.

    Args:
        candidates: Every satellite with a live downlink, in catalogue order.
        windows: What :func:`search_windows` found for the hour ``at`` is in.
        place: Latitude and longitude, already rounded.
        at: The minute asked about.
        held: The planner's model, or ``None`` when none is published.

    Returns:
        The body: passes under way at ``at`` or rising before ``until``.
    """
    until = at + timedelta(hours=PLAN_HOURS)
    satellites: list[PlanSatellite] = []
    passes: list[PlanPass] = []
    omitted: list[OmittedSatellite] = []
    for index, candidate in enumerate(candidates):
        satellite = candidate.satellite
        stored = candidate.element_set
        found = windows.get(satellite.satellite_id)
        if index >= MAX_SATELLITES:
            omitted.append(_omit(satellite, "over_limit"))
            break
        if stored is None:
            omitted.append(_omit(satellite, "no_element_set"))
            continue
        if found is None:
            omitted.append(_omit(satellite, "propagation_failed"))
            continue
        satellites.append(_satellite(candidate, stored, at))
        passes.extend(
            _pass(window, held)
            for window in found
            if window.los > at and window.aos < until
        )
    passes.sort(key=lambda one: (one.aos, one.satellite_id))
    return PublicPlan(
        status="computed",
        at=at,
        until=until,
        place=PlanPlace(lat_deg=place[0], lon_deg=place[1], alt_m=ALT_M),
        min_elevation_deg=round(FLOOR_DEG),
        yield_model=_yield_model(held),
        satellites=satellites,
        passes=passes,
        omitted=omitted,
    )


def _satellite(
    candidate: Candidate, stored: StoredElementSet, at: datetime
) -> PlanSatellite:
    return PlanSatellite(
        satellite_id=candidate.satellite.satellite_id,
        name=candidate.satellite.name,
        downlinks=[
            PlanDownlink(centre_freq_hz=one.centre_freq_hz, mode=one.mode)
            for one in candidate.downlinks
        ],
        element_set_epoch=stored.epoch,
        element_set_age_s=_to_element_set(stored).age_s(at),
        element_set_source=stored.source,
    )


def _pass(window: PassWindow, held: HeldPlanModel | None) -> PlanPass:
    return PlanPass(
        satellite_id=window.satellite_id,
        aos=floor_minute(window.aos),
        los=ceil_minute(window.los),
        max_elevation_deg=publish_angle(window.max_elevation_deg),
        max_elevation_at=floor_minute(window.max_elevation_at),
        aos_azimuth_deg=publish_angle(window.aos_azimuth_deg),
        los_azimuth_deg=publish_angle(window.los_azimuth_deg),
        predicted_yield=_yield(window, held),
    )


def _yield(
    window: PassWindow, held: HeldPlanModel | None
) -> PlanYield | NotMeasuredFigure:
    """Configuration A's probability, from the unrounded peak, as the scheduler's."""
    if held is None:
        return NotMeasuredFigure(reason=NO_MODEL_REASON)
    scored = predict(
        held.model, {"max_elevation_deg": window.max_elevation_deg}, station_history=0
    )
    return PlanYield(status="computed", probability=round(scored.probability, 2))


def _yield_model(held: HeldPlanModel | None) -> PlanYieldModel | NotYetComputed:
    if held is None:
        return NotYetComputed(reason=NO_MODEL_REASON, available_from_stage=PLAN_STAGE)
    return PlanYieldModel(
        status="computed",
        configuration=held.file.configuration,
        population=held.file.population,
        model_sha256=held.file.model_sha256,
        as_of=held.file.as_of,
    )


def _omit(satellite: CataloguedSatellite, reason: OmissionReason) -> OmittedSatellite:
    return OmittedSatellite(
        satellite_id=satellite.satellite_id, name=satellite.name, reason=reason
    )


def _to_element_set(stored: StoredElementSet) -> ElementSet:
    """The orbit module's shape of an archived element set."""
    return ElementSet(
        satellite_id=stored.satellite_id,
        epoch=stored.epoch,
        line1=stored.line1,
        line2=stored.line2,
        source=stored.source,
    )
