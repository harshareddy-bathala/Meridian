"""Change against a baseline, with the interval that says how sure it is.

For each area and quantity with a rule: the mean of the series' values in the
baseline period and in the current period, their difference — absolute, or
relative to the baseline — and a percentile bootstrap interval for it, from a
seeded generator so the interval is the same every run (rule 8).

**An alert is raised only when the whole interval lies beyond the threshold**
(D-231): below a negative threshold, or above a positive one. A point estimate
past the line with an interval straddling it is reported as ``within``, with
both numbers printed — a change we cannot distinguish from noise is not one we
send anyone.

**Too few values is ``insufficient``**, never a change of zero and never an
alert: fewer than ``min_points`` in either period, or a relative rule against
a baseline mean of zero, which has no relative change to speak of.

The generator for each area and quantity is seeded from the configured seed
and a digest of the pair, so adding an area does not change another's interval.

Reference: docs/DECISIONS.md D-229, D-231.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass, replace

from meridian.regions.config import Period, RegionsConfig, Rule
from meridian.regions.series import SeriesPoint

__all__ = ["VERDICTS", "Change", "measure_change"]

VERDICTS = ("alert", "within", "insufficient")


@dataclass(frozen=True, slots=True)
class Change:
    """One area's change in one quantity, and what it amounts to."""

    area_id: int
    quantity: str
    rule: Rule
    baseline: Period
    current: Period
    baseline_n: int
    current_n: int
    baseline_mean: float | None
    current_mean: float | None
    change: float | None
    low: float | None
    high: float | None
    verdict: str
    reason: str


def measure_change(
    area_id: int,
    quantity: str,
    points: Sequence[SeriesPoint],
    config: RegionsConfig,
) -> Change | None:
    """The change for one area and quantity, or None if there is no rule or period.

    Args:
        area_id: The area.
        quantity: Which series.
        points: That series' points.
        config: The periods, the rule, the bootstrap.
    """
    rule = config.rules.get(quantity)
    if rule is None or config.baseline is None or config.current is None:
        return None
    before = _values(points, config.baseline)
    after = _values(points, config.current)
    blank = Change(
        area_id=area_id,
        quantity=quantity,
        rule=rule,
        baseline=config.baseline,
        current=config.current,
        baseline_n=len(before),
        current_n=len(after),
        baseline_mean=_mean(before),
        current_mean=_mean(after),
        change=None,
        low=None,
        high=None,
        verdict="insufficient",
        reason="",
    )
    if min(len(before), len(after)) < config.min_points:
        return replace(
            blank, reason=f"fewer than {config.min_points} values in a period"
        )
    if rule.kind == "relative" and _mean(before) == 0:
        return replace(blank, reason="a relative change from a baseline of zero")
    change = _difference(rule, before, after)
    seed = _seed(config.seed, area_id, quantity)
    low, high = _interval(rule, before, after, config, seed)
    beyond = high < rule.threshold if rule.threshold < 0 else low > rule.threshold
    return replace(
        blank,
        change=change,
        low=low,
        high=high,
        verdict="alert" if beyond else "within",
        reason=(
            f"the {config.confidence:.0%} interval lies beyond {rule.threshold:+g}"
            if beyond
            else f"the {config.confidence:.0%} interval reaches {rule.threshold:+g}"
            " or does not cross it"
        ),
    )


def _values(points: Sequence[SeriesPoint], period: Period) -> list[float]:
    return [
        one.value
        for one in points
        if one.value is not None and period.holds(one.period_from)
    ]


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _difference(rule: Rule, before: Sequence[float], after: Sequence[float]) -> float:
    base, now = sum(before) / len(before), sum(after) / len(after)
    return round((now - base) / abs(base) if rule.kind == "relative" else now - base, 6)


def _interval(
    rule: Rule,
    before: Sequence[float],
    after: Sequence[float],
    config: RegionsConfig,
    seed: int,
) -> tuple[float, float]:
    """A percentile bootstrap of the difference, periods resampled apart."""
    generator = random.Random(seed)
    draws = []
    for _ in range(config.resamples):
        a = [generator.choice(before) for _ in before]
        b = [generator.choice(after) for _ in after]
        if rule.kind == "relative" and sum(a) == 0:
            continue
        draws.append(_difference(rule, a, b))
    draws.sort()
    tail = (1.0 - config.confidence) / 2.0
    low = draws[max(0, math.floor(tail * len(draws)))]
    high = draws[min(len(draws) - 1, math.ceil((1.0 - tail) * len(draws)) - 1)]
    return low, high


def _seed(seed: int, area_id: int, quantity: str) -> int:
    digest = hashlib.sha256(f"{seed}:{area_id}:{quantity}".encode()).digest()
    return int.from_bytes(digest[:8], "big")
