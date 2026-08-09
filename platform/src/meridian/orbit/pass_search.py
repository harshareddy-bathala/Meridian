"""Finding passes in an elevation curve, without knowing what a satellite is.

A pass is an interval during which a satellite stays above an elevation floor.
This module finds those intervals given only a function returning elevation at
an instant: it steps coarsely to bracket each horizon crossing, then hands the
bracket to ``bracket_refinement`` to narrow it to a time, and searches
separately for the culmination.

Nothing here propagates an orbit — the callable it is handed does that. So the
search can be checked against an elevation curve whose crossings are known in
closed form, with no element set and no propagator involved.
``meridian.orbit.skyfield_service`` supplies the real curve and turns each
result into a :class:`~meridian.orbit.types.PassWindow` carrying the satellite
and element set it came from.

Reference: docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md Stage 6;
``meridian.orbit.service.OrbitService.pass_windows``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from meridian.orbit.bracket_refinement import (
    ValueAt,
    refine_maximum,
    refine_zero_crossing,
)
from meridian.orbit.types import require_utc

__all__ = [
    "REFINEMENT_TOLERANCE_S",
    "ElevationAt",
    "ElevationPass",
    "ElevationScan",
    "find_elevation_passes",
]

ElevationAt = ValueAt
"""A curve whose value is elevation above the horizon, in degrees."""

REFINEMENT_TOLERANCE_S = 0.05
"""How precisely a boundary or a culmination is located before refining stops.

Set from what consumes the answer, not from what the arithmetic could reach.
Pass boundaries reach stations through MSP §4.3 alongside a timing uncertainty
measured in seconds, and a station widens its recording window by that figure —
so resolving a boundary far inside it buys nothing anyone can act on. Near the
horizon a low Earth orbit pass changes elevation by well under a degree per
second, which puts 0.05 s below 0.05° of pointing error: an order of magnitude
inside the accuracy of any rotator this project will drive.
"""


SAMPLES_NEEDED_TO_BRACKET = 2
"""Fewer samples than this and there is nothing to bracket.

