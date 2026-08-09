"""Evenly spaced instants across an interval, without accumulating drift.

Two ways of ending an interval, and both are needed. A series of look angles
must not include the instant at ``end`` — the next series along starts there,
and a rotator commanded twice for one moment is a bug. A scan looking for
horizon crossings must include it, because a crossing is detected *between* two
samples and the last bracket needs its far side.

Both build each instant as ``start`` plus a whole multiple of the step rather
than as the previous instant plus one more. Adding repeatedly rounds the step
once and then repeats that rounded value, so the error grows with the sample
count; over a fifteen-minute pass at a third of a second it reaches most of a
millisecond, which lands in the timestamps a rotator is driven by. Multiplying
rounds once, at the end.

:func:`aligned_to_grid` answers the third question the other two do not: *where*
the instants fall. A scan that starts wherever its caller's horizon starts gives
one pass two different answers depending on who asked, which is fine for
pointing and fatal for identity.

Arithmetic on datetimes: no propagator, no clock and no I/O.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

__all__ = [
    "GRID_ANCHOR",
    "aligned_to_grid",
    "closed_sample_times",
    "half_open_sample_times",
]


def half_open_sample_times(
    start: datetime, end: datetime, step_s: float
) -> list[datetime]:
    """Instants at ``step_s`` intervals across ``[start, end)``.

    Args:
        start: First instant, always included when the interval is non-empty.
        end: Excluded, so two adjacent intervals stitched together cannot both
            sample the moment they share.
        step_s: Interval between samples, in seconds. Must be positive; the
            caller checks that, because it has the better error message.

    Returns:
        The instants, ascending. Empty when ``end`` is at or before ``start``.
    """
    span_s = (end - start).total_seconds()
    if span_s <= 0:
        return []

    # Bound by comparison rather than by trusting `span / step` to round the way
    # the arithmetic says it should: one instant landing a float's-breadth
    # before `end` would otherwise be dropped.
    candidate_count = math.ceil(span_s / step_s)
    return [
        t
        for t in (start + timedelta(seconds=step_s * i) for i in range(candidate_count))
        if t < end
    ]


def closed_sample_times(
    start: datetime, end: datetime, step_s: float
) -> list[datetime]:
    """Instants at ``step_s`` intervals across ``[start, end]``.

    Args:
        start: First instant, always included when the interval is non-empty.
        end: Included, as the far side of the last bracket. It is appended when
            the step does not divide the span evenly, so the final gap may be
            shorter than ``step_s``.
        step_s: Interval between samples, in seconds. Must be positive; the
            caller checks that, because it has the better error message.

    Returns:
        The instants, ascending. Empty when ``end`` is at or before ``start``.
    """
    span_s = (end - start).total_seconds()
    if span_s <= 0:
        return []

    whole_steps = math.floor(span_s / step_s)
    times = [start + timedelta(seconds=step_s * i) for i in range(whole_steps + 1)]
    if times[-1] < end:
        times.append(end)
    return times


GRID_ANCHOR = datetime(2000, 1, 1, tzinfo=UTC)
"""The instant every coarse scan grid is measured from.

Any fixed instant would do; this is J2000's civil date, which is already the
reference epoch for the frames underneath. What matters is that it never moves.
"""


def aligned_to_grid(t: datetime, step_s: float) -> datetime:
    """The last grid instant at or before ``t``, counting from :data:`GRID_ANCHOR`.

    Without this the coarse scan starts wherever the caller's horizon starts, so
    the same pass sampled by two different horizons is bracketed at two
    different places and refined to two answers a few milliseconds apart. That
    is harmless for pointing and fatal for identity: a pass-generation job run
    over a rolling horizon would store the same physical pass again on every
    run, because no two acquisitions would ever compare equal.

    Aligning the scan start makes the grid a property of the step alone. Two
    searches sharing a ``coarse_step_s`` then sample the same instants wherever
    their horizons begin, and one pass has one acquisition to the microsecond.

    Args:
        t: The instant to align, timezone-aware UTC.
        step_s: Grid spacing in seconds — ``PassSearch.coarse_step_s``.

    Returns:
        A grid instant at or before ``t``, never after it, so aligning a scan
        start can only widen the interval searched and never narrow it.
    """
    elapsed_s = (t - GRID_ANCHOR).total_seconds()
    return GRID_ANCHOR + timedelta(seconds=math.floor(elapsed_s / step_s) * step_s)
