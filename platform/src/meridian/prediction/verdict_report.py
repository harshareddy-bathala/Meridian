"""``meridian verdict evaluate``'s text: SC-7 for one verdict model.

Plain lines, in the order EVALUATION.md §11.1 lists what a verdict ships with:
- the split and what was left out;
- Brier against the base rate, for every labelled reception and for rated
  ones only;
- the reliability diagram;
- calibration by segment;
- what validation says about the partial threshold.

Every figure says it is measured, since a verdict is never fitted or judged
on a simulated reception (D-078).

Reference: docs/DECISIONS.md D-260, D-262.
"""

from __future__ import annotations

from meridian.datasets.weighting import Rate
from meridian.prediction.calibration import Bin, Calibration, Segment
from meridian.prediction.verdict_evaluation import Side, VerdictEvaluation
from meridian.prediction.verdict_examples import VerdictExamples

__all__ = ["verdict_lines"]


def verdict_lines(
    evaluation: VerdictEvaluation, found: VerdictExamples, *, method: str
) -> list[str]:
    """The report, one line per element."""
    split = evaluation.split
    lines = [
        f"reception verdict {method} — measured receptions only (D-078)",
        f"  split              train until {split.train_until.isoformat()},"
        f" validate until {split.validate_until.isoformat()},"
        f" test until {split.as_of.isoformat()}",
        f"  receptions         train {len(split.train)} · validate"
        f" {len(split.validate)} · test {len(split.test)}",
        f"  left out           {found.simulated} simulated · {found.unrated}"
        f" unrated · {found.other_rubric} under another rubric",
        "",
        "every labelled reception",
        *_scores(evaluation.every),
        "",
        "rated receptions only (D-260: no product-less reception)",
    ]
    if evaluation.rated is None:
        lines.append("  not measured: no rated reception in training or test")
    else:
        lines.extend(_scores(evaluation.rated))
    lines.extend(["", "reliability diagram, every labelled reception"])
    lines.extend(_bin(one) for one in evaluation.every.bins)
    lines.extend(["", "segments, every labelled reception"])
    lines.extend(_segment(one) for one in evaluation.every.segments)
    lines.append("  archive receptions: none; an archive reception has no rating")
    lines.extend(
        [
            "",
            f"partial threshold {evaluation.partial_below:.2f},"
            " decoded validation receptions",
            f"  below              {_side(evaluation.below)}",
            f"  at or above        {_side(evaluation.above)}",
        ]
    )
    return lines


def _scores(calibration: Calibration) -> list[str]:
    skill = "—" if calibration.skill is None else f"{calibration.skill:+.4f}"
    routes = " · ".join(
        f"{one.path} {one.n} (Brier {one.brier:.4f})" for one in calibration.routes
    )
    return [
        f"  test span          n = {calibration.n} ({calibration.decoded} usable)",
        f"  Brier              {calibration.brier:.4f}",
        f"  base rate          {calibration.base_rate:.4f} from training,"
        f" Brier {calibration.base_brier:.4f}",
        f"  skill              {skill}  (1 − Brier / base-rate Brier; SC-7 asks"
        " ≥ +0.40, proposed)",
        f"  routes             {routes}",
    ]


def _bin(one: Bin) -> str:
    edges = f"  {one.low:.1f}–{one.high:.1f}"
    if one.observed is None or one.mean_predicted is None:
        return f"{edges}  n 0     predicted —       observed —"
    return (
        f"{edges}  n {one.n:<5} predicted {one.mean_predicted:.3f}"
        f"   observed {_rate(one.observed)}"
    )


def _segment(one: Segment) -> str:
    return (
        f"  {one.dimension:<18} {one.value:<20} n {one.n:<5}"
        f" Brier {one.brier:.4f}  predicted {one.mean_predicted:.3f}"
        f"  observed {_rate(one.observed)}"
    )


def _side(side: Side) -> str:
    if side.usable is None:
        return "n 0"
    return f"n {side.n:<5} usable {_rate(side.usable)}"


def _rate(rate: Rate) -> str:
    return f"{rate.estimate:.3f} [{rate.low:.3f}, {rate.high:.3f}]"
