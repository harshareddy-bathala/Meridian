"""Azimuth as a continuous angle, so a rotator never takes the long way round.

Compass azimuth wraps at north: a satellite crossing from 359° to 1° has moved
two degrees, but the numbers say it moved back 358. A rotator handed that
sequence slews almost a full turn in the wrong direction, in the middle of a
pass that will not repeat. This module removes the wrap by letting the series
run outside 0–360 instead — 359°, 361° — so successive values differ by the
angle actually travelled.

Plain arithmetic on a list of numbers — no I/O, no clock, and nothing here knows
what a satellite is. That is why it sits in its own file: it can be tested
exhaustively in milliseconds, and the rule it implements belongs to every
propagator rather than to the one we happen to use.

Reference: docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md Stage 6, "azimuth
continuity"; ``meridian.orbit.service.OrbitService.look_angles``.
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = ["AMBIGUOUS_STEP_DEG", "unwrap_azimuth_deg"]

HALF_TURN_DEG = 180.0
FULL_TURN_DEG = 360.0

AMBIGUOUS_STEP_DEG = HALF_TURN_DEG
"""The step at which "shortest way round" stops having a unique answer.

Exactly half a turn is equally far in both directions, so no unwrapping can
recover which way the antenna actually travelled. This is a statement about the
sampling interval rather than about the sky: a pass sampled finely enough never
produces it, and one that does is being sampled too coarsely to drive a rotator.
It happens first near zenith, where azimuth genuinely swings fastest.
"""


def _smallest_step_deg(from_deg: float, to_deg: float) -> float:
    """The signed change from ``from_deg`` to ``to_deg``, taking the short way."""
    # A raw difference of, say, -358 and one of +2 describe the same movement.
    # Adding half a turn, wrapping, then removing it again maps every difference
    # into (-180, +180] — the range in which "shortest" is what the sign says.
    # Python's % returns a non-negative result for a positive divisor, which is
    # what makes this hold for negative differences too; the same expression in
    # C would not.
    return (to_deg - from_deg + HALF_TURN_DEG) % FULL_TURN_DEG - HALF_TURN_DEG


def unwrap_azimuth_deg(azimuths_deg: Sequence[float]) -> list[float]:
    """Rewrite a compass-azimuth series so consecutive values never jump a turn.

    Args:
        azimuths_deg: Azimuths in degrees, in time order, each in 0–360. Samples
            must be close enough together that the antenna moves less than half
            a turn between them — see :data:`AMBIGUOUS_STEP_DEG`.

    Returns:
        A series of the same length starting at the same value, in which each
        step is the angle actually travelled. Values may leave 0–360, which is
        the point: 358.5, 361.2 rather than 358.5, 1.2.

    Note:
        The first value is kept as given rather than normalised, so the series
        stays anchored to the compass reading a reader can check against a
        published prediction.

        A step of exactly :data:`AMBIGUOUS_STEP_DEG` resolves negative. It is
        not signalled as an error: the series is still returned, because a
        caller plotting a ground track is unharmed by it and only a caller
        driving a rotator is. Sample more finely rather than relying on this.
    """
    if not azimuths_deg:
        return []

    unwrapped_deg = [azimuths_deg[0]]
    for azimuth_deg in azimuths_deg[1:]:
        previous_deg = unwrapped_deg[-1]
        unwrapped_deg.append(
            previous_deg + _smallest_step_deg(previous_deg, azimuth_deg)
        )
    return unwrapped_deg
