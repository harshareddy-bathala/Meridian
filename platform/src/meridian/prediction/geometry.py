"""Angles on a circle, and where a pass went — shared by features and profiles.

Azimuth wraps at north, so every difference, midpoint and encoding of one is
taken on the circle here, once. The features read a pass's peak and sweep; the
learned profiles place a pass, and a detection on it, in a sector of sky.

Reference: docs/DECISIONS.md D-158, D-159.
"""

from __future__ import annotations

import math
from itertools import pairwise

from meridian.prediction.feature_rows import PassGeometry, PassTrack

__all__ = ["circle", "peak_and_sweep", "sector", "track_at", "turn"]

FULL_TURN = 360.0
_HALF_TURN = 180.0


def turn(start_deg: float, end_deg: float) -> float:
    """The signed short way from one azimuth to another, in (-180, 180]."""
    delta = (end_deg - start_deg) % FULL_TURN
    return delta - FULL_TURN if delta > _HALF_TURN else delta


def circle(azimuth_deg: float) -> tuple[float, float]:
    """An azimuth as its sine and cosine, so north is not a discontinuity."""
    radians = math.radians(azimuth_deg)
    return math.sin(radians), math.cos(radians)


def sector(azimuth_deg: float, width_deg: float) -> int:
    """Which ``width_deg`` slice of the sky an azimuth is in, counted from north."""
    return int((azimuth_deg % FULL_TURN) // width_deg)


def peak_and_sweep(geometry: PassGeometry) -> tuple[float, float]:
    """The azimuth at the peak, and how far azimuth travels over the pass.

    From the track where there is one. Without it, the peak is taken as the
    circular midpoint of rise and set, and the sweep as the short way between
    them — right for most passes, and ``track_known`` says it was a guess.
    """
    track = geometry.track
    if track is None or not track.azimuth_deg:
        rise, set_ = geometry.aos_azimuth_deg, geometry.los_azimuth_deg
        between = turn(rise, set_)
        return (rise + between / 2.0) % FULL_TURN, abs(between)
    elevations = track.elevation_deg
    peak = track.azimuth_deg[elevations.index(max(elevations))]
    sweep = sum(abs(turn(a, b)) for a, b in pairwise(track.azimuth_deg))
    return peak, sweep


def track_at(track: PassTrack, seconds: float) -> tuple[float, float]:
    """Azimuth and elevation ``seconds`` after the track's start, interpolated.

    Linear between the two samples either side, on the circle for azimuth.
    Before the first sample the first is used, after the last the last: a
    detection a few seconds outside ``[aos, los)`` is placed at the edge of the
    pass, not dropped.
    """
    last = len(track.azimuth_deg) - 1
    position = min(max(seconds / track.step_s, 0.0), float(last))
    index = min(int(position), max(last - 1, 0))
    if last == 0:
        return track.azimuth_deg[0], track.elevation_deg[0]
    fraction = position - index
    azimuth = track.azimuth_deg[index] + fraction * turn(
        track.azimuth_deg[index], track.azimuth_deg[index + 1]
    )
    elevation = track.elevation_deg[index] + fraction * (
        track.elevation_deg[index + 1] - track.elevation_deg[index]
    )
    return azimuth % FULL_TURN, elevation
