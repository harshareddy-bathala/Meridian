"""A published model judged on its test span, and refitted across folds.

**The test span is scored with the published file.** The model is read back
from ``model.json`` and each pass scored by :func:`~meridian.prediction.score.
predict`, the function the scheduler calls, so the figures are those of the
numbers that ship and not of an array still held by the fitter.

**The reference is learned from training.** The base rate a model is compared
with is the training span's decode rate, and the test span is read only to be
judged (D-162, D-164).

**The folds give the spread.** Each rolling-origin fold is a whole split inside
the span before ``validate_until`` (:func:`~meridian.prediction.splits.
rolling_origin`), fitted by the same :func:`~meridian.prediction.fit.fit_split`
and judged the same way. A fold that cannot be fitted is reported with the
reason, never left out, and none of them reads the test span.

This module refits, so it imports the fitter and is part of the ``fit`` extra;
nothing on the Pi imports it.

Reference: docs/DECISIONS.md D-161, D-162, D-164; docs/EVALUATION.md §7, §8.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from math import fsum
from statistics import stdev

from meridian.datasets.canonical import canonical_bytes
from meridian.prediction.calibration import (
    UNKNOWN,
    Calibration,
    Scored,
    age_bucket,
    calibrate,
)
from meridian.prediction.examples import Example, ExampleSet
from meridian.prediction.fit import ModelFitError, fit_split
from meridian.prediction.model_config import ModelConfig
from meridian.prediction.score import Model, parse_model, predict
from meridian.prediction.splits import Split, rolling_origin, temporal_split

__all__ = [
    "EvaluationError",
    "FoldResult",
    "ModelEvaluation",
    "base_rate_of",
    "evaluate_model",
    "judge",
]

_AGE = "element_set_age_h"


class EvaluationError(ValueError):
    """A model that cannot be judged on these examples with these dates."""


@dataclass(frozen=True, slots=True)
class FoldResult:
    """One rolling-origin fold: its dates, and its figures or why it has none."""

    train_until: datetime
    validate_until: datetime
    as_of: datetime
    n: int
    """Passes in the fold's judged span."""

    brier: float | None
    base_brier: float | None
    refused: str | None
    """Why the fold has no figures; ``None`` when it has them."""


@dataclass(frozen=True, slots=True)
class ModelEvaluation:
    """The test span's calibration, and the folds beside it."""

    split: Split
    calibration: Calibration
    folds: tuple[FoldResult, ...]
    simulated: int
    without_weight: int

    @property
    def fold_brier(self) -> tuple[float, float | None] | None:
        """Mean and sample standard deviation of Brier over the fitted folds.

        ``None`` with no fitted fold; the deviation is ``None`` with one.
        """
        scores = [one.brier for one in self.folds if one.brier is not None]
        if not scores:
            return None
        spread = stdev(scores) if len(scores) > 1 else None
        return fsum(scores) / len(scores), spread


def evaluate_model(
    model: Model,
    found: ExampleSet,
    config: ModelConfig,
    *,
    as_of: datetime,
    bands: Mapping[str, str],
) -> ModelEvaluation:
    """Judge a published model on its test span, and run the folds.

    Args:
        model: The model, as read back from its directory.
        found: The examples of the dataset it was fitted on.
        config: The configuration it was fitted under.
        as_of: The dataset's export time, the end of the test span.
        bands: Each satellite's band, for the band segment.

    Returns:
        The test span's calibration and each fold's figures.

    Raises:
        EvaluationError: No split dates, another population, or an empty
            training or test span.
        SplitError: The dates cannot split this dataset.
    """
    if config.train_until is None or config.validate_until is None:
        message = "the model configuration names no split dates (D-162)"
        raise EvaluationError(message)
    if found.population != config.population:
        message = (
            f"the examples are the {found.population} population and the"
            f" model was fitted on {config.population}"
        )
        raise EvaluationError(message)
    split = temporal_split(
        found.examples,
        train_until=config.train_until,
        validate_until=config.validate_until,
        as_of=as_of,
    )
    if not split.train or not split.test:
        message = (
            f"the training span holds {len(split.train)} examples and the test"
            f" span from {config.validate_until.isoformat()} to"
            f" {as_of.isoformat()} holds {len(split.test)}; a model is judged"
            " on a test span against its training base rate"
        )
        raise EvaluationError(message)
    return ModelEvaluation(
        split=split,
        calibration=judge(model, split.test, base_rate_of(split.train), bands),
        folds=_folds(found, config, config.validate_until, bands),
        simulated=found.simulated,
        without_weight=found.without_weight,
    )


def base_rate_of(span: Sequence[Example]) -> float:
    """The decode rate of a span: the base-rate predictor's one number.

    Raises:
        EvaluationError: The span is empty.
    """
    if not span:
        message = "a base rate needs at least one example"
        raise EvaluationError(message)
    return sum(one.positive for one in span) / len(span)


def judge(
    model: Model,
    span: Sequence[Example],
    base_rate: float,
    bands: Mapping[str, str],
) -> Calibration:
    """Score every pass of ``span`` with ``model`` and calibrate the result."""
    scored = []
    for one in span:
        prediction = predict(model, one.features, one.station_history)
        age = one.features.get(_AGE)
        scored.append(
            Scored(
                probability=prediction.probability,
                positive=one.positive,
                path=prediction.path,
                segments={
                    "station": one.station_id,
                    "band": bands.get(one.satellite_id, UNKNOWN),
                    "element_set_age": age_bucket(age),
                },
            )
        )
    return calibrate(scored, base_rate=base_rate)


def _folds(
    found: ExampleSet,
    config: ModelConfig,
    until: datetime,
    bands: Mapping[str, str],
) -> tuple[FoldResult, ...]:
    """Each rolling-origin fold refitted and judged on its own span."""
    if config.folds == 0:
        return ()
    results = []
    for split in rolling_origin(found.examples, until=until, folds=config.folds):
        results.append(_fold(found, split, config, bands))
    return tuple(results)


def _fold(
    found: ExampleSet, split: Split, config: ModelConfig, bands: Mapping[str, str]
) -> FoldResult:
    refused: str | None = None
    calibration: Calibration | None = None
    if not split.test:
        refused = "its judged span holds no examples"
    else:
        try:
            fitted = fit_split(found, split, config)
        except ModelFitError as exc:
            refused = str(exc)
        else:
            model = parse_model(canonical_bytes(fitted.document))
            calibration = judge(model, split.test, base_rate_of(split.train), bands)
    return FoldResult(
        train_until=split.train_until,
        validate_until=split.validate_until,
        as_of=split.as_of,
        n=len(split.test),
        brier=None if calibration is None else calibration.brier,
        base_brier=None if calibration is None else calibration.base_brier,
        refused=refused,
    )
