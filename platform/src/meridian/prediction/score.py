"""Scoring a pass from a model file — the standard library and nothing else.

This is the one prediction module the scheduler of Stage 18 imports, on the
Pi, so it is held to the runtime rule: no numpy, no scikit-learn, and no
``meridian.datasets`` (D-155). A fitted model is data — ``model.json``,
written by :mod:`meridian.prediction.fit` — and scoring it is:

1. standardise each feature with the training span's mean and scale;
2. the dot product with the coefficients, plus the intercept: the logit;
3. the Platt map fitted on validation, ``sigmoid(a × logit + b)`` (D-162).

**The route is decided here too** (D-161). A configuration that reads the
station's own record scores a station with fewer than ``min_station_history``
settled outcomes by the geometry-only model instead, and every prediction
carries its path and the reason, so a report can count them.

**A model file is read strictly.** An unknown format, a missing field, a
length that does not match the feature list or a number that is not finite is
refused by name, since a model scored from a damaged file would produce a
probability that looks like any other.

Reference: docs/DECISIONS.md D-155, D-161, D-162, D-163.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

__all__ = [
    "CONFIGURED",
    "GEOMETRY_FALLBACK",
    "MODEL_FORMAT",
    "Linear",
    "MalformedModelError",
    "Model",
    "Prediction",
    "Route",
    "parse_model",
    "predict",
    "route_for",
    "sigmoid",
]

MODEL_FORMAT = 1
"""Bumped when ``model.json`` changes shape. An unknown format is refused."""

CONFIGURED = "configured"
GEOMETRY_FALLBACK = "geometry_fallback"


class MalformedModelError(ValueError):
    """A model file that cannot be scored as written."""


@dataclass(frozen=True, slots=True)
class Route:
    """Which model scores a pass, and why."""

    path: str
    """``configured`` or ``geometry_fallback``."""

    reason: str


@dataclass(frozen=True, slots=True)
class Linear:
    """One calibrated logistic regression, as numbers."""

    features: tuple[str, ...]
    mean: tuple[float, ...]
    scale: tuple[float, ...]
    coefficients: tuple[float, ...]
    intercept: float
    calibration_a: float
    calibration_b: float

    def logit(self, values: Mapping[str, float]) -> float:
        """The uncalibrated log-odds.

        Raises:
            MalformedModelError: A feature the model reads is not in ``values``.
        """
        total = self.intercept
        for name, mean, scale, weight in zip(
            self.features, self.mean, self.scale, self.coefficients, strict=True
        ):
            try:
                value = values[name]
            except KeyError as exc:
                message = f"the model reads {name!r}, which the pass does not have"
                raise MalformedModelError(message) from exc
            total += weight * (value - mean) / scale
        return total

    def probability(self, values: Mapping[str, float]) -> float:
        """The calibrated probability of a decode."""
        return sigmoid(self.calibration_a * self.logit(values) + self.calibration_b)


@dataclass(frozen=True, slots=True)
class Model:
    """A fitted model: the configured one, and the fallback where it has one."""

    configuration: str
    reads_history: bool
    min_station_history: int
    configured: Linear
    fallback: Linear | None
    """The geometry-only model; ``None`` when the configuration reads no history."""


@dataclass(frozen=True, slots=True)
class Prediction:
    """A probability, and which model gave it."""

    probability: float
    path: str
    reason: str


def sigmoid(x: float) -> float:
    """The logistic function, without overflow at either end."""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def route_for(
    configuration: str,
    *,
    reads_history: bool,
    station_history: int,
    min_station_history: int,
) -> Route:
    """The model a pass is scored by, given its station's settled record.

    Args:
        configuration: The configured model's name, for the reason.
        reads_history: Whether it reads the station's own record.
        station_history: The station's settled, usable outcomes before the pass.
        min_station_history: How many the configured model needs.

    Returns:
        The route, with its reason stated.
    """
    if not reads_history:
        return Route(CONFIGURED, f"configuration {configuration} reads no history")
    if station_history >= min_station_history:
        return Route(
            CONFIGURED, f"{station_history} settled outcomes, enough for history"
        )
    if station_history == 0:
        reason = "a new station: no settled outcomes"
    else:
        reason = (
            f"{station_history} settled outcomes, below min_station_history"
            f" {min_station_history}"
        )
    return Route(GEOMETRY_FALLBACK, reason)


def predict(
    model: Model, features: Mapping[str, float], station_history: int
) -> Prediction:
    """Score one pass.

    Args:
        model: The fitted model.
        features: The pass's features by name; extra names are ignored.
        station_history: The station's settled, usable outcomes before the pass.

    Returns:
        The probability, the path it came by and why.
    """
    taken = route_for(
        model.configuration,
        reads_history=model.reads_history,
        station_history=station_history,
        min_station_history=model.min_station_history,
    )
    linear = model.configured
    if taken.path == GEOMETRY_FALLBACK:
        if model.fallback is None:
            message = "the model reads history but carries no fallback"
            raise MalformedModelError(message)
        linear = model.fallback
    return Prediction(linear.probability(features), taken.path, taken.reason)


def parse_model(raw: bytes) -> Model:
    """Read ``model.json`` back, strictly.

    Raises:
        MalformedModelError: Not JSON, an unknown format, a missing field, or
            a value of the wrong type or length.
    """
    try:
        stored = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        message = f"the model is not readable JSON: {exc}"
        raise MalformedModelError(message) from exc
    stored = _object(stored, "model")
    if stored.get("model_format") != MODEL_FORMAT:
        message = f"unknown model format {stored.get('model_format')!r}"
        raise MalformedModelError(message)
    fallback = _field(stored, "fallback")
    reads_history = _field(stored, "reads_history")
    history = _field(stored, "min_station_history")
    configuration = _field(stored, "configuration")
    if not isinstance(reads_history, bool) or not isinstance(configuration, str):
        message = "reads_history must be true or false, configuration text"
        raise MalformedModelError(message)
    if isinstance(history, bool) or not isinstance(history, int):
        message = f"min_station_history is {history!r}, not a whole number"
        raise MalformedModelError(message)
    if reads_history != (fallback is not None):
        message = "a model has a fallback exactly when it reads history"
        raise MalformedModelError(message)
    return Model(
        configuration=configuration,
        reads_history=reads_history,
        min_station_history=history,
        configured=_linear(_field(stored, "configured"), "configured"),
        fallback=None if fallback is None else _linear(fallback, "fallback"),
    )


def _linear(value: object, where: str) -> Linear:
    stored = _object(value, where)
    features = _field(stored, "features")
    if not isinstance(features, list) or not all(
        isinstance(one, str) for one in features
    ):
        message = f"{where}.features is not a list of names"
        raise MalformedModelError(message)
    calibration = _object(_field(stored, "calibration"), f"{where}.calibration")
    size = len(features)
    scale = _numbers(stored, "scale", size, where)
    if any(one <= 0 for one in scale):
        message = f"{where}.scale holds a value that is not positive"
        raise MalformedModelError(message)
    return Linear(
        features=tuple(features),
        mean=_numbers(stored, "mean", size, where),
        scale=scale,
        coefficients=_numbers(stored, "coefficients", size, where),
        intercept=_number(_field(stored, "intercept"), f"{where}.intercept"),
        calibration_a=_number(_field(calibration, "a"), f"{where}.calibration.a"),
        calibration_b=_number(_field(calibration, "b"), f"{where}.calibration.b"),
    )


def _object(value: object, where: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        message = f"{where} is not an object"
        raise MalformedModelError(message)
    return value


def _field(stored: Mapping[str, object], name: str) -> object:
    try:
        return stored[name]
    except KeyError as exc:
        message = f"the model has no {name!r}"
        raise MalformedModelError(message) from exc


def _numbers(
    stored: Mapping[str, object], name: str, size: int, where: str
) -> tuple[float, ...]:
    values = _field(stored, name)
    if not isinstance(values, Sequence) or isinstance(values, str):
        message = f"{where}.{name} is not a list"
        raise MalformedModelError(message)
    if len(values) != size:
        message = f"{where}.{name} has {len(values)} values for {size} features"
        raise MalformedModelError(message)
    return tuple(_number(one, f"{where}.{name}") for one in values)


def _number(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        message = f"{where} is {value!r}, not a number"
        raise MalformedModelError(message)
    if not math.isfinite(value):
        message = f"{where} is not finite"
        raise MalformedModelError(message)
    return float(value)
