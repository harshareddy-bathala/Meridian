"""How honest a model's probabilities are — ``EVALUATION.md`` §7, D-164.

Plain Python, like the scorer: a calibration is arithmetic over scored passes,
so it gives the same figures whichever numerical stack is installed, or none.

**Every model ships with three things**, and each is here:

* **the Brier score against a base rate** — the mean squared gap between the
  probability and the outcome, beside the Brier score of predicting the
  training span's decode rate for every pass. The skill is
  ``1 − brier / base_brier``: above 0 the model beats knowing only how often
  passes decode, at 0 it is no better. The base rate comes from training, so
  nothing about the reference is learned from the span it is judged on;
* **the reliability diagram** — predictions in ten equal-width bins, each with
  its count, its mean prediction and the observed decode frequency with a
  Wilson 95% interval. An empty bin is kept, with no frequency, because a
  diagram that drops its empty bins hides where the model never goes;
* **calibration by segment** — by station, band and element-set age, since an
  aggregate that looks calibrated can hide a station it is wrong about. Ages
  are listed youngest first, and ``unknown`` last in every dimension.

**The figures are unweighted:** the observed frequency of what was measured.
An inverse-propensity fit changes what the model learned, not what happened.

Reference: docs/DECISIONS.md D-161, D-164; docs/EVALUATION.md §7, §10.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from math import fsum

from meridian.datasets.weighting import Rate, wilson

__all__ = [
    "BINS",
    "DIMENSIONS",
    "UNKNOWN",
    "Bin",
    "Calibration",
    "RouteCount",
    "Scored",
    "Segment",
    "age_bucket",
    "brier",
    "calibrate",
]

BINS = 10
"""Equal-width bins of predicted probability; the last one holds 1.0."""

DIMENSIONS = ("station", "band", "element_set_age")
"""What calibration is broken down by (``EVALUATION.md`` §7)."""

UNKNOWN = "unknown"
"""A segment a pass carries no value for: an archive pass has no set age."""

_AGE_EDGES_H = ((24.0, "<24 h"), (72.0, "24–72 h"), (168.0, "72–168 h"))
_AGE_OLDEST = "≥168 h"
_AGE_ORDER = (*(name for _, name in _AGE_EDGES_H), _AGE_OLDEST)


@dataclass(frozen=True, slots=True)
class Scored:
    """One judged pass: its probability, its outcome and where it belongs."""

    probability: float
    positive: bool
    path: str
    """``configured`` or ``geometry_fallback`` (D-161)."""

    segments: Mapping[str, str]
    """Its value in each of :data:`DIMENSIONS`."""


@dataclass(frozen=True, slots=True)
class Bin:
    """One bar of the reliability diagram."""

    low: float
    high: float
    n: int
    mean_predicted: float | None
    observed: Rate | None
    """``None`` for an empty bin, which is kept."""


@dataclass(frozen=True, slots=True)
class Segment:
    """Calibration inside one station, band or age bucket."""

    dimension: str
    value: str
    n: int
    brier: float
    mean_predicted: float
    observed: Rate


@dataclass(frozen=True, slots=True)
class RouteCount:
    """How many passes one path scored, and how well (D-161)."""

    path: str
    n: int
    brier: float


@dataclass(frozen=True, slots=True)
class Calibration:
    """Everything §7 says a model ships with, for one judged span."""

    n: int
    decoded: int
    brier: float
    base_rate: float
    base_brier: float
    skill: float | None
    """``None`` only when the base rate is already perfect, which a training
    span with both outcomes cannot give."""

    bins: tuple[Bin, ...]
    segments: tuple[Segment, ...]
    routes: tuple[RouteCount, ...]


def age_bucket(hours: float | None) -> str:
    """The element-set-age bucket of a pass, ``unknown`` without an age."""
    if hours is None:
        return UNKNOWN
    for edge, name in _AGE_EDGES_H:
        if hours < edge:
            return name
    return _AGE_OLDEST


def brier(pairs: Iterable[tuple[float, bool]]) -> float:
    """The mean squared gap between probability and outcome.

    Raises:
        ValueError: No pairs, for which there is no mean.
    """
    gaps = [(probability - float(positive)) ** 2 for probability, positive in pairs]
    if not gaps:
        message = "a Brier score needs at least one scored pass"
        raise ValueError(message)
    return fsum(gaps) / len(gaps)


def calibrate(scored: Sequence[Scored], *, base_rate: float) -> Calibration:
    """The calibration of one judged span.

    Args:
        scored: Every judged pass, scored.
        base_rate: The training span's decode rate, the reference predictor.

    Returns:
        The Brier scores, the reliability diagram, the segments and the routes.

    Raises:
        ValueError: Nothing was scored, or ``base_rate`` is outside 0..1.
    """
    if not scored:
        message = "calibration needs at least one scored pass"
        raise ValueError(message)
    if not 0.0 <= base_rate <= 1.0:
        message = f"a base rate of {base_rate} is not a probability"
        raise ValueError(message)
    score = brier((one.probability, one.positive) for one in scored)
    base = brier((base_rate, one.positive) for one in scored)
    return Calibration(
        n=len(scored),
        decoded=sum(one.positive for one in scored),
        brier=score,
        base_rate=base_rate,
        base_brier=base,
        skill=None if base == 0 else 1.0 - score / base,
        bins=_bins(scored),
        segments=_segments(scored),
        routes=_routes(scored),
    )


def _bins(scored: Sequence[Scored]) -> tuple[Bin, ...]:
    held: list[list[Scored]] = [[] for _ in range(BINS)]
    for one in scored:
        held[min(int(one.probability * BINS), BINS - 1)].append(one)
    return tuple(
        Bin(
            low=index / BINS,
            high=(index + 1) / BINS,
            n=len(members),
            mean_predicted=_mean(one.probability for one in members)
            if members
            else None,
            observed=_observed(members) if members else None,
        )
        for index, members in enumerate(held)
    )


def _segments(scored: Sequence[Scored]) -> tuple[Segment, ...]:
    grouped: dict[tuple[str, str], list[Scored]] = defaultdict(list)
    for one in scored:
        for dimension in DIMENSIONS:
            grouped[(dimension, one.segments.get(dimension, UNKNOWN))].append(one)
    return tuple(
        Segment(
            dimension=dimension,
            value=value,
            n=len(members),
            brier=brier((one.probability, one.positive) for one in members),
            mean_predicted=_mean(one.probability for one in members),
            observed=_observed(members),
        )
        for (dimension, value), members in sorted(
            grouped.items(), key=lambda item: _order(*item[0])
        )
    )


def _order(dimension: str, value: str) -> tuple[int, bool, int, str]:
    """Dimensions as listed; ages youngest first; ``unknown`` last in each."""
    age = _AGE_ORDER.index(value) if value in _AGE_ORDER else 0
    return DIMENSIONS.index(dimension), value == UNKNOWN, age, value


def _routes(scored: Sequence[Scored]) -> tuple[RouteCount, ...]:
    grouped: dict[str, list[Scored]] = defaultdict(list)
    for one in scored:
        grouped[one.path].append(one)
    return tuple(
        RouteCount(
            path=path,
            n=len(members),
            brier=brier((one.probability, one.positive) for one in members),
        )
        for path, members in sorted(grouped.items())
    )


def _observed(members: Sequence[Scored]) -> Rate:
    """The decode frequency with its Wilson 95% interval."""
    return wilson(sum(one.positive for one in members) / len(members), len(members))


def _mean(values: Iterable[float]) -> float:
    held = list(values)
    return fsum(held) / len(held)
