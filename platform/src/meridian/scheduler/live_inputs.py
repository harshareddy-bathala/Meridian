"""The passes a run decides, described as the live scorer reads them — D-169.

Prediction reaches no orbit and no database, so this module does both for it:
it reads each candidate's rise from the stored predictions, and computes each
prediction's track, then hands :mod:`meridian.prediction.live` plain geometry.

**The same geometry the model was trained on.** Export freezes a track per
pass for the training set (D-158): sampled every 30 s over ``[aos, los)``,
each angle rounded to a hundredth of a degree and azimuth folded into
``[0, 360)`` after rounding, and no track at all for a simulated pass. The
track computed here follows each of those rules, and a unit test holds it equal
to the export's for the same pass, so the live path cannot drift from the one
the model learned from.

**A rise is predictions of one satellite over one station whose ``[aos, los)``
windows overlap, transitively** — D-148's grouping, over what this run read.

Reference: docs/DECISIONS.md D-148, D-158, D-169.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime

from meridian.orbit.service import OrbitService
from meridian.orbit.types import ElementSet, GroundSite
from meridian.prediction.live import LivePass, PassGeometry, PassTrack
from meridian.store.passes import StoredPass
from meridian.store.receiving_stations import ReceivingStation

__all__ = ["TRACK_STEP_S", "live_inputs", "rises", "track_of"]

TRACK_STEP_S = 30
"""The export's step (D-158). A unit test holds the two equal."""

_PLACES = 2


def rises(stored: Sequence[StoredPass]) -> dict[int, tuple[int, ...]]:
    """Every prediction's rise, as the sorted ids of all its predictions."""
    found: dict[int, tuple[int, ...]] = {}
    by_satellite: dict[str, list[StoredPass]] = {}
    for one in stored:
        by_satellite.setdefault(one.satellite_id, []).append(one)
    for predictions in by_satellite.values():
        group: list[StoredPass] = []
        group_end: datetime | None = None
        for one in sorted(predictions, key=lambda row: (row.aos, row.id)):
            if group_end is not None and one.aos >= group_end:
                _close(group, found)
                group = []
                group_end = None
            group.append(one)
            group_end = one.los if group_end is None else max(group_end, one.los)
        _close(group, found)
    return found


def _close(group: Sequence[StoredPass], found: dict[int, tuple[int, ...]]) -> None:
    members = tuple(sorted(one.id for one in group))
    for one in group:
        found[one.id] = members


def track_of(
    orbit: OrbitService,
    element_set: ElementSet,
    site: GroundSite,
    stored: StoredPass,
) -> PassTrack | None:
    """The pass's track as export freezes it; ``None`` for a simulated pass."""
    if stored.simulated:
        return None
    angles = orbit.look_angles(
        element_set, site, stored.aos, stored.los, step_s=TRACK_STEP_S
    )
    return PassTrack(
        start=stored.aos,
        step_s=TRACK_STEP_S,
        azimuth_deg=tuple(round(one.azimuth_deg, _PLACES) % 360.0 for one in angles),
        elevation_deg=tuple(round(one.elevation_deg, _PLACES) for one in angles),
    )


def live_inputs(
    orbit: OrbitService,
    station: ReceivingStation,
    stored: Mapping[int, StoredPass],
    candidate_ids: Sequence[int],
    element_set_for: Callable[[int], ElementSet],
) -> tuple[list[LivePass], dict[int, PassGeometry]]:
    """The candidates as live passes, and the geometry of every prediction they name.

    Args:
        orbit: The propagator, for tracks.
        station: Where the passes are seen from.
        stored: Every prediction this run read for the station, by id.
        candidate_ids: The predictions being decided.
        element_set_for: Each prediction's element set, by its id.

    Returns:
        One live pass per candidate, in the order given, and the geometry of
        each candidate and of every other prediction of its rise.
    """
    site = GroundSite(
        lat_deg=station.lat_deg, lon_deg=station.lon_deg, alt_m=station.alt_m
    )
    rise_of = rises(list(stored.values()))
    passes, geometry = [], {}
    for pass_id in candidate_ids:
        one = stored[pass_id]
        members = rise_of[pass_id]
        passes.append(
            LivePass(
                pass_id=pass_id,
                pass_ids=members,
                station_id=one.station_id,
                satellite_id=one.satellite_id,
                aos=one.aos,
                los=one.los,
                simulated=one.simulated,
            )
        )
        for member in members:
            if member not in geometry:
                predicted = stored[member]
                element_set = element_set_for(predicted.element_set_id)
                geometry[member] = PassGeometry(
                    aos=predicted.aos,
                    computed_at=predicted.computed_at,
                    max_elevation_deg=predicted.max_elevation_deg,
                    aos_azimuth_deg=predicted.aos_azimuth_deg,
                    los_azimuth_deg=predicted.los_azimuth_deg,
                    element_set_epoch=element_set.epoch,
                    track=track_of(orbit, element_set, site, predicted),
                )
    return passes, geometry
