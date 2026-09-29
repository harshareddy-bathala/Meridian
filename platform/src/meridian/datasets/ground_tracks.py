"""Where each observed pass was over the ground, computed once at export.

Stage 32 asks which of our receptions cover an area of interest, and "cover"
is a fact about the ground beneath the satellite while the station heard it —
not about the station's sky. So the export propagates each pass that has a
report, over its own window, and freezes the sub-satellite points as
``pass_ground_tracks.jsonl``, for the reason ``pass_tracks.jsonl`` exists: no
regional computation propagates, and no series' hash rests on ``sgp4``
agreeing to the last bit across machines (D-158, D-230).

**Every pass with a report gets one, simulated or not**, and says which. A
simulated reception is excluded from coverage unless it is asked for by name,
and it can only be asked for if its track exists; the flag travels with the
row so the exclusion is a filter anyone can read, not an absence.

**Sampled every 30 s over ``[aos, los)``**, latitude and longitude to three
decimals of a degree — about a hundred metres, far inside any swath.

**What cannot be computed is counted**, under ``pass_ground_tracks.*``.

Pure: rows in, rows and counts out. The orbit service is handed in.

Reference: docs/DECISIONS.md D-158, D-230.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from meridian.orbit.types import ElementSet, SubPoint

__all__ = [
    "GROUND_STEP_S",
    "GROUND_TRACKS",
    "GroundRows",
    "GroundTrackFinder",
    "GroundTracks",
    "compute_ground_tracks",
]

GROUND_TRACKS = "pass_ground_tracks"
GROUND_STEP_S = 30
_PLACES = 3
_COUNTS = ("without_element_set",)


class GroundTrackFinder(Protocol):
    """The one part of ``OrbitService`` a ground track needs."""

    def ground_track(
        self, element_set: ElementSet, start: datetime, end: datetime, *, step_s: float
    ) -> list[SubPoint]:
        """The sub-satellite point every ``step_s`` over ``[start, end)``."""
        ...


@dataclass(frozen=True, slots=True)
class GroundRows:
    """What a ground track is computed from, as the export read it."""

    passes: Sequence[Mapping[str, object]]
    assignments: Sequence[Mapping[str, object]]
    observations: Sequence[Mapping[str, object]]
    element_sets: Sequence[Mapping[str, object]]


@dataclass(frozen=True, slots=True)
class GroundTracks:
    """Every computed ground track, and what could not be computed."""

    rows: tuple[Mapping[str, object], ...]
    counts: Mapping[str, int]


def compute_ground_tracks(rows: GroundRows, orbit: GroundTrackFinder) -> GroundTracks:
    """Propagate every pass that has a report, over its own window.

    Args:
        rows: The passes, their assignments and reports, and element sets.
        orbit: The orbit service to propagate with.

    Returns:
        The tracks in ``pass_id`` order, and the counts.
    """
    reported = {str(one["assignment_id"]) for one in rows.observations}
    wanted = {
        _int(one["pass_id"])
        for one in rows.assignments
        if str(one["assignment_id"]) in reported
    }
    element_sets = {_int(one["id"]): _element_set(one) for one in rows.element_sets}
    counts = dict.fromkeys(_COUNTS, 0)
    tracks = []
    for one in sorted(rows.passes, key=lambda row: _int(row["id"])):
        if _int(one["id"]) not in wanted:
            continue
        element_set = element_sets.get(_int(one["element_set_id"]))
        if element_set is None:
            counts["without_element_set"] += 1
            continue
        tracks.append(_track(one, element_set, orbit))
    return GroundTracks(
        rows=tuple(tracks),
        counts={GROUND_TRACKS: len(tracks)}
        | {f"{GROUND_TRACKS}.{name}": value for name, value in counts.items()},
    )


def _track(
    row: Mapping[str, object], element_set: ElementSet, orbit: GroundTrackFinder
) -> Mapping[str, object]:
    aos, los = _instant(row["aos"]), _instant(row["los"])
    points = orbit.ground_track(element_set, aos, los, step_s=GROUND_STEP_S)
    return {
        "pass_id": _int(row["id"]),
        "satellite_id": str(row["satellite_id"]),
        "station_id": str(row["station_id"]),
        "simulated": row["simulated"] is True,
        "start": aos,
        "step_s": GROUND_STEP_S,
        "lat_deg": [round(one.lat_deg, _PLACES) for one in points],
        "lon_deg": [round(one.lon_deg, _PLACES) for one in points],
    }


def _element_set(row: Mapping[str, object]) -> ElementSet:
    return ElementSet(
        satellite_id=str(row["satellite_id"]),
        epoch=_instant(row["epoch"]),
        line1=str(row["line1"]),
        line2=str(row["line2"]),
    )


def _instant(value: object) -> datetime:
    if not isinstance(value, datetime):
        message = f"expected a timestamp, found {type(value).__name__}"
        raise TypeError(message)
    return value


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        message = f"expected an integer, found {type(value).__name__}"
        raise TypeError(message)
    return value
