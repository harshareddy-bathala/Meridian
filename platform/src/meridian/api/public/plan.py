"""``/api/v1/plan`` — the next 48 hours of passes over a place, for the planner.

Someone deciding whether a station is worth building asks what would pass
over a place, how high, and how often it would decode. This route answers for
every satellite with a live downlink, from the element set pass generation
would use, scored by configuration A's model when one is published (D-370).

**Nothing is stored.** The place is rounded to a tenth of a degree as it
arrives and is used for nothing but the answer: no row is written, the front
forwards the request without keeping a copy, and the log line withholds the
query (:mod:`meridian.log_redaction`). What the process does keep, a few
dozen recent places' windows, is in memory and keyed by the rounded place.

**One search per place an hour.** The windows are searched from twenty
minutes before the hour to 49 hours after it, so every minute of the hour is
answered from them: the pass under way at the minute, and every pass rising in
the 48 hours after. Each satellite's coarse scan shares one time grid, so the
Earth's orientation is computed once per hour for every place asked (D-370).
The windows are kept until the hour, an element set or the catalogue changes.
One search runs at a time, and the planner has a rate of its own (D-202).

Which windows are searched, and what the answer says of them, is
:mod:`meridian.api.public.plan_search`'s, which is pure; this module reads
the catalogue and the model, keeps the windows and serves the route.

Not paged (D-085): one computed view, bounded by :data:`MAX_SATELLITES`, with
a satellite beyond it named rather than dropped.

Reference: docs/DECISIONS.md D-082, D-085, D-086, D-093, D-160, D-202, D-203,
D-361, D-370.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from meridian.api import platform_clock
from meridian.api.dependencies import get_connection, get_settings
from meridian.api.public.envelope import SERVER_ERROR, PublicError
from meridian.api.public.models.plan import PublicPlan
from meridian.api.public.plan_model import (
    HeldPlanModel,
    PlanModelRefusedError,
    read_plan_model,
)
from meridian.api.public.plan_search import (
    MAX_SATELLITES,
    Candidate,
    PlanOrbit,
    Windows,
    build_plan,
    round_place,
    search_windows,
)
from meridian.config import Settings
from meridian.orbit.skyfield_service import SkyfieldOrbitService
from meridian.store.element_sets import StoredElementSet, find_current_element_sets
from meridian.store.satellite_catalogue import find_satellites_after
from meridian.store.satellites import StoredTransmitter, find_active_transmitters
from meridian.store.stations import Connection

__all__ = ["router"]

_KEPT_PLACES = 64
"""How many places' windows one process keeps, newest used first."""

_log = logging.getLogger(__name__)

router = APIRouter()


class _WindowCache:
    """The hour's windows for the places asked about lately, one search at a time.

    Keyed on the rounded place, the hour, and every searched satellite's id
    and element set id, so a new set is searched at once. A lock, because a
    synchronous route runs in a thread pool, and a burst of new places should
    queue for the one propagator rather than share the host's CPU between
    them.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._kept: OrderedDict[tuple[object, ...], Windows] = OrderedDict()
        self._orbit: PlanOrbit | None = None

    def windows(
        self,
        candidates: Sequence[Candidate],
        place: tuple[float, float],
        hour: datetime,
    ) -> Windows:
        searched = candidates[:MAX_SATELLITES]
        key = (
            place,
            hour,
            *(
                (one.satellite.satellite_id, _set_id(one.element_set))
                for one in searched
            ),
        )
        with self._lock:
            kept = self._kept.get(key)
            if kept is not None:
                self._kept.move_to_end(key)
                return kept
            if self._orbit is None:
                self._orbit = SkyfieldOrbitService()
            found = search_windows(self._orbit, searched, place, hour)
            self._kept[key] = found
            while len(self._kept) > _KEPT_PLACES:
                self._kept.popitem(last=False)
            return found


def _set_id(stored: StoredElementSet | None) -> int | None:
    return None if stored is None else stored.id


_CACHE = _WindowCache()


def _candidates(conn: Connection, at: datetime) -> list[Candidate]:
    """Every catalogued satellite with a live downlink, in catalogue order."""
    downlinks: dict[str, list[StoredTransmitter]] = {}
    for transmitter in find_active_transmitters(conn):
        downlinks.setdefault(transmitter.satellite_id, []).append(transmitter)
    element_sets = find_current_element_sets(conn, at)
    candidates: list[Candidate] = []
    after: str | None = None
    page = 200
    while True:
        satellites = find_satellites_after(conn, after, page)
        candidates.extend(
            Candidate(
                satellite=one,
                downlinks=tuple(downlinks[one.satellite_id]),
                element_set=element_sets.get(one.satellite_id),
            )
            for one in satellites
            if one.satellite_id in downlinks
        )
        if len(satellites) < page or len(candidates) > MAX_SATELLITES:
            return candidates
        after = satellites[-1].satellite_id


def _held_model(settings: Settings) -> HeldPlanModel | None:
    """The model ``PLAN_MODEL`` names, or ``None`` when it names none.

    Raises:
        PublicError: ``server_error`` when the file cannot be read or is
            refused. The reason is logged for the operator; the reader is told
            only which setting.
    """
    if not settings.plan_model:
        return None
    try:
        return read_plan_model(Path(settings.plan_model))
    except (OSError, PlanModelRefusedError):
        _log.warning("planner model refused", exc_info=True)
        raise PublicError(
            SERVER_ERROR,
            "The planner model PLAN_MODEL names was refused; the platform's log"
            " says why.",
        ) from None


Latitude = Annotated[float, Query(ge=-90, le=90, allow_inf_nan=False)]
Longitude = Annotated[float, Query(ge=-180, le=180, allow_inf_nan=False)]


@router.get("/plan")
def station_plan(
    lat: Latitude,
    lon: Longitude,
    conn: Connection = Depends(get_connection, scope="function"),
    settings: Settings = Depends(get_settings),
) -> PublicPlan:
    """The next 48 hours of passes over a place, for deciding on a station.

    Args:
        lat: Latitude in degrees, -90 to 90; used to a tenth of a degree.
        lon: Longitude east in degrees, -180 to 180; used to a tenth.
        conn: A pooled connection, injected.
        settings: The deployment's settings, injected.

    Returns:
        Each pass from the current minute, with its peak and, when a model is
        published, configuration A's predicted yield; and each satellite with
        a live downlink that was not searched, by reason.
    """
    held = _held_model(settings)
    at = platform_clock.utc_now().replace(second=0, microsecond=0)
    place = round_place(lat, lon)
    candidates = _candidates(conn, at)
    hour = at.replace(minute=0)
    windows = _CACHE.windows(candidates, place, hour)
    return build_plan(candidates, windows, place, at, held)
