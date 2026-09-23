"""``meridian snapshot completeness``'s text — every line D-151 and D-153 require.

Pure: results in, lines out, so what the command prints is tested without a
terminal. Nothing is optional. A population with no station-days still prints
its zero counts, and an unreliable weighted rate is printed with the word
``UNRELIABLE`` beside it rather than left out (``EVALUATION.md`` §4.2).

Reference: docs/DECISIONS.md D-151, D-153, D-154.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from meridian.datasets.completeness import STATUSES, CompletenessSummary
from meridian.datasets.result import EvaluationResult, NotWeighted
from meridian.datasets.weighting import IpwDiagnostics, Rate

__all__ = ["report_lines"]

_TITLES = {"own": "our stations", "archive": "archive stations"}


def report_lines(results: Iterable[EvaluationResult]) -> list[str]:
    """The report, one population after another.

    Args:
        results: Each population's result, as :func:`read_results` gave it.

    Returns:
        The lines to print, without newlines.
    """
    lines: list[str] = []
    for result in results:
        lines.append(f"{_TITLES.get(result.population, result.population)}")
        lines.extend(_completeness(result.completeness))
        lines.extend(_weighting(result.weighting))
    return lines


def _completeness(summary: CompletenessSummary) -> list[str]:
    days = " · ".join(f"{name} {summary.statuses[name]}" for name in STATUSES)
    distribution = summary.distribution
    deciles = _numbers(distribution.deciles) if distribution.deciles else "—"
    sensitivity = " · ".join(
        f"{threshold:g}: {kept} kept, {dropped} out"
        for threshold, kept, dropped in summary.sensitivity
    )
    return [
        f"  threshold          {summary.threshold:g}",
        f"  station-days       {days}",
        f"  passes             eligible {summary.eligible} · attempted "
        f"{summary.attempted} · usable {summary.usable}",
        f"  deciles            {deciles}  (over {distribution.count} days)",
        f"  histogram (tenths) {_counts(distribution.histogram)}",
        f"  sensitivity        {sensitivity}",
    ]


def _weighting(weighting: IpwDiagnostics | NotWeighted) -> list[str]:
    if isinstance(weighting, NotWeighted):
        return [f"  weighting          not weighted: {weighting.reason}"]
    flag = "  UNRELIABLE" if weighting.unreliable else ""
    quartiles = (
        _numbers(weighting.weight_quartiles) if weighting.weight_quartiles else "—"
    )
    return [
        f"  weighting          {weighting.model}, floor {weighting.floor:g}",
        f"  unweighted rate    {_rate(weighting.unweighted)}",
        f"  weighted rate      {_rate(weighting.weighted_rate)}{flag}",
        f"  effective n        {weighting.ess:.1f} of {weighting.weighted} weighted",
        f"  support            available {weighting.available} · unsupported "
        f"{weighting.unsupported} · certain {weighting.certain} · floored "
        f"{weighting.floored}",
        f"  weights            {quartiles}  (min, quartiles, max)",
        f"  overlap attempted  {_counts(weighting.overlap_attempted)}",
        f"  overlap not        {_counts(weighting.overlap_not_attempted)}",
    ]


def _rate(rate: Rate | None) -> str:
    if rate is None:
        return "— (nothing to score)"
    return f"{rate.estimate:.3f} [{rate.low:.3f}, {rate.high:.3f}] at n {rate.n:.1f}"


def _numbers(values: Sequence[float]) -> str:
    return " ".join(f"{one:.2f}" for one in values)


def _counts(values: Sequence[int]) -> str:
    return " ".join(str(one) for one in values)
