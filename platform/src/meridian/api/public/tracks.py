"""``/api/v1/satellites/tracks`` — every tracked satellite's next ninety minutes.

The live page draws each satellite where it is and where it goes next, so the
browser needs ground tracks and must not propagate them itself: SGP4 belongs to
``meridian.orbit`` alone (ARCHITECTURE.md rule 2). This route samples each
track with the orbit service, from the element set pass generation would use at
the same instant (D-361).

**One answer per minute.** ``at`` is the platform's clock floored to the
minute, and the body computed for a minute is kept and served to every request
in it, until the minute or an element set changes. Propagating every satellite
for every visitor's poll would spend the host's CPU on identical answers; a
minute is also the grain every other public window is published at (D-093).
The kept body lives in the process, so each worker computes its own.

Not paged, unlike the catalogue (D-085): this is one computed view of the
catalogue, as ``/conditions`` is, not a list of rows. Its size is bounded by
:data:`MAX_SATELLITES`, and a satellite beyond it is named as omitted rather
than dropped.

Reference: docs/DECISIONS.md D-085, D-086, D-093, D-361; ARCHITECTURE.md.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from fastapi import APIRouter, Depends

from meridian.api import platform_clock
from meridian.api.dependencies import get_connection
from meridian.api.public.models.tracks import (
    OmissionReason,
    OmittedSatellite,
    SatelliteTrack,
    SatelliteTracks,
    TrackPoint,
)
from meridian.orbit.skyfield_service import SkyfieldOrbitService
from meridian.orbit.types import ElementSet, SubPoint
from meridian.store.element_sets import StoredElementSet, find_current_element_sets
from meridian.store.satellite_catalogue import (
    CataloguedSatellite,
    find_satellites_after,
)
from meridian.store.stations import Connection

__all__ = [
    "HORIZON_S",
    "MAX_SATELLITES",
    "STEP_S",
    "GroundTracker",
    "build_tracks",
    "router",
]

STEP_S = 60
"""A point a minute: about 450 km of ground in low Earth orbit, smooth enough
for a line across a world map, and 91 points a satellite."""

HORIZON_S = 90 * 60
"""Ninety minutes, about one low-Earth orbit, so a reader sees a whole lap."""

MAX_SATELLITES = 200
"""The most one response propagates, the public API's largest page (D-085)."""

DECIMALS = 2
"""A hundredth of a degree, about a kilometre; see ``models/tracks.py``."""

router = APIRouter()


class GroundTracker(Protocol):
    """The one question this route asks the orbit service (``OrbitService``)."""

    def ground_track(
        self, element_set: ElementSet, start: datetime, end: datetime, *, step_s: float
    ) -> list[SubPoint]:
        """The sub-satellite point every ``step_s`` seconds over ``[start, end)``."""
        ...


def build_tracks(
    orbit: GroundTracker,
    satellites: Sequence[CataloguedSatellite],
    element_sets: Mapping[str, StoredElementSet],
    at: datetime,
) -> SatelliteTracks:
    """Every satellite's track from ``at``, or the reason it has none.

    Args:
        orbit: The propagator.
        satellites: The catalogue, in catalogue order.
        element_sets: The set current at ``at`` for each satellite holding one.
        at: The instant the tracks start from, timezone-aware UTC.

    Returns:
        The body, satellites in catalogue order.
    """
    tracks: list[SatelliteTrack] = []
    omitted: list[OmittedSatellite] = []
    end = at + timedelta(seconds=HORIZON_S + STEP_S)
    for index, satellite in enumerate(satellites):
        stored = element_sets.get(satellite.satellite_id)
        if index >= MAX_SATELLITES:
            omitted.append(_omit(satellite, "over_limit"))
        elif stored is None:
            omitted.append(_omit(satellite, "no_element_set"))
        else:
            element_set = _to_element_set(stored)
            sampled = orbit.ground_track(element_set, at, end, step_s=STEP_S)
            if not all(math.isfinite(p.lat_deg + p.lon_deg) for p in sampled):
                omitted.append(_omit(satellite, "propagation_failed"))
                continue
            tracks.append(
                SatelliteTrack(
                    satellite_id=satellite.satellite_id,
                    name=satellite.name,
                    is_active=satellite.is_active,
                    element_set_epoch=stored.epoch,
                    element_set_age_s=element_set.age_s(at),
                    element_set_source=stored.source,
                    track=[
                        TrackPoint(
                            t=point.t,
                            lat_deg=round(point.lat_deg, DECIMALS),
                            lon_deg=round(point.lon_deg, DECIMALS),
                        )
                        for point in sampled
                    ],
                )
            )
    return SatelliteTracks(
        status="computed",
        at=at,
        step_s=STEP_S,
        horizon_s=HORIZON_S,
        satellites=tracks,
        omitted=omitted,
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


@dataclass(frozen=True, slots=True)
class _Kept:
    """The body computed for one minute and one state of the catalogue."""

    key: tuple[object, ...]
    body: SatelliteTracks


class _MinuteCache:
    """Keeps the last body, so one minute's answer is computed once.

    Keyed on the minute and on everything the body is computed from: each
    satellite's id, name and activity, and the id of its element set. A new set
    or a renamed satellite is served at once, not a minute late. A lock, because
    FastAPI runs a synchronous route in a thread pool, and two requests at the
    start of a minute should not both propagate the catalogue.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._kept: _Kept | None = None
        self._orbit: GroundTracker | None = None

    def body(
        self,
        satellites: Sequence[CataloguedSatellite],
        element_sets: Mapping[str, StoredElementSet],
        at: datetime,
    ) -> SatelliteTracks:
        key = (
            at,
            *(
                (s.satellite_id, s.name, s.is_active, _set_id(element_sets, s))
                for s in satellites
            ),
        )
        with self._lock:
            if self._kept is None or self._kept.key != key:
                if self._orbit is None:
                    self._orbit = SkyfieldOrbitService()
                body = build_tracks(self._orbit, satellites, element_sets, at)
                self._kept = _Kept(key=key, body=body)
            return self._kept.body


def _set_id(
    element_sets: Mapping[str, StoredElementSet], satellite: CataloguedSatellite
) -> int | None:
    stored = element_sets.get(satellite.satellite_id)
    return None if stored is None else stored.id


_CACHE = _MinuteCache()


# Registered ahead of ``/satellites/{satellite_id}`` in ``surface.py``: a route
# is matched in order, and that one would otherwise take ``tracks`` as an id.
@router.get("/satellites/tracks")
def satellite_tracks(
    conn: Connection = Depends(get_connection, scope="function"),
) -> SatelliteTracks:
    """Every tracked satellite's next ninety minutes over the ground.

    Args:
        conn: A pooled connection, injected.

    Returns:
        Each satellite's sampled track from the current minute, with its element
        set's epoch, age and source, and each satellite without one, by reason.
    """
    at = platform_clock.utc_now().replace(second=0, microsecond=0)
    satellites = find_satellites_after(conn, None, MAX_SATELLITES + 1)
    element_sets = find_current_element_sets(conn, at)
    return _CACHE.body(satellites, element_sets, at)
