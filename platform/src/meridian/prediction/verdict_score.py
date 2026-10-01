"""Scoring a reception's verdict from ``verdict.json`` — no numerical stack.

A fitted verdict model is data, written by :mod:`meridian.prediction.fit` and
read here with the standard library and :mod:`meridian.prediction.score`'s
calibrated logistic regression. Scoring a reception means:

1. choose its route by the evidence it has (:func:`verdict_inputs.route_of`);
2. score that route's model on its features: standardise, logit, then the
   Platt map fitted on validation (D-162's recipe, D-261).

**Read strictly.** An unknown format, a route missing or extra, a route whose
features are not exactly the ones :data:`verdict_inputs.FEATURES` names, or a
partial threshold outside (0, 1) is refused by name. A verdict scored from a
model that reads different features from the ones the inputs carry would be a
probability that looks like any other.

**What a verdict carries.** The probability, the route, the model's ``method``
and the inputs' hash, so a stored verdict can be traced to what it read and
the model that read it (D-104).

Reference: docs/DECISIONS.md D-104, D-162, D-260, D-261.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass

from meridian.prediction.score import Linear, MalformedModelError, parse_linear
from meridian.prediction.verdict_inputs import (
    FEATURES,
    ROUTES,
    ReceptionInputs,
    feature_values,
    inputs_sha256,
    route_of,
)

__all__ = [
    "VERDICT_FORMAT",
    "Verdict",
    "VerdictModel",
    "parse_verdict_model",
    "score_reception",
]

VERDICT_FORMAT = 1
"""Bumped when ``verdict.json`` changes shape. An unknown format is refused."""

_METHOD = re.compile(r"^verdict-[0-9]+:[0-9a-f]{12}$")
_RUBRIC = re.compile(r"^[a-z0-9][a-z0-9.-]{0,31}$")


@dataclass(frozen=True, slots=True)
class VerdictModel:
    """A fitted verdict: one calibrated model per route, and its threshold."""

    method: str
    """``verdict-<n>:<12 hex>``, naming the fit, stored with every verdict."""

    rubric: str
    """The rating instructions its labels were made under (D-260)."""

    partial_below: float
    """Below this, a decoded reception counts as partial (Stage 26, D-261)."""

    routes: Mapping[str, Linear]


@dataclass(frozen=True, slots=True)
class Verdict:
    """One reception's verdict, and what produced it."""

    probability_usable: float
    route: str
    method: str
    inputs_sha256: bytes


def score_reception(model: VerdictModel, inputs: ReceptionInputs) -> Verdict:
    """Score one reception by the route its evidence allows.

    Args:
        model: The fitted verdict model.
        inputs: What the reception reported and what the platform knew.

    Returns:
        The calibrated probability that it is usable, its route, the model's
        method and the inputs' hash.
    """
    route = route_of(inputs)
    probability = model.routes[route].probability(feature_values(inputs))
    return Verdict(
        probability_usable=probability,
        route=route,
        method=model.method,
        inputs_sha256=inputs_sha256(inputs),
    )


def parse_verdict_model(raw: bytes) -> VerdictModel:
    """Read ``verdict.json`` back, strictly.

    Raises:
        MalformedModelError: Not JSON, an unknown format, a missing or extra
            route, a route reading other features, or a bad field.
    """
    try:
        stored = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        message = f"the verdict model is not readable JSON: {exc}"
        raise MalformedModelError(message) from exc
    if not isinstance(stored, dict):
        message = "the verdict model is not an object"
        raise MalformedModelError(message)
    if stored.get("verdict_format") != VERDICT_FORMAT:
        message = f"unknown verdict format {stored.get('verdict_format')!r}"
        raise MalformedModelError(message)
    return VerdictModel(
        method=_matching(stored, "method", _METHOD),
        rubric=_matching(stored, "rubric", _RUBRIC),
        partial_below=_threshold(stored.get("partial_below")),
        routes=_routes(stored.get("routes")),
    )


def _routes(value: object) -> dict[str, Linear]:
    if not isinstance(value, dict) or set(value) != set(ROUTES):
        message = f"routes must be exactly {', '.join(ROUTES)}"
        raise MalformedModelError(message)
    routes = {}
    for route in ROUTES:
        linear = parse_linear(value[route], f"routes.{route}")
        if linear.features != FEATURES[route]:
            message = (
                f"routes.{route} reads {list(linear.features)},"
                f" not {list(FEATURES[route])}"
            )
            raise MalformedModelError(message)
        routes[route] = linear
    return routes


def _matching(stored: Mapping[str, object], name: str, pattern: re.Pattern[str]) -> str:
    value = stored.get(name)
    if not isinstance(value, str) or not pattern.match(value):
        message = f"{name} is {value!r}, not of the form {pattern.pattern}"
        raise MalformedModelError(message)
    return value


def _threshold(value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or not 0 < value < 1
    ):
        message = f"partial_below is {value!r}, not a probability strictly in (0, 1)"
        raise MalformedModelError(message)
    return float(value)
