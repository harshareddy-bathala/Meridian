"""The reception-verdict section: SC-7, from the raw snapshot the run reads.

``EVALUATION.md`` §11.1 asks the verdict to ship with a Brier score against
the base rate, a reliability diagram and calibration by segment. The section
fits the verdict from the run's raw snapshot under ``[verdict]``, with the
fit's seed derived from the master seed (D-236). It publishes the model under
the datasets root, as the prediction section publishes its models, and judges
the test span (D-262):
- **every labelled reception**, against the training span's usable rate;
- **rated receptions only**, without the ones whose label follows from having
  no product (D-260). SC-7 is read from this one, ≥ 40% skill, proposed.

Each skill carries a station-day bootstrap interval, as SC-2's does
(:mod:`meridian.reports.bootstrap`, D-237).

**Not measured is a result.** A snapshot with too few rated receptions, no
split dates, or no product manifest gives one ``verdict`` row with
``status = not_measured`` and the reason. It never gives a number from too
little. That is SC-7's state until station 001's receptions are rated (D-260).

Reference: docs/DECISIONS.md D-236, D-237, D-260, D-262, D-264.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from math import fsum

from meridian.datasets.manifest import content_sha256
from meridian.datasets.publish import SnapshotDirectory
from meridian.datasets.row_fields import MalformedSnapshotError
from meridian.datasets.usable_labels import read_usable_labels
from meridian.datasets.weighting import Rate
from meridian.prediction.calibration import Calibration
from meridian.prediction.splits import SplitError
from meridian.prediction.verdict_evaluation import (
    VerdictEvaluation,
    evaluate_verdict,
)
from meridian.prediction.verdict_examples import (
    VerdictExample,
    VerdictExamples,
    build_verdict_examples,
)
from meridian.prediction.verdict_files import publish_verdict, read_verdict
from meridian.prediction.verdict_rows import read_receptions
from meridian.prediction.verdict_score import VerdictModel, score_reception
from meridian.reports.bootstrap import Bootstrapped, cluster_bootstrap, clustered
from meridian.reports.prediction import Destination
from meridian.reports.verdict_config import VerdictSectionConfig

__all__ = ["SC7_TARGET", "VERDICT_FILE", "VerdictSection", "verdict_section"]

VERDICT_FILE = "verdict.jsonl"

SC7_TARGET = 0.40
"""SC-7: at least 40% Brier skill against the base rate (EVALUATION.md §1)."""

_PLACES = 6

Row = dict[str, object]


@dataclass(frozen=True, slots=True)
class _Judge:
    """What every subset is scored and resampled with."""

    model: VerdictModel
    resamples: int
    seed: int


@dataclass(frozen=True, slots=True)
class VerdictSection:
    """The section's rows, and the model it fitted, if it fitted one."""

    rows: list[Row]
    model_sha256: bytes | None


def verdict_section(
    raw: SnapshotDirectory,
    config: VerdictSectionConfig,
    *,
    seeds: tuple[int, int],
    destination: Destination,
) -> VerdictSection:
    """Fit the verdict from ``raw``, judge it, and give every row.

    Args:
        raw: The run's raw snapshot.
        config: ``[verdict]``.
        seeds: The fit's seed and the bootstrap's, both derived.
        destination: Where the model is published, and when.

    Returns:
        The rows: one ``verdict`` row always, and the judgement when measured.
    """
    # The fitter, inside: ``meridian`` loads this module and must start in the
    # image, where the ``fit`` extra is not (D-155).
    from meridian.prediction.fit import ModelFitError, fit_verdict  # noqa: PLC0415

    fit_seed, bootstrap_seed = seeds
    settings = replace(config.verdict, seed=fit_seed)
    try:
        found = build_verdict_examples(
            read_receptions(raw.files),
            read_usable_labels(raw.files),
            rubric=settings.rubric,
        )
        fitted = fit_verdict(found, settings, as_of=raw.manifest.as_of)
    except (MalformedSnapshotError, ModelFitError, SplitError) as exc:
        return VerdictSection([_refused(str(exc), config)], None)
    published = publish_verdict(
        fitted,
        snapshot=raw,
        config=settings,
        root=destination.root,
        created_at=destination.created_at,
    )
    held = read_verdict(published.path)
    split = fitted.split
    evaluation = evaluate_verdict(
        held.model,
        found,
        train_until=split.train_until,
        validate_until=split.validate_until,
        as_of=split.as_of,
    )
    judge = _Judge(held.model, config.resamples, bootstrap_seed)
    rows = [
        _summary(held.model, found, evaluation, config),
        *_judged("every", evaluation.every, evaluation.split.test, judge),
    ]
    if evaluation.rated is not None:
        rated_test = [one for one in evaluation.split.test if one.rated]
        rows.extend(_judged("rated", evaluation.rated, rated_test, judge))
    rows.extend(_segments(evaluation.every))
    rows.extend(
        {"row": "threshold", "side": side, "n": held_side.n}
        | {"usable": _rate(held_side.usable)}
        for side, held_side in (
            ("below", evaluation.below),
            ("above", evaluation.above),
        )
    )
    rows.append(_sc7(evaluation, rows))
    return VerdictSection(rows, content_sha256(held.directory.manifest))


