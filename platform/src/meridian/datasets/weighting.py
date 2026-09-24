"""Inverse-propensity weights and what every weighted result must say — D-153.

``EVALUATION.md`` §4.2 weights each scored outcome by the inverse of the
probability that the historical policy attempted its pass, so the passes it
tended to skip count for more. That estimate is only as good as its weights,
so this module reports the weights along with the estimate:

* **the floor** — a propensity below ``floor`` is weighted as if it were
  ``floor``, so no weight exceeds ``1 / floor``, and the count floored is
  reported. A floor pulls the estimate towards the unweighted one; it is
  preferred to an unbounded weight because it fails visibly;
* **support** — an available pass in a cell nothing was ever attempted from
  has propensity 0, and no weight can stand for it. It is counted as
  ``unsupported``, never absorbed;
* **certainty** — a pass in a cell where every pass was attempted has
  propensity 1: there is no counterfactual to weight towards. A deterministic
  policy puts most passes at 0 or 1, and this count is how that shows (D-152);
* **the rates** — the unweighted success rate with a Wilson 95% interval, and
  the Hájek estimate (weights normalised to sum to one) with a Wilson interval
  taken at n = ESS;
* **the effective sample size**, (Σw)² ÷ Σw², and ``unreliable`` when it falls
  below max(30, 0.1 × n) — §4.2's "labelled as such rather than quoted", in
  the data where no report can drop it. At the default floor every weight is
  within 1..20, which holds ESS above about 0.18 n, so there only the 30
  decides; the tenth of n can bind only under a floor below about 0.026;
* **the distributions** — the weights' quartiles, and propensity in tenths for
  attempted and not-attempted passes apart, which is the overlap check.

Sums are ``math.fsum``, which is correctly rounded, so the result does not
depend on the order passes arrive in.

Reference: docs/DECISIONS.md D-152, D-153.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import ceil, fsum, sqrt

from meridian.datasets.propensity import Estimate

__all__ = [
    "IpwDiagnostics",
    "Rate",
    "Scored",
    "weigh",
    "weight_of",
]

Z_95 = 1.959963984540054
"""The two-sided 95% normal quantile."""

MIN_ESS = 30
ESS_FRACTION = 0.1
_BINS = 10
_QUARTILES = (0, 1, 2, 3, 4)


@dataclass(frozen=True, slots=True)
class Scored:
    """An eligible pass's estimate, and — if it was attempted — how it went."""

    estimate: Estimate
    usable: bool
    """Attempted, with an outcome a yield figure can score."""

    success: bool
    """Usable and successful. Never read by the propensity (D-152)."""


@dataclass(frozen=True, slots=True)
class Rate:
    """A success rate with its Wilson 95% interval, over ``n`` (possibly ESS)."""

    estimate: float
    low: float
    high: float
    n: float


@dataclass(frozen=True, slots=True)
class IpwDiagnostics:
    """Everything D-153 says a weighted result carries."""

    model: str
    floor: float
    available: int
    """Eligible passes the weights were estimated over."""

    weighted: int
    """Passes that carry a weight: attempted, usable, and supported."""

    unsupported: int
    certain: int
    floored: int
    unweighted: Rate | None
    weighted_rate: Rate | None
    ess: float
    unreliable: bool
    weight_quartiles: tuple[float, ...]
    """Min, lower quartile, median, upper quartile, max; empty with no weights."""

    overlap_attempted: tuple[int, ...]
    overlap_not_attempted: tuple[int, ...]

    def parameters(self) -> dict[str, object]:
        """The diagnostics as plain values, for a manifest or a report."""
        return {
            "model": self.model,
            "floor": self.floor,
            "available": self.available,
            "weighted": self.weighted,
            "unsupported": self.unsupported,
            "certain": self.certain,
            "floored": self.floored,
            "unweighted": _rate(self.unweighted),
            "weighted_rate": _rate(self.weighted_rate),
            "ess": self.ess,
            "unreliable": self.unreliable,
            "weight_quartiles": list(self.weight_quartiles),
            "overlap": {
                "attempted": list(self.overlap_attempted),
                "not_attempted": list(self.overlap_not_attempted),
            },
        }


def weight_of(estimate: Estimate, floor: float) -> float | None:
    """The weight an attempted pass carries, or None where it has no support."""
    if estimate.attempted == 0:
        return None
    return 1 / max(estimate.propensity, floor)


def weigh(scored: Sequence[Scored], *, model: str, floor: float) -> IpwDiagnostics:
    """Weight one population's scored passes and describe the weights.

    Args:
        scored: Every eligible pass of one population, with its estimate.
        model: The propensity model's name, recorded with the result.
        floor: The least propensity a weight is computed from.

    Returns:
        The weighted and unweighted rates and every diagnostic D-153 names.
    """
    units = [
        (weight, one.success)
        for one in scored
        if one.usable
        and one.estimate.candidate.attempted
        and (weight := weight_of(one.estimate, floor)) is not None
    ]
    weights = [weight for weight, _ in units]
    total = fsum(weights)
    ess = total * total / fsum(w * w for w in weights) if weights else 0.0
    successes = sum(1 for _, success in units if success)
    return IpwDiagnostics(
        model=model,
        floor=floor,
        available=len(scored),
        weighted=len(units),
        unsupported=sum(1 for one in scored if one.estimate.attempted == 0),
        certain=sum(
            1 for one in scored if one.estimate.attempted == one.estimate.available
        ),
        floored=sum(
            1
            for one in scored
            if one.usable
            and one.estimate.candidate.attempted
            and one.estimate.propensity < floor
        ),
        unweighted=_wilson(successes / len(units), len(units)) if units else None,
        weighted_rate=(
            _wilson(fsum(w for w, success in units if success) / total, ess)
            if units
            else None
        ),
        ess=ess,
        unreliable=ess < max(MIN_ESS, ESS_FRACTION * len(units)),
        weight_quartiles=_quartiles(weights),
        overlap_attempted=_overlap(scored, attempted=True),
        overlap_not_attempted=_overlap(scored, attempted=False),
    )


def _wilson(p: float, n: float) -> Rate:
    """The Wilson score interval, which stays inside 0..1 at small n."""
    z2 = Z_95 * Z_95
    denominator = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denominator
    half = Z_95 * sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denominator
    return Rate(
        estimate=p, low=max(0.0, centre - half), high=min(1.0, centre + half), n=n
    )


def _quartiles(weights: Sequence[float]) -> tuple[float, ...]:
    """Nearest-rank quartiles, min and max included."""
    if not weights:
        return ()
    ordered = sorted(weights)
    last = len(ordered) - 1
    return tuple(ordered[ceil(q * last / 4)] for q in _QUARTILES)


def _overlap(scored: Sequence[Scored], *, attempted: bool) -> tuple[int, ...]:
    """Propensity in tenths, for passes the policy did or did not attempt.

    Binned by integer arithmetic on the cell's counts, so a propensity of
    exactly 0.7 is in the 0.7 bin.
    """
    histogram = [0] * _BINS
    for one in scored:
        if one.estimate.candidate.attempted is attempted:
            bin_index = one.estimate.attempted * _BINS // one.estimate.available
            histogram[min(bin_index, _BINS - 1)] += 1
    return tuple(histogram)


def _rate(rate: Rate | None) -> dict[str, float] | None:
    if rate is None:
        return None
    return {"estimate": rate.estimate, "low": rate.low, "high": rate.high, "n": rate.n}
