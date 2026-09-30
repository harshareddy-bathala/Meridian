"""Intervals for clustered data: resample whole station-days, never single passes.

Passes are not independent. Two passes at one station on one day share its
weather, its interference and its operator's attention, so a bootstrap that
drew passes one at a time would treat a day of correlated misses as many
independent pieces of evidence and give an interval that is too narrow. A
resample here draws **station-days** with replacement, keeps every pass of each
drawn day together, and computes the statistic over them.

**Two statistics read from one draw are paired.** Every model in a report is
judged on the same test passes, and each comparison reads both models on the
same resampled days, so the variation they share cancels, as the scheduler's
paired gain does (D-172).

The draws come from ``random.Random(seed)`` and the bounds are percentiles by
nearest rank, read by the same function the scheduler comparison uses, so one
seed gives one interval on every machine. A draw on which the statistic is
undefined — a skill against a base rate every drawn pass happened to match —
is dropped and counted, never replaced.

Reference: docs/DECISIONS.md D-172, D-237.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass
from typing import TypeVar

from meridian.scheduler.comparison import CONFIDENCE, Interval, percentile_interval

__all__ = ["CONFIDENCE", "Bootstrapped", "cluster_bootstrap", "clustered"]

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Bootstrapped:
    """A percentile interval, and how many of its draws counted."""

    interval: Interval | None
    """``None`` when every draw was undefined."""

    resamples: int
    dropped: int


def clustered(items: Sequence[T], key: Callable[[T], Hashable]) -> list[list[T]]:
    """``items`` grouped by ``key``, groups in first-seen order, order kept."""
    groups: dict[Hashable, list[T]] = {}
    for one in items:
        groups.setdefault(key(one), []).append(one)
    return list(groups.values())


def cluster_bootstrap(
    clusters: Sequence[Sequence[T]],
    statistics: Mapping[str, Callable[[Sequence[T]], float | None]],
    *,
    seed: int,
    resamples: int,
) -> dict[str, Bootstrapped]:
    """Each statistic's interval, every one read on the same resampled clusters.

    Args:
        clusters: The groups to resample, each kept whole.
        statistics: Named functions of the pooled items of one draw. A
            statistic returns ``None`` where it is undefined for a draw.
        seed: The resampling seed.
        resamples: How many draws.

    Returns:
        Each statistic's interval and its dropped draws, by name.

    Raises:
        ValueError: No clusters to draw from.
    """
    if not clusters:
        message = "a bootstrap needs at least one cluster to draw"
        raise ValueError(message)
    rng = random.Random(seed)
    values: dict[str, list[float]] = {name: [] for name in statistics}
    for _ in range(resamples):
        drawn = [clusters[rng.randrange(len(clusters))] for _ in clusters]
        pooled = [item for cluster in drawn for item in cluster]
        for name, statistic in statistics.items():
            value = statistic(pooled)
            if value is not None:
                values[name].append(value)
    return {
        name: Bootstrapped(
            interval=percentile_interval(held) if held else None,
            resamples=resamples,
            dropped=resamples - len(held),
        )
        for name, held in values.items()
    }
