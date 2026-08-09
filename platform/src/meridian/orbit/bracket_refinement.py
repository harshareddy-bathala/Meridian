"""Narrowing a bracketed interval to the instant a curve crosses zero or peaks.

A coarse scan can only report that something happened *between* two samples.
These two functions turn that into an instant: bisection for a crossing,
ternary search for a maximum. Both take a curve as a function of time and
return a time, and neither knows what the curve measures.

``pass_search`` supplies elevation, and uses these to turn "the satellite was
below the horizon at 10:14:00 and above it at 10:14:30" into an acquisition
time. Because nothing here refers to a satellite, both functions can be checked
against curves whose answers are known in closed form — a straight line, a
parabola, a sine — with no element set and no propagator involved.

No orbital mathematics: these are the textbook root-finding and unimodal-search
methods, and all the geometry lives in the callable they are handed.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

__all__ = ["ValueAt", "refine_maximum", "refine_zero_crossing"]

ValueAt = Callable[[datetime], float]
"""A curve sampled by time: one number for one instant."""


def refine_zero_crossing(
    f: ValueAt, t_below: datetime, t_above: datetime, tolerance_s: float
) -> datetime:
    """Bisect toward the instant at which ``f`` reaches zero.

    Args:
        f: The curve. A caller with a threshold rather than a zero subtracts it
            first — for a horizon crossing, ``elevation_deg(t) - floor_deg``.
        t_below: An instant at which ``f`` is negative.
        t_above: An instant at which ``f`` is zero or positive.
        tolerance_s: Stop once the two are this close, in seconds.

    Returns:
        The ``t_above`` end of the final bracket, so the instant returned is one
        at which ``f`` is known non-negative. For a pass that means the span
        between an acquisition and a loss is time the satellite spent above the
        floor, never time straddling the crossing.

    Raises:
        ValueError: ``tolerance_s`` is not positive, which would loop until the
            bracket collapsed to a microsecond and the midpoint stopped moving.

    Note:
        ``t_below`` may fall either side of ``t_above`` in time. Acquisition
        brackets run earlier-to-later and loss brackets later-to-earlier;
        bisection cares which endpoint is which, not which came first.

        The bracket is trusted rather than verified. Checking it would cost two
        evaluations of ``f`` — the expensive part, since ``f`` is a propagator
        in the only caller — to re-establish what the scan that produced the
        bracket already knew.
    """
    if tolerance_s <= 0:
        raise ValueError(f"tolerance_s must be positive, got {tolerance_s}")

    while abs((t_above - t_below).total_seconds()) > tolerance_s:
        midpoint = t_below + (t_above - t_below) / 2
        if f(midpoint) >= 0:
            t_above = midpoint
        else:
            t_below = midpoint
    return t_above


def refine_maximum(
    f: ValueAt, t_earlier: datetime, t_later: datetime, tolerance_s: float
) -> datetime:
    """Ternary-search for the instant at which ``f`` is highest.

    Args:
        f: The curve, single-peaked across the bracket. With two peaks inside
            it the search converges to one of them without saying which.
        t_earlier: Start of the bracket.
        t_later: End of the bracket, at or after ``t_earlier``.
        tolerance_s: Stop once the bracket is this narrow, in seconds.

    Returns:
        The midpoint of the final bracket. The value there is not returned —
        a caller that wants it evaluates ``f`` once more, which costs one call
        against the tens this search already made.

    Raises:
        ValueError: ``t_later`` precedes ``t_earlier``, or ``tolerance_s`` is
            not positive.

    Note:
        Each step evaluates ``f`` a third of the way in from each end and
        discards the outer third on the losing side, so the bracket shrinks by
        a third per step whichever way the comparison lands. That matters near
        a peak, where the curve is flat and the comparison is decided by
        floating-point noise rather than by the shape: convergence is
        guaranteed by the arithmetic, and what it converges to is the peak to
        within its own flatness.

        Ternary rather than golden-section, which would reuse one evaluation
        per step. The two extra calls buy a search a reader can follow without
        first being told where the ratio comes from.
    """
    if t_later < t_earlier:
        raise ValueError("t_later precedes t_earlier")
    if tolerance_s <= 0:
        raise ValueError(f"tolerance_s must be positive, got {tolerance_s}")

    while (t_later - t_earlier).total_seconds() > tolerance_s:
        one_third = (t_later - t_earlier) / 3
        if f(t_earlier + one_third) < f(t_later - one_third):
            t_earlier = t_earlier + one_third
        else:
            t_later = t_later - one_third
    return t_earlier + (t_later - t_earlier) / 2
