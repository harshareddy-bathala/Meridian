"""Where each pass was in the sky, computed once at export — D-158.

``passes`` holds where a satellite rose and set and how high it climbed, not
the path between. The learned horizon and interference profiles of Stage 17
need the path (D-159): a first detection is placed on the sky by the pass's
elevation at that instant, and a noise floor by the sectors the pass crossed.

So the export propagates each measured pass from its own element set, over its
own station, and freezes the answer as ``pass_tracks.jsonl``. As with the
archive denominator (D-150), nothing downstream of export propagates, and no
feature's hash rests on ``sgp4`` agreeing to the last bit across machines.

**A track is sampled every 30 s over ``[aos, los)``,** in the half-open
convention every window in this project uses. Both angles are written to a
hundredth of a degree: finer than any profile cell, and coarse enough that the
file does not record a machine's floating-point noise as if it were geometry.
Azimuth is then folded into ``[0, 360)``, after rounding, so 359.999° is
written as 0 and never as 360.

**Simulated passes get no track,** and are counted. D-078 keeps them out of
every model, so their tracks would be computed only to be ignored.

**What cannot be computed is counted, not dropped**, each under
``pass_tracks.*`` in the manifest: a pass whose station is not in the
snapshot, and one whose element set is not.

Pure: rows in, rows and counts out. The orbit service is handed in, and only
the orbit's plain types are imported here.

Reference: docs/DECISIONS.md D-078, D-150, D-158.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from meridian.orbit.types import ElementSet, GroundSite, LookAngle

__all__ = [
    "PASS_TRACKS",
    "TRACK_STEP_S",
    "PassTracks",
    "TrackFinder",
    "TrackRows",
    "compute_pass_tracks",
]

PASS_TRACKS = "pass_tracks"
"""The file is ``pass_tracks.jsonl``."""

TRACK_STEP_S = 30
"""Seconds between samples. A low-orbit pass moves a few degrees in that time,
under a 10° azimuth sector (D-159)."""

_PLACES = 2
_COUNTS = ("simulated_skipped", "without_station", "without_element_set")


class TrackFinder(Protocol):
    """The one part of ``OrbitService`` a track needs."""

    def look_angles(
        self,
        element_set: ElementSet,
        site: GroundSite,
        start: datetime,
        end: datetime,
        *,
        step_s: float,
    ) -> list[LookAngle]:
        """Where the satellite is from the site, sampled over ``[start, end)``."""
        ...


@dataclass(frozen=True, slots=True)
class TrackRows:
    """The three tables a track is computed from, as the export read them."""

    passes: Sequence[Mapping[str, object]]
    stations: Sequence[Mapping[str, object]]
    element_sets: Sequence[Mapping[str, object]]


@dataclass(frozen=True, slots=True)
class PassTracks:
    """Every computed track, and what could not be computed."""

    rows: tuple[Mapping[str, object], ...]
    """In ``pass_id`` order."""

    counts: Mapping[str, int]
    """``pass_tracks.<reason>``, every reason present, zeros included, and
    ``pass_tracks`` itself."""


def compute_pass_tracks(rows: TrackRows, orbit: TrackFinder) -> PassTracks:
    """Propagate every measured pass over its own window.

    Args:
        rows: The passes, the stations they belong to, and their element sets.
        orbit: The orbit service to propagate with.

    Returns:
        The tracks and the counts of what could not be computed.
    """
    sites = {str(one["station_id"]): _site(one) for one in rows.stations}
    element_sets = {_int(one["id"]): _element_set(one) for one in rows.element_sets}
    counts = dict.fromkeys(_COUNTS, 0)
    tracks = []
    for one in sorted(rows.passes, key=lambda row: _int(row["id"])):
        if one["simulated"] is True:
            counts["simulated_skipped"] += 1
            continue
        site = sites.get(str(one["station_id"]))
        element_set = element_sets.get(_int(one["element_set_id"]))
        if site is None:
            counts["without_station"] += 1
        elif element_set is None:
            counts["without_element_set"] += 1
        else:
            tracks.append(_track(one, element_set, site, orbit))
    return PassTracks(
        rows=tuple(tracks),
        counts={PASS_TRACKS: len(tracks)}
        | {f"{PASS_TRACKS}.{name}": value for name, value in counts.items()},
    )


def _track(
    row: Mapping[str, object],
    element_set: ElementSet,
    site: GroundSite,
    orbit: TrackFinder,
) -> Mapping[str, object]:
    aos, los = _instant(row["aos"]), _instant(row["los"])
    angles = orbit.look_angles(element_set, site, aos, los, step_s=TRACK_STEP_S)
    return {
        "pass_id": _int(row["id"]),
        "start": aos,
        "step_s": TRACK_STEP_S,
        "azimuth_deg": [round(one.azimuth_deg, _PLACES) % 360.0 for one in angles],
        "elevation_deg": [round(one.elevation_deg, _PLACES) for one in angles],
    }


def _site(row: Mapping[str, object]) -> GroundSite:
    return GroundSite(
        lat_deg=_float(row["lat_deg"]),
        lon_deg=_float(row["lon_deg"]),
        alt_m=_float(row["alt_m"]),
    )


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


def _float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        message = f"expected a number, found {type(value).__name__}"
        raise TypeError(message)
    return float(value)
