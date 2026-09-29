"""The comparison's numbers: frames per station-hour, and a gain with its interval.

SC-1 is decoded frames per station-hour (``EVALUATION.md`` §1), measured as
**D − B** (§3). Every scheduler is judged on the same retained station-days,
so the station-hours are the same for all of them and a difference in the
rate is a difference in frames.

**Frames are counted where they are known.** A pass a schedule takes that
nobody attempted has no outcome; it adds nothing to the frames and one to the
schedule's unknown count, which is reported beside the rate. A scheduler that
leaves the historical policy's passes for unattempted ones is therefore
judged low by exactly the share it reports, and the completeness threshold is
what keeps that share small (D-151).

**The interval is a paired bootstrap over station-days.** A resample draws
station-days with replacement, and both schedulers are read on the same draw,
so the day-to-day variation they share cancels. Resampling uses the
configuration's seed, and the interval's bounds are percentiles by nearest
rank, so one seed gives one interval on every machine.

A leaf: no I/O and no clock.

Reference: docs/DECISIONS.md D-151, D-172; docs/EVALUATION.md §1, §3.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from meridian.scheduler.replay import DayResult

__all__ = [
    "CONFIDENCE",
    "Gain",
    "Interval",
    "Totals",
    "paired_gain",
    "totals",
]

CONFIDENCE = 0.95


@dataclass(frozen=True, slots=True)
class Totals:
    """One scheduler over every replayed station-day."""

    selected: int
    frames: int
    unknown: int
    statuses: tuple[tuple[str, int], ...]
    """How each day's schedule was found, and on how many days, by name."""

    def per_hour(self, hours: float) -> float:
        """Frames per station-hour."""
        return self.frames / hours if hours > 0 else 0.0

    @property
    def unknown_share(self) -> float | None:
        """The share of passes taken whose outcome nobody knows."""
        return self.unknown / self.selected if self.selected else None


@dataclass(frozen=True, slots=True)
class Interval:
    """A two-sided percentile interval."""

    low: float
    high: float


@dataclass(frozen=True, slots=True)
class Gain:
    """How far one scheduler is ahead of another, with its uncertainty."""

    per_hour: float
    """Frames per station-hour, the first minus the second."""

    relative: float | None
    """The difference over the second's frames; ``None`` where it has none."""

    per_hour_interval: Interval
    relative_interval: Interval | None
    """``None`` where some resample gives the second scheduler no frames."""

    resamples: int


def totals(days: Sequence[DayResult]) -> Totals:
    """Sum one scheduler's days."""
    statuses = Counter(one.status for one in days)
    return Totals(
        selected=sum(len(one.selected) for one in days),
        frames=sum(one.frames for one in days),
        unknown=sum(one.unknown for one in days),
        statuses=tuple(sorted(statuses.items())),
    )


def paired_gain(
    first: Sequence[DayResult],
    second: Sequence[DayResult],
    hours: Sequence[float],
    *,
    seed: int,
    resamples: int,
) -> Gain:
    """``first`` minus ``second`` on the same station-days, with intervals.

    Args:
        first: One scheduler's days.
        second: Another's, aligned with ``first``.
        hours: Each day's station-hours, aligned with both.
        seed: The resampling seed.
        resamples: How many bootstrap resamples to draw.

    Raises:
        ValueError: The three are not aligned, or there are no days.
    """
    if not len(first) == len(second) == len(hours) or not hours:
        message = "a paired gain needs the same station-days, and at least one"
        raise ValueError(message)
    days = [
        (one.frames, other.frames, spent)
        for one, other, spent in zip(first, second, hours, strict=True)
    ]
    rng = random.Random(seed)
    per_hour, relative = [], []
    for _ in range(resamples):
        drawn = [days[rng.randrange(len(days))] for _ in days]
        difference, base, spent = _sums(drawn)
        per_hour.append(difference / spent)
        relative.append(difference / base if base else None)
    difference, base, spent = _sums(days)
    return Gain(
        per_hour=difference / spent,
        relative=difference / base if base else None,
        per_hour_interval=_interval(per_hour),
        relative_interval=(
            None
            if any(one is None for one in relative)
            else _interval([one for one in relative if one is not None])
        ),
        resamples=resamples,
    )


def _sums(days: Sequence[tuple[int, int, float]]) -> tuple[int, int, float]:
    """The frames gained, the second scheduler's frames, and the hours."""
    return (
        sum(one - other for one, other, _ in days),
        sum(other for _, other, _ in days),
        math.fsum(spent for _, _, spent in days),
    )


def _interval(values: Sequence[float]) -> Interval:
    """The central :data:`CONFIDENCE` of ``values``, by nearest rank."""
    ordered = sorted(values)
    tail = (1.0 - CONFIDENCE) / 2.0
    return Interval(low=_rank(ordered, tail), high=_rank(ordered, 1.0 - tail))


def _rank(ordered: Sequence[float], share: float) -> float:
    index = max(math.ceil(share * len(ordered)) - 1, 0)
    return ordered[min(index, len(ordered) - 1)]
