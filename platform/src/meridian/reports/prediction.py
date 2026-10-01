"""The prediction section's models: fitted, published and judged, one per variant.

``EVALUATION.md`` §3 asks for every configuration and §7 for every model's
calibration. The report fits:

* **A**, elevation only — and B, whose model is A's: B differs in the
  scheduler's objective, never in its probabilities (D-160);
* **C**, our features only;
* **D**, everything, the shipped system;
* **D∖conditions**, D with the public ``conditions`` group left out, the
  leave-one-group-out run that isolates that group (D-224, D-237).

Each is fitted by :func:`~meridian.prediction.fit.fit_model` under the
``[prediction]`` settings, with a seed derived from the run's master seed,
published under ``<root>/models`` exactly as ``meridian model fit`` would, read
back, and judged by :func:`~meridian.prediction.evaluation.evaluate_model` —
the same functions, so the report and ``meridian model evaluate`` cannot
disagree. The test span is then scored once more from the published file, pass
by pass, for the station-day bootstrap.

**A model that cannot be fitted is reported, not dropped.** Too few examples,
a population that holds only simulated passes (D-078), or a configuration the
population cannot take (D-156) gives a row that says why, and the report is
still built. On a development snapshot, where every pass is simulated, that is
every model, which is the honest answer.

**The fitter is imported inside :func:`fit_variants`**, as ``meridian model``
imports it: scikit-learn is the ``fit`` extra, which the image does not carry
(D-155).

Reference: docs/DECISIONS.md D-078, D-155, D-156, D-160, D-164, D-224, D-237.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from meridian.datasets.manifest import content_sha256
from meridian.datasets.publish import SnapshotDirectory
from meridian.datasets.seeds import derive
from meridian.prediction.features import require_current_features
from meridian.prediction.lineage import Inputs, LineageError, examples_of
from meridian.prediction.model_config import ModelConfig, ModelConfigError
from meridian.prediction.model_files import publish_model, read_model
from meridian.prediction.score import predict
from meridian.prediction.splits import SplitError
from meridian.reports.config import PredictionConfig

if TYPE_CHECKING:
    from meridian.prediction.evaluation import ModelEvaluation

__all__ = [
    "MODEL_SEED_RANGE",
    "VARIANTS",
    "Destination",
    "Fitted",
    "Judged",
    "Refused",
    "Variant",
    "fit_variants",
    "fitted_paths",
]

MODEL_SEED_RANGE = 2**32
"""A model's seed is below this (``ModelConfig``), so a derived seed is taken
modulo it; the manifest records the value used."""


@dataclass(frozen=True, slots=True)
class Variant:
    """One model the report fits: a name, a configuration, groups left out."""

    name: str
    configuration: str
    without: tuple[str, ...] = ()

    @property
    def seed_name(self) -> str:
        """The component its seed is derived under."""
        return f"model.{self.name}"


VARIANTS = (
    Variant("A", "A"),
    Variant("C", "C"),
    Variant("D", "D"),
    Variant("D-conditions", "D", ("conditions",)),
)


@dataclass(frozen=True, slots=True)
class Judged:
    """One test pass: where it belongs, what the model said, what happened."""

    station_day: tuple[str, str]
    probability: float
    positive: bool


@dataclass(frozen=True, slots=True)
class Fitted:
    """A variant fitted, published and judged."""

    variant: Variant
    config: ModelConfig
    path: Path
    sha256: bytes
    evaluation: ModelEvaluation

    judged: tuple[Judged, ...]


@dataclass(frozen=True, slots=True)
class Refused:
    """A variant that could not be fitted or judged, and why."""

    variant: Variant
    config: ModelConfig | None
    reason: str


@dataclass(frozen=True, slots=True)
class Destination:
    """Where models are published, and the time recorded on them (never hashed)."""

    root: Path
    created_at: datetime


def fit_variants(
    dataset: SnapshotDirectory,
    raw: SnapshotDirectory,
    config: PredictionConfig,
    *,
    seed: int,
    destination: Destination,
) -> tuple[dict[str, int], list[Fitted | Refused], Inputs | None]:
    """Every variant, fitted where it can be, with the seed each drew.

    Args:
        dataset: The evaluation dataset, verified.
        raw: The raw snapshot it was labelled from, verified.
        config: The ``[prediction]`` settings.
        seed: The run's master seed.
        destination: The datasets root the models are published under, and
            when this run happened.

    Returns:
        Each variant's seed by component name, each outcome in variant order,
        and the examples (``None`` when the dataset cannot give them).

    Raises:
        ModuleNotFoundError: The ``fit`` extra is not installed.
    """
    from meridian.prediction.evaluation import EvaluationError  # noqa: PLC0415
    from meridian.prediction.fit import ModelFitError  # noqa: PLC0415

    seeds = {
        one.seed_name: derive(seed, one.seed_name) % MODEL_SEED_RANGE
        for one in VARIANTS
    }
    try:
        inputs = examples_of(dataset, raw, config.model)
    except LineageError as exc:
        return seeds, [Refused(one, None, str(exc)) for one in VARIANTS], None
    refusals = (ModelConfigError, ModelFitError, SplitError, EvaluationError)
    outcomes: list[Fitted | Refused] = []
    for variant in VARIANTS:
        model_config: ModelConfig | None = None
        try:
            model_config = replace(
                config.model,
                configuration=variant.configuration,
                without=variant.without,
                seed=seeds[variant.seed_name],
            )
            outcomes.append(_fit(variant, model_config, inputs, dataset, destination))
        except refusals as exc:
            outcomes.append(Refused(variant, model_config, str(exc)))
    return seeds, outcomes, inputs


def _fit(
    variant: Variant,
    config: ModelConfig,
    inputs: Inputs,
    dataset: SnapshotDirectory,
    destination: Destination,
) -> Fitted:
    """Fit one variant, publish it, read it back, and judge it."""
    from meridian.prediction.evaluation import evaluate_model  # noqa: PLC0415
    from meridian.prediction.fit import fit_model  # noqa: PLC0415

    as_of = dataset.manifest.as_of
    fitted = fit_model(inputs.examples, config, as_of=as_of)
    published = publish_model(
        fitted,
        dataset=dataset,
        config=config,
        root=destination.root,
        created_at=destination.created_at,
    )
    model = read_model(published.path).model
    require_current_features(model)
    evaluation = evaluate_model(
        model, inputs.examples, config, as_of=as_of, bands=inputs.bands
    )
    judged = tuple(
        Judged(
            station_day=(one.station_id, one.aos.date().isoformat()),
            probability=predict(model, one.features, one.station_history).probability,
            positive=one.positive,
        )
        for one in fitted.split.test
    )
    return Fitted(
        variant=variant,
        config=config,
        path=published.path,
        sha256=content_sha256(published.manifest),
        evaluation=evaluation,
        judged=judged,
    )


def fitted_paths(outcomes: list[Fitted | Refused]) -> Mapping[str, Path]:
    """Each fitted variant's published directory, by name."""
    return {one.variant.name: one.path for one in outcomes if isinstance(one, Fitted)}
