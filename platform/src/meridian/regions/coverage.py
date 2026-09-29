"""Which of our own receptions cover an area — the part that is Meridian's.

Everything else in a regional report is somebody else's product placed on an
area. This is ours: which passes our stations received imaged the ground in
question. A reception **covers** an area when, at some instant between its
start and end, the ground beneath the satellite lay within half the imager's
swath of the area (D-230). The ground tracks were frozen at export, so nothing
here propagates.

**Only a decoded reception is imagery**, so only a decoded one counts. A pass
that was merely scheduled over the area, or heard without a decode, covers
nothing and is not listed.

**Simulated receptions are left out unless asked for by name**
(``include_simulated``), and even then are counted apart and carry their flag
on every row (rule 5, Stage 32's test list).

Reference: docs/DECISIONS.md D-230.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from meridian.regions.geometry import Polygon
from meridian.regions.rows import GroundTrack, Reception

__all__ = ["DECODED", "Covering", "covering_receptions"]

DECODED = "decoded"


@dataclass(frozen=True, slots=True)
class Covering:
    """One reception that imaged the area."""

    area_id: int
    assignment_id: str
    pass_id: int
    station_id: str
    satellite_id: str
    started_at: datetime
    ended_at: datetime | None
    closest_km: float
    """The nearest the ground beneath the satellite came to the area while the
    station received it."""

    simulated: bool


def covering_receptions(  # noqa: PLR0913 — two settings beside four inputs
    area_id: int,
    polygon: Polygon,
    receptions: Sequence[Reception],
    tracks: Mapping[int, GroundTrack],
    *,
    swath_km: float,
    include_simulated: bool,
) -> list[Covering]:
    """Every decoded reception whose ground track came within half a swath.

    Args:
        area_id: The area.
        polygon: Its shape.
        receptions: Each assignment's latest report.
        tracks: The frozen ground tracks, by pass.
        swath_km: The imager's swath width.
        include_simulated: Whether simulated receptions are asked for.

    Returns:
        The covering receptions, by start then assignment.
    """
    reach = swath_km / 2.0
    found = []
    for one in receptions:
        if one.outcome != DECODED or (one.simulated and not include_simulated):
            continue
        track = tracks.get(one.pass_id)
        if track is None:
            continue
        closest = _closest_km(polygon, track, one)
        if closest is not None and closest <= reach:
            found.append(
                Covering(
                    area_id=area_id,
                    assignment_id=one.assignment_id,
                    pass_id=one.pass_id,
                    station_id=one.station_id,
                    satellite_id=one.satellite_id,
                    started_at=one.started_at,
                    ended_at=one.ended_at,
                    closest_km=round(closest, 1),
                    simulated=one.simulated,
                )
            )
    return sorted(found, key=lambda one: (one.started_at, one.assignment_id))


def _closest_km(polygon: Polygon, track: GroundTrack, one: Reception) -> float | None:
    """The nearest approach while the station received, or None if never sampled."""
    distances = []
    for index, (lat, lon) in enumerate(track.points):
        at = track.start + timedelta(seconds=index * track.step_s)
        if at < one.started_at - timedelta(seconds=track.step_s):
            continue
        if one.ended_at is not None and at > one.ended_at + timedelta(
            seconds=track.step_s
        ):
            continue
        distances.append(polygon.distance_km(lat, lon))
    return min(distances) if distances else None