def _refused(reason: str, config: VerdictSectionConfig) -> Row:
    return {
        "row": "verdict",
        "status": "not_measured",
        "reason": reason,
        "partial_below": _real(config.verdict.partial_below),
        "rubric": config.verdict.rubric,
    }


def _summary(
    model: VerdictModel,
    found: VerdictExamples,
    evaluation: VerdictEvaluation,
    config: VerdictSectionConfig,
) -> Row:
    split = evaluation.split
    return {
        "row": "verdict",
        "status": "measured",
        "method": model.method,
        "rubric": model.rubric,
        "partial_below": _real(model.partial_below),
        "train_until": split.train_until,
        "validate_until": split.validate_until,
        "as_of": split.as_of,
        "train": len(split.train),
        "validate": len(split.validate),
        "test": len(split.test),
        "simulated": found.simulated,
        "unrated": found.unrated,
        "other_rubric": found.other_rubric,
        "resamples": config.resamples,
    }


def _judged(
    subset: str,
    calibration: Calibration,
    test: Sequence[VerdictExample],
    judge: _Judge,
) -> list[Row]:
    """One subset's score, its interval, its diagram and its routes."""
    bootstrapped = _skill_interval(test, judge, calibration.base_rate)
    interval = bootstrapped.interval
    score: Row = {
        "row": "score",
        "subset": subset,
        "n": calibration.n,
        "usable": calibration.decoded,
        "brier": _real(calibration.brier),
        "base_rate": _real(calibration.base_rate),
        "base_brier": _real(calibration.base_brier),
        "skill": None if calibration.skill is None else _real(calibration.skill),
        "interval": None
        if interval is None
        else {"low": _real(interval.low), "high": _real(interval.high)},
        "dropped": bootstrapped.dropped,
    }
    bins: list[Row] = [
        {
            "row": "bin",
            "subset": subset,
            "low": _real(one.low),
            "high": _real(one.high),
            "n": one.n,
            "mean_predicted": None
            if one.mean_predicted is None
            else _real(one.mean_predicted),
            "observed": _rate(one.observed),
        }
        for one in calibration.bins
    ]
    routes: list[Row] = [
        {"row": "route", "subset": subset, "route": one.path, "n": one.n}
        | {"brier": _real(one.brier)}
        for one in calibration.routes
    ]
    return [score, *bins, *routes]


def _skill_interval(
    test: Sequence[VerdictExample], judge: _Judge, base_rate: float
) -> Bootstrapped:
    """Skill's interval, resampling whole station-days of the test span."""
    pairs = [
        (one, score_reception(judge.model, one.reception.inputs).probability_usable)
        for one in test
    ]
    days = clustered(
        pairs,
        lambda held: (
            held[0].reception.station_id,
            held[0].reception.started_at.date(),
        ),
    )
    return cluster_bootstrap(
        days,
        {"skill": _skill(base_rate)},
        seed=judge.seed,
        resamples=judge.resamples,
    )["skill"]


def _skill(
    base_rate: float,
) -> Callable[[Sequence[tuple[VerdictExample, float]]], float | None]:
    def skill(drawn: Sequence[tuple[VerdictExample, float]]) -> float | None:
        brier = fsum((p - one.usable) ** 2 for one, p in drawn)
        base = fsum((base_rate - one.usable) ** 2 for one, _ in drawn)
        return None if base == 0 else 1.0 - brier / base

    return skill


def _segments(calibration: Calibration) -> list[Row]:
    return [
        {
            "row": "segment",
            "dimension": one.dimension,
            "value": one.value,
            "n": one.n,
            "brier": _real(one.brier),
            "mean_predicted": _real(one.mean_predicted),
            "observed": _rate(one.observed),
        }
        for one in calibration.segments
    ]


def _sc7(evaluation: VerdictEvaluation, rows: Sequence[Row]) -> Row:
    """SC-7, read from rated receptions: the hard half (D-260)."""
    rated = next(
        (one for one in rows if one["row"] == "score" and one["subset"] == "rated"),
        None,
    )
    if evaluation.rated is None or rated is None:
        return {
            "row": "sc7",
            "status": "not_measured",
            "reason": "no rated reception in training or test",
            "target": SC7_TARGET,
        }
    skill, interval = rated["skill"], rated["interval"]
    return {
        "row": "sc7",
        "status": "measured",
        "n": rated["n"],
        "skill": skill,
        "interval": interval,
        "target": SC7_TARGET,
        "point_meets": isinstance(skill, float) and skill >= SC7_TARGET,
        "interval_above": isinstance(interval, dict) and interval["low"] >= SC7_TARGET,
    }


def _rate(rate: Rate | None) -> dict[str, float] | None:
    if rate is None:
        return None
    return {
        "estimate": _real(rate.estimate),
        "low": _real(rate.low),
        "high": _real(rate.high),
        "n": rate.n,
    }


def _real(value: float) -> float:
    return round(value, _PLACES)
