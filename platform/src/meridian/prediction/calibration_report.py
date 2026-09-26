"""``meridian model evaluate``'s text — every line §7, §8 and §10 require.

Pure: an evaluation in, lines out, so what the command prints is tested
without a terminal. Nothing is optional. Every figure is printed with the
count it is over, every observed frequency with its Wilson interval, an empty
reliability bin as a row of dashes, and a fold that could not be fitted with
the reason. The split dates, the seed and the three hashes head the report,
because a figure without them cannot be regenerated (``EVALUATION.md`` §9).

Reference: docs/DECISIONS.md D-154, D-160, D-161, D-162, D-164.
"""

from __future__ import annotations

from dataclasses import dataclass

from meridian.datasets.completeness_report import report_lines
from meridian.datasets.result import EvaluationResult
from meridian.datasets.weighting import Rate
from meridian.prediction.calibration import Bin, Calibration, Segment
from meridian.prediction.configurations import CONFIGURATIONS
from meridian.prediction.evaluation import FoldResult, ModelEvaluation
from meridian.prediction.model_config import ModelConfig

__all__ = ["Provenance", "evaluation_lines"]


@dataclass(frozen=True, slots=True)
class Provenance:
    """What a reader needs to regenerate the figures: three hashes."""

    model_sha256: str
    dataset_sha256: str
    config_sha256: str


def evaluation_lines(
    evaluation: ModelEvaluation,
    config: ModelConfig,
    provenance: Provenance,
    result: EvaluationResult,
) -> list[str]:
    """The report, top to bottom.

    Args:
        evaluation: The test span's calibration and the folds.
        config: The configuration the model was fitted under.
        provenance: The model's, dataset's and configuration's hashes.
        result: The dataset's completeness for the model's population (D-154).

    Returns:
        The lines to print, without newlines.
    """
    return [
        *_header(evaluation, config, provenance),
        *_examples(evaluation, config),
        *_scores(evaluation.calibration),
        "reliability (predicted probability in tenths)",
        *(_bin(one) for one in evaluation.calibration.bins),
        "calibration by segment",
        *(_segment(one) for one in evaluation.calibration.segments),
        *_folds(evaluation, config),
        "completeness of the dataset",
        *report_lines([result]),
    ]


def _header(
    evaluation: ModelEvaluation, config: ModelConfig, provenance: Provenance
) -> list[str]:
    configuration = CONFIGURATIONS[config.configuration]
    split = evaluation.split
    lines = [
        f"model {provenance.model_sha256}",
        f"  dataset            {provenance.dataset_sha256}",
        f"  configuration file {provenance.config_sha256}",
        f"  configuration      {configuration.name}: {configuration.question}",
        f"  population         {config.population}",
        f"  train until        {split.train_until.isoformat()}",
        f"  validate until     {split.validate_until.isoformat()}",
        f"  test until         {split.as_of.isoformat()} (the dataset's as_of)",
        f"  seed               {config.seed}",
        f"  regularisation     C = {config.inverse_regularisation:g}, stated",
    ]
    if configuration.weighted_by_priority:
        lines.append(
            "  B's probabilities are A's; priority weights the objective (D-160)"
        )
    return lines


def _examples(evaluation: ModelEvaluation, config: ModelConfig) -> list[str]:
    split = evaluation.split
    spans = " · ".join(
        f"{name} {len(span)} ({sum(one.positive for one in span)} decoded)"
        for name, span in (
            ("train", split.train),
            ("validate", split.validate),
            ("test", split.test),
        )
    )
    lines = [
        f"  examples           {spans}",
        f"  simulated          {evaluation.simulated} left out (D-078)",
    ]
    if config.weighting == "ipw":
        lines.append(
            f"  weighting          ipw in the fit; {evaluation.without_weight}"
            " examples without a weight left out; figures below are unweighted"
        )
    else:
        lines.append("  weighting          none")
    return lines


def _scores(calibration: Calibration) -> list[str]:
    skill = "—" if calibration.skill is None else f"{calibration.skill:+.4f}"
    routes = " · ".join(
        f"{one.path} {one.n} (Brier {one.brier:.4f})" for one in calibration.routes
    )
    return [
        f"test span, n = {calibration.n} ({calibration.decoded} decoded)",
        f"  Brier              {calibration.brier:.4f}",
        f"  base rate          {calibration.base_rate:.4f} from training,"
        f" Brier {calibration.base_brier:.4f}",
        f"  skill              {skill}  (1 − Brier / base-rate Brier)",
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
        f"  {one.dimension:<16} {one.value:<20} n {one.n:<5}"
        f" Brier {one.brier:.4f}  predicted {one.mean_predicted:.3f}"
        f"  observed {_rate(one.observed)}"
    )


def _folds(evaluation: ModelEvaluation, config: ModelConfig) -> list[str]:
    if config.folds == 0:
        return ["rolling-origin folds: not run (folds = 0)"]
    lines = [f"rolling-origin folds ({config.folds} asked, inside the pre-test span)"]
    lines.extend(_fold(one) for one in evaluation.folds)
    if not evaluation.folds:
        lines.append("  none: the span before validate_until has one instant")
    spread = evaluation.fold_brier
    if spread is None:
        lines.append("  Brier across folds —: no fold could be fitted")
    else:
        mean, deviation = spread
        shown = "—" if deviation is None else f"{deviation:.4f}"
        lines.append(f"  Brier across folds {mean:.4f} ± {shown} (sample s.d.)")
    return lines


def _fold(one: FoldResult) -> str:
    dates = (
        f"  {one.train_until.date()} / {one.validate_until.date()} /"
        f" {one.as_of.date()}  n {one.n:<5}"
    )
    if one.refused is not None or one.brier is None or one.base_brier is None:
        return f"{dates} not fitted: {one.refused}"
    return f"{dates} Brier {one.brier:.4f}  base {one.base_brier:.4f}"


def _rate(rate: Rate) -> str:
    return f"{rate.estimate:.3f} [{rate.low:.3f}, {rate.high:.3f}]"