A crossing is detected *between* two instants, so a single sample cannot show
one. This is reached when the scan interval is empty or backwards, never for a
real interval, because :func:`_coarse_sample_times` always includes both ends.
"""


@dataclass(frozen=True, slots=True)
class ElevationScan:
    """Where in time to look for passes, and how finely.

    The part of :class:`~meridian.orbit.types.PassSearch` the search actually
    reads. Which satellite and which site have already been absorbed into the
    elevation callable by the time they get here, which is what lets this be
    tested without either.
    """

    start: datetime
    end: datetime

    min_elevation_deg: float = 0.0
    """The elevation floor. A pass is the interval spent at or above it, so the
    floor is part of the answer and is copied onto every pass found — two
    searches run at different floors describe different populations and must
    never be pooled."""

    coarse_step_s: float = 30.0
    """Interval between the samples that bracket a crossing.

    A correctness parameter: a pass that begins and ends between two samples is
    not found late, it is not found at all. The default matches
    :class:`~meridian.orbit.types.PassSearch`, where the reasoning behind 30 s
    is recorded."""


@dataclass(frozen=True, slots=True)
class ElevationPass:
    """One interval spent above the floor, with its culmination."""

    aos: datetime
    los: datetime
    max_elevation_deg: float
    max_elevation_at: datetime
    min_elevation_deg: float
    """The floor this pass was found against, carried so it stays interpretable
    away from the scan that produced it."""

    is_truncated: bool
    """True when a boundary is the edge of the scan rather than a crossing.

    The satellite was already above the floor at ``scan.start``, or still above
    it at ``scan.end``. A caller counting passes for a completeness denominator
    should widen the scan or drop these, because the adjacent scan reports the
    same pass truncated at the other end and the two would both be counted."""


@dataclass(frozen=True, slots=True)
class _CoarseSamples:
    """The coarse pass over the interval: the instants, and elevation at each."""

    times: list[datetime]
    elevations_deg: list[float]


def find_elevation_passes(
    elevation_at: ElevationAt, scan: ElevationScan
) -> list[ElevationPass]:
    """Every interval in ``scan`` spent at or above the elevation floor.

    Args:
        elevation_at: Elevation in degrees at an instant. Called once per
            coarse step, plus roughly twenty per boundary and forty per
            culmination, so an expensive implementation should expect that
            cost rather than be surprised by it.
        scan: The interval, the floor and the coarse step.

    Returns:
        Passes in ascending order of acquisition.

    Raises:
        ValueError: ``scan.coarse_step_s`` is not positive, or either bound is
            naive or not UTC.
    """
    if scan.coarse_step_s <= 0:
        raise ValueError(f"coarse_step_s must be positive, got {scan.coarse_step_s}")

    times = _coarse_sample_times(
        require_utc(scan.start, "start"),
        require_utc(scan.end, "end"),
        scan.coarse_step_s,
    )
    if len(times) < SAMPLES_NEEDED_TO_BRACKET:
        return []

    samples = _CoarseSamples(times, [elevation_at(t) for t in times])
    above = [
        elevation_deg >= scan.min_elevation_deg
        for elevation_deg in samples.elevations_deg
    ]
    return [
        _refine_pass(elevation_at, scan, samples, span) for span in _above_spans(above)
    ]


def _coarse_sample_times(
    start: datetime, end: datetime, step_s: float
) -> list[datetime]:
    """Instants across the closed interval ``[start, end]``.

    Closed, unlike the half-open windows this project uses elsewhere: a crossing
    is detected *between* two samples, so the instant at ``end`` is needed as
    the far side of the last bracket rather than as a sample in its own right.

    Each instant is ``start`` plus a whole multiple of the step rather than the
    previous plus one more, so the sample times do not drift across a long scan.
    """
    span_s = (end - start).total_seconds()
    if span_s <= 0:
        return []

    whole_steps = math.floor(span_s / step_s)
    times = [start + timedelta(seconds=step_s * i) for i in range(whole_steps + 1)]
    if times[-1] < end:
        times.append(end)
    return times


def _above_spans(above: list[bool]) -> list[tuple[int, int]]:
    """Runs of consecutive above-floor samples, as (first index, last index)."""
    spans: list[tuple[int, int]] = []
    first: int | None = None
    for index, is_above in enumerate(above):
        if is_above and first is None:
            first = index
        elif not is_above and first is not None:
            spans.append((first, index - 1))
            first = None
    if first is not None:
        spans.append((first, len(above) - 1))
    return spans


def _refine_pass(
    elevation_at: ElevationAt,
    scan: ElevationScan,
    samples: _CoarseSamples,
    span: tuple[int, int],
) -> ElevationPass:
    """Turn one run of above-floor samples into a pass with refined boundaries."""

    def height_above_floor(t: datetime) -> float:
        """Zero exactly at the floor, so a boundary is a zero crossing."""
        return elevation_at(t) - scan.min_elevation_deg

    first, last = span
    times = samples.times
    at_scan_start, at_scan_end = first == 0, last == len(times) - 1

    aos = (
        times[0]
        if at_scan_start
        else refine_zero_crossing(
            height_above_floor, times[first - 1], times[first], REFINEMENT_TOLERANCE_S
        )
    )
    los = (
        times[-1]
        if at_scan_end
        else refine_zero_crossing(
            height_above_floor, times[last + 1], times[last], REFINEMENT_TOLERANCE_S
        )
    )

    # Bracket the culmination by the neighbours of the highest coarse sample, so
    # the only thing assumed single-peaked is that minute or so of curve rather
    # than the whole pass.
    peak = max(range(first, last + 1), key=lambda i: samples.elevations_deg[i])
    culmination = refine_maximum(
        elevation_at,
        times[max(peak - 1, 0)],
        times[min(peak + 1, len(times) - 1)],
        REFINEMENT_TOLERANCE_S,
    )
    return ElevationPass(
        aos=aos,
        los=los,
        max_elevation_deg=elevation_at(culmination),
        max_elevation_at=culmination,
        min_elevation_deg=scan.min_elevation_deg,
        is_truncated=at_scan_start or at_scan_end,
    )
