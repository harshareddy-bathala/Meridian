"""The station planner's model: configuration A, read from a published file.

The planner scores each pass with configuration A, which reads a pass's peak
elevation and nothing else (D-160). A fitted model is a directory under the
datasets root (D-163), which the api container is not given (D-332), and the
code that verifies one imports ``meridian.datasets``, which the API may not.
So ``meridian model planner`` copies the model out as ``plan-model-1``, a
file in ``PUBLISHED_DIR`` that ``PLAN_MODEL`` names, and this module reads it
back with the scorer of :mod:`meridian.prediction.score`, which imports the
standard library alone (D-155).

**Checked whole, and refused whole**, as D-332's summaries are: the schema
string must match, no key may be unknown, nothing is coerced, and the model
inside must parse as ``model.json`` does. Beyond that it must be a model the
planner can score with nothing but a pass: configuration A, reading no
history, whose one input is ``max_elevation_deg``, fitted on the feature code
this platform runs (D-255).

:data:`PLAN_FEATURE_VERSION` writes that code's version a second time, because
``meridian.prediction.features`` imports ``meridian.datasets``.
``tests/unit/test_plan_model.py`` holds the two equal, as D-061 holds the
speed of light, so a new feature version refuses an old planner model rather
than scoring it.

Reference: docs/DECISIONS.md D-061, D-155, D-160, D-163, D-255, D-332, D-370.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from meridian.api.public.models.plan_model import PlanModelFile
from meridian.prediction.score import MalformedModelError, Model, parse_model

__all__ = [
    "PLAN_CONFIGURATION",
    "PLAN_FEATURES",
    "PLAN_FEATURE_VERSION",
    "PLAN_SCHEMA",
    "HeldPlanModel",
    "PlanModelRefusedError",
    "read_plan_model",
]

PLAN_SCHEMA = "plan-model-1"

PLAN_CONFIGURATION = "A"

PLAN_FEATURES = ("max_elevation_deg",)
"""What a planner model may read: what the orbit says of a pass, and no more."""

PLAN_FEATURE_VERSION = "features-1"
"""``meridian.prediction.features.FEATURE_VERSION``, written again; see above."""


class PlanModelRefusedError(ValueError):
    """A planner model file that cannot be served, and why."""


@dataclass(frozen=True, slots=True)
class HeldPlanModel:
    """A planner model read and checked: what it is, and what scores with it."""

    file: PlanModelFile
    model: Model


def read_plan_model(path: Path) -> HeldPlanModel:
    """Read ``plan-model-1`` from ``path``, checked whole.

    Raises:
        OSError: The file cannot be read.
        PlanModelRefusedError: It is not a model the planner may score with.
    """
    raw = path.read_bytes()
    try:
        held = PlanModelFile.model_validate_json(raw)
    except ValidationError as exc:
        message = f"{path} is not {PLAN_SCHEMA}: {exc}"
        raise PlanModelRefusedError(message) from exc
    try:
        model = parse_model(json.dumps(held.fitted).encode("utf-8"))
    except MalformedModelError as exc:
        message = f"{path} holds a model that cannot be scored: {exc}"
        raise PlanModelRefusedError(message) from exc
    _require_plannable(model, path)
    return HeldPlanModel(file=held, model=model)


def _require_plannable(model: Model, path: Path) -> None:
    if model.configuration != PLAN_CONFIGURATION or model.reads_history:
        message = (
            f"{path} holds configuration {model.configuration}; the planner"
            f" scores with configuration {PLAN_CONFIGURATION}, which reads no"
            " history"
        )
        raise PlanModelRefusedError(message)
    if model.configured.features != PLAN_FEATURES:
        message = (
            f"{path} reads {', '.join(model.configured.features)}; a planner"
            f" model reads {', '.join(PLAN_FEATURES)} alone"
        )
        raise PlanModelRefusedError(message)
    if model.feature_version != PLAN_FEATURE_VERSION:
        message = (
            f"{path} was fitted on {model.feature_version} and this platform"
            f" computes {PLAN_FEATURE_VERSION}; fit it again"
        )
        raise PlanModelRefusedError(message)
