"""``plan-model-1``: a configuration A model, written out for the station planner.

The api container is not given the datasets root (D-332), and the code that
verifies a model directory imports ``meridian.datasets``, which the API may
not. So ``meridian model planner`` reads a verified model directory here and
writes the one thing the planner needs: ``model.json`` unchanged, with the
model's hash, its training population, when its dataset was taken and which
dataset it was. ``meridian.api.public.plan_model`` reads it back.

Only a model the planner can score from a pass alone is written: configuration
A, reading no history and nothing but ``max_elevation_deg``, fitted on the
feature code this checkout runs (D-160, D-255).

Reference: docs/DECISIONS.md D-160, D-163, D-255, D-332, D-370.
"""

from __future__ import annotations

import json
from pathlib import Path

from meridian.datasets.manifest import content_sha256
from meridian.prediction.features import require_current_features
from meridian.prediction.model_config import POPULATIONS
from meridian.prediction.model_files import MODEL_FILE, FittedDirectory
from meridian.prediction.score import MalformedModelError

__all__ = [
    "PLANNER_FEATURES",
    "PLANNER_SCHEMA",
    "planner_document",
    "write_planner_file",
]

PLANNER_SCHEMA = "plan-model-1"
"""The file ``meridian.api.public.plan_model`` reads; a test holds the two equal."""

PLANNER_FEATURES = ("max_elevation_deg",)
"""Configuration A's one input: all the planner knows of a pass is its orbit."""


def planner_document(held: FittedDirectory) -> dict[str, object]:
    """``plan-model-1`` for a model the planner can score with a pass alone.

    Raises:
        MalformedModelError: Not configuration A, reading history or more than
            the peak elevation, or fitted on other feature code than this.
    """
    model = held.model
    require_current_features(model)
    if model.configuration != "A" or model.reads_history:
        message = (
            f"the planner scores with configuration A, and this model is"
            f" configuration {model.configuration}"
        )
        raise MalformedModelError(message)
    if model.configured.features != PLANNER_FEATURES:
        message = (
            f"the model reads {', '.join(model.configured.features)}; the planner's"
            f" reads {', '.join(PLANNER_FEATURES)} alone"
        )
        raise MalformedModelError(message)
    fitted = json.loads(held.directory.files[MODEL_FILE])
    population = fitted.get("population")
    if population not in POPULATIONS:
        message = f"the model names no population it was fitted on: {population!r}"
        raise MalformedModelError(message)
    manifest = held.directory.manifest
    if manifest.derived_from is None:
        message = "the model's manifest names no dataset it was fitted on"
        raise MalformedModelError(message)
    return {
        "schema": PLANNER_SCHEMA,
        "model_sha256": content_sha256(manifest).hex(),
        "configuration": model.configuration,
        "population": population,
        "as_of": manifest.as_of.isoformat(),
        "dataset_sha256": manifest.derived_from.hex(),
        "fitted": fitted,
    }


def write_planner_file(path: Path, document: dict[str, object]) -> None:
    """Write the file so the api never reads half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(f".{path.name}.partial")
    scratch.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    scratch.replace(path)
