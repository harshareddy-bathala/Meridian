"""Fitting a model — the one module that imports numpy and scikit-learn.

It is part of the ``meridian[fit]`` extra, which the platform image does not
install (D-155), and ``tests/unit/test_prediction_boundaries.py`` holds every
other module to that. What it produces is data: the document written as
``model.json``, which :mod:`meridian.prediction.score` reads with the standard
library alone.

**One fit is:**

1. the examples split by the configuration's dates (D-162), refused with the
   counts if either span is too small or holds one outcome only;
2. each feature standardised with the training span's mean and scale;
3. an L2-regularised logistic regression on training, with the stated
   ``inverse_regularisation`` and nothing tuned (D-155);
4. Platt calibration on validation: a one-feature logistic regression of the
   outcome on the model's logit, with Platt's smoothed targets so a
   validation span that the logit separates perfectly still has a finite map;
5. the same again for the geometry-only fallback, when the configuration reads
   station history (D-161).

**Every stored number is rounded to 12 significant figures,** and the model is
standardised and calibrated with the rounded values, so what is stored is
exactly what was used. In one environment, two fits give the same bytes; across
environments the claim is that predictions agree within 1e-9 (D-163).

**Nothing is fitted on simulated passes** (D-078), and the refusal says so when
every usable pass was simulated, rather than reporting too few examples.

Reference: docs/DECISIONS.md D-078, D-155, D-156, D-161, D-162, D-163.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import sklearn
from numpy.typing import NDArray
from sklearn.linear_model import LogisticRegression

from meridian.prediction.configurations import CONFIGURATIONS, FALLBACK
from meridian.prediction.examples import Example, ExampleSet
from meridian.prediction.model_config import ModelConfig
from meridian.prediction.score import MODEL_FORMAT
from meridian.prediction.splits import Split, temporal_split

__all__ = [
    "MIN_TRAIN",
    "MIN_VALIDATE",
    "FittedModel",
    "ModelFitError",
    "fit_model",
    "fit_split",
    "rounded",
]

MIN_TRAIN = 20
"""Training examples a fit needs, with both outcomes among them."""

MIN_VALIDATE = 10
"""Validation examples the calibration map needs, with both outcomes."""

SIGNIFICANT = 12
_MAX_ITER = 10_000
_TOL = 1e-10


class ModelFitError(ValueError):
    """A fit that cannot be made from these examples and this configuration."""


@dataclass(frozen=True, slots=True)
class FittedModel:
    """What a fit produced: the ``model.json`` document and its counts."""

    document: dict[str, object]
    counts: dict[str, int]
    split: Split


def rounded(value: float) -> float:
    """``value`` to 12 significant figures: what is stored, and what is used."""
    return float(f"{value:.{SIGNIFICANT}g}")


def fit_model(
    found: ExampleSet, config: ModelConfig, *, as_of: datetime
) -> FittedModel:
    """Fit the configured model, and its fallback where it needs one.

    Args:
        found: One population's examples, optionally weighted.
        config: The model configuration; it must name both split dates.
        as_of: The evaluation dataset's export time, the end of the test span.

    Returns:
        The ``model.json`` document, the counts for its manifest, and the split.

    Raises:
        ModelFitError: The configuration names no split dates, the population
            is not the configuration's, or a span is too small or one-sided.
        SplitError: The dates cannot split this dataset.
    """
    if config.train_until is None or config.validate_until is None:
        message = (
            "the model configuration names no train_until and validate_until;"
            " a fit splits on stated dates only (D-162)"
        )
        raise ModelFitError(message)
    if found.population != config.population:
        message = (
            f"the examples are the {found.population} population and the"
            f" configuration fits {config.population}"
        )
        raise ModelFitError(message)
    split = temporal_split(
        found.examples,
        train_until=config.train_until,
        validate_until=config.validate_until,
        as_of=as_of,
    )
    return fit_split(found, split, config)


def fit_split(found: ExampleSet, split: Split, config: ModelConfig) -> FittedModel:
    """Fit on a split already made: the main one, or a rolling-origin fold.

    Args:
        found: The examples the split was made from, for their counts.
        split: Training, validation and test spans, with their dates.
        config: The model configuration; its own dates are not read here.

    Returns:
        The document, its counts and the split.

    Raises:
        ModelFitError: A span is too small or holds one outcome only.
    """
    _check_enough(found, split)
    configuration = CONFIGURATIONS[config.configuration]
    document: dict[str, object] = {
        "model_format": MODEL_FORMAT,
        "configuration": configuration.name,
        "population": config.population,
        "weighted_by_priority": configuration.weighted_by_priority,
        "reads_history": configuration.reads_history,
        "min_station_history": config.min_station_history,
        "configured": _fit_linear(configuration.features, split, config),
        "fallback": (
            _fit_linear(FALLBACK.features, split, config)
            if configuration.reads_history
            else None
        ),
        "train_until": split.train_until,
        "validate_until": split.validate_until,
        "as_of": split.as_of,
        "inverse_regularisation": float(config.inverse_regularisation),
        "weighting": config.weighting,
        "seed": config.seed,
        "libraries": {"numpy": np.__version__, "scikit-learn": sklearn.__version__},
    }
    return FittedModel(document=document, counts=_counts(found, split), split=split)


def _check_enough(found: ExampleSet, split: Split) -> None:
    """Refuse a span a model or its calibration cannot be learned from."""
    if not found.examples and found.simulated:
        message = (
            f"all {found.simulated} usable passes are simulated, and a model is"
            " never fitted on the simulator's own rule (D-078)"
        )
        raise ModelFitError(message)
    for name, span, least in (
        ("training", split.train, MIN_TRAIN),
        ("validation", split.validate, MIN_VALIDATE),
    ):
        positive = sum(one.positive for one in span)
        if len(span) < least or positive in {0, len(span)}:
            message = (
                f"{name} holds {len(span)} examples, {positive} of them decoded;"
                f" a fit needs at least {least} with both outcomes"
                f" ({found.simulated} simulated passes were not counted)"
            )
            raise ModelFitError(message)


def _fit_linear(
    names: Sequence[str], split: Split, config: ModelConfig
) -> dict[str, object]:
    """One calibrated logistic regression, as the numbers ``score`` reads."""
    train_x, train_y, train_w = _arrays(split.train, names)
    mean = np.array([rounded(one) for one in train_x.mean(axis=0)])
    spread = train_x.std(axis=0)
    scale = np.array([rounded(one) if one > 0 else 1.0 for one in spread])
    model = LogisticRegression(
        C=config.inverse_regularisation,
        random_state=config.seed,
        max_iter=_MAX_ITER,
        tol=_TOL,
    ).fit((train_x - mean) / scale, train_y, sample_weight=train_w)
    coefficients = np.array([rounded(one) for one in model.coef_[0]])
    intercept = rounded(float(model.intercept_[0]))
    valid_x, valid_y, valid_w = _arrays(split.validate, names)
    logits = ((valid_x - mean) / scale) @ coefficients + intercept
    a, b = _platt(logits, valid_y, valid_w, config.seed)
    return {
        "features": list(names),
        "mean": [float(one) for one in mean],
        "scale": [float(one) for one in scale],
        "coefficients": [float(one) for one in coefficients],
        "intercept": intercept,
        "calibration": {"method": "platt", "a": a, "b": b},
    }


def _platt(
    logits: NDArray[np.float64],
    outcomes: NDArray[np.int_],
    weights: NDArray[np.float64],
    seed: int,
) -> tuple[float, float]:
    """Platt's map from logit to probability, with his smoothed targets.

    Each example appears twice, once as a decode and once as a miss, weighted
    by its target and its complement, so the fit is Platt's soft-label one and
    stays finite whatever the logit separates.
    """
    positives = float(weights[outcomes == 1].sum())
    negatives = float(weights[outcomes == 0].sum())
    target = np.where(
        outcomes == 1, (positives + 1) / (positives + 2), 1 / (negatives + 2)
    )
    column = np.concatenate([logits, logits]).reshape(-1, 1)
    labels = np.concatenate([np.ones_like(outcomes), np.zeros_like(outcomes)])
    sample = np.concatenate([weights * target, weights * (1 - target)])
    fitted = LogisticRegression(
        C=np.inf, random_state=seed, max_iter=_MAX_ITER, tol=_TOL
    ).fit(column, labels, sample_weight=sample)
    return rounded(float(fitted.coef_[0][0])), rounded(float(fitted.intercept_[0]))


def _arrays(
    span: Sequence[Example], names: Sequence[str]
) -> tuple[NDArray[np.float64], NDArray[np.int_], NDArray[np.float64]]:
    """The features, outcomes and weights of a span, in ``names`` order."""
    return (
        np.array([[one.features[name] for name in names] for one in span], dtype=float),
        np.array([int(one.positive) for one in span], dtype=int),
        np.array([one.weight for one in span], dtype=float),
    )


def _counts(found: ExampleSet, split: Split) -> dict[str, int]:
    """Each span's size and decodes, and what was left out and why."""
    counts = {
        "examples.simulated": found.simulated,
        "examples.without_weight": found.without_weight,
    }
    for name, span in (
        ("train", split.train),
        ("validate", split.validate),
        ("test", split.test),
    ):
        counts[f"examples.{name}"] = len(span)
        counts[f"examples.{name}_decoded"] = sum(one.positive for one in span)
    return counts
