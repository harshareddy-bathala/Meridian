"""Judging a published model — on its test span, against training, across folds.

Three claims:

* **the figures are the published file's** — the test span is scored by
  ``score.predict`` over the model read back, pass by pass;
* **nothing is learned from the judged span** — the base rate is training's,
  and no rolling-origin fold reads anything at or after ``validate_until``
  (D-162, D-164). Each is shown by flipping outcomes and finding the figure
  unmoved, with a positive control that moves it;
* **a fold that cannot be fitted says why** — it is never left out.

Every model here is fitted on examples built in this file.

Reference: docs/DECISIONS.md D-161, D-162, D-164.
"""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from meridian.datasets.canonical import canonical_bytes
from meridian.datasets.completeness import summarise
from meridian.datasets.result import EvaluationResult, NotWeighted
from meridian.datasets.selection_config import CompletenessConfig
from meridian.prediction.calibration import UNKNOWN, brier
from meridian.prediction.calibration_report import Provenance, evaluation_lines
from meridian.prediction.evaluation import (
    EvaluationError,
    ModelEvaluation,
    base_rate_of,
    evaluate_model,
)
from meridian.prediction.examples import Example, ExampleSet
from meridian.prediction.features import FEATURES
from meridian.prediction.fit import fit_model
from meridian.prediction.model_config import ModelConfig
from meridian.prediction.score import Model, parse_model, predict, sigmoid
from meridian.prediction.splits import rolling_origin

DAY0 = datetime(2026, 1, 1, 6, 0, tzinfo=UTC)
TRAIN_UNTIL = DAY0 + timedelta(days=60)
VALIDATE_UNTIL = DAY0 + timedelta(days=80)
AS_OF = DAY0 + timedelta(days=100)
CONFIG = ModelConfig(
    configuration="D",
    min_station_history=5,
    train_until=TRAIN_UNTIL,
    validate_until=VALIDATE_UNTIL,
    folds=3,
)
BANDS = {"norad:57166": "vhf"}


def example(n: int, rng: random.Random) -> Example:
    """Pass ``n``: two a day, decoding more often the higher it climbs."""
    features = {one.name: rng.uniform(-1.0, 1.0) for one in FEATURES}
    features["max_elevation_deg"] = rng.uniform(5.0, 85.0)
    features["element_set_age_h"] = rng.uniform(0.0, 200.0)
    chance = sigmoid((features["max_elevation_deg"] - 35.0) / 12.0)
    return Example(
        population="own",
        station_id=f"st_{n % 3}",
        satellite_id="norad:57166" if n % 5 else "norad:99999",
        aos=DAY0 + timedelta(hours=12 * n),
        positive=rng.random() < chance,
        features=features,
        station_history=n // 3,
    )


def example_set(count: int = 200, seed: int = 7) -> ExampleSet:
    rng = random.Random(seed)
    return ExampleSet("own", tuple(example(n, rng) for n in range(count)), 0)


def fitted(found: ExampleSet, config: ModelConfig = CONFIG) -> Model:
    document = fit_model(found, config, as_of=AS_OF).document
    return parse_model(canonical_bytes(document))


def evaluated(
    found: ExampleSet, config: ModelConfig = CONFIG, model: Model | None = None
) -> ModelEvaluation:
    held = model if model is not None else fitted(found, config)
    return evaluate_model(held, found, config, as_of=AS_OF, bands=BANDS)


def flipped(found: ExampleSet, *, since: datetime, until: datetime) -> ExampleSet:
    """Every outcome between two dates the other way round."""
    return replace(
        found,
        examples=tuple(
            replace(one, positive=not one.positive) if since <= one.aos < until else one
            for one in found.examples
        ),
    )


# --- the test span, scored by the published model ------------------------------


def test_the_test_span_is_scored_by_the_published_model_pass_by_pass() -> None:
    found = example_set()
    model = fitted(found)

    result = evaluated(found, model=model)
    expected = [
        (predict(model, one.features, one.station_history).probability, one.positive)
        for one in result.split.test
    ]

    assert result.calibration.n == len(result.split.test) == 40
    assert result.calibration.brier == pytest.approx(brier(expected), abs=1e-15)


def test_the_base_rate_is_trainings_and_the_test_span_cannot_move_it() -> None:
    found = example_set()
    model = fitted(found)
    train = [one for one in found.examples if one.aos < TRAIN_UNTIL]

    result = evaluated(found, model=model)
    later = evaluated(flipped(found, since=VALIDATE_UNTIL, until=AS_OF), model=model)
    earlier = evaluated(flipped(found, since=DAY0, until=TRAIN_UNTIL), model=model)

    assert result.calibration.base_rate == base_rate_of(train)
    assert later.calibration.base_rate == result.calibration.base_rate
    assert earlier.calibration.base_rate != result.calibration.base_rate


def test_segments_come_from_the_band_table_and_the_set_age() -> None:
    result = evaluated(example_set())
    segments = {(one.dimension, one.value) for one in result.calibration.segments}

    assert ("band", "vhf") in segments
    assert ("band", UNKNOWN) in segments
    assert {value for dimension, value in segments if dimension == "station"} == {
        "st_0",
        "st_1",
        "st_2",
    }
    ages = {value for dimension, value in segments if dimension == "element_set_age"}
    assert ages <= {"<24 h", "24–72 h", "72–168 h", "≥168 h"}
    assert len(ages) > 1


def test_both_routes_are_counted_when_young_stations_are_judged() -> None:
    found = example_set()
    young = replace(
        found,
        examples=tuple(
            replace(one, station_history=0) if one.station_id == "st_0" else one
            for one in found.examples
        ),
    )

    result = evaluated(young)

    assert {one.path for one in result.calibration.routes} == {
        "configured",
        "geometry_fallback",
    }
    assert sum(one.n for one in result.calibration.routes) == result.calibration.n


# --- refusals ---------------------------------------------------------------------


def test_an_empty_test_span_is_refused_with_the_dates() -> None:
    found = example_set(160)
    model = fitted(found)

    with pytest.raises(EvaluationError, match="test span from 2026-03-22"):
        evaluate_model(model, found, CONFIG, as_of=VALIDATE_UNTIL, bands=BANDS)


def test_a_configuration_without_dates_is_refused() -> None:
    found = example_set()

    with pytest.raises(EvaluationError, match="no split dates"):
        evaluate_model(fitted(found), found, ModelConfig(), as_of=AS_OF, bands=BANDS)


def test_another_population_is_refused() -> None:
    found = example_set()

    with pytest.raises(EvaluationError, match="archive population"):
        evaluate_model(
            fitted(found),
            replace(found, population="archive"),
            CONFIG,
            as_of=AS_OF,
            bands=BANDS,
        )


# --- rolling-origin folds ------------------------------------------------------------


def test_each_fold_is_fitted_and_judged_before_validate_until() -> None:
    result = evaluated(example_set())

    assert len(result.folds) == CONFIG.folds
    assert all(one.refused is None for one in result.folds)
    assert all(one.brier is not None and one.n > 0 for one in result.folds)
    assert result.folds[-1].as_of == VALIDATE_UNTIL
    spread = result.fold_brier
    assert spread is not None
    mean, deviation = spread
    assert deviation is not None
    assert min(one.brier or 0 for one in result.folds) <= mean


def test_no_fold_reads_the_test_span() -> None:
    """Flip every test outcome and every fold is unmoved; flip validation and
    they move — the positive control."""
    found = example_set()
    model = fitted(found)

    result = evaluated(found, model=model)
    after_test = evaluated(
        flipped(found, since=VALIDATE_UNTIL, until=AS_OF), model=model
    )
    after_validation = evaluated(
        flipped(found, since=TRAIN_UNTIL, until=VALIDATE_UNTIL), model=model
    )

    assert after_test.folds == result.folds
    assert after_validation.folds != result.folds


def test_a_fold_too_small_to_fit_says_why() -> None:
    config = replace(CONFIG, folds=20)

    result = evaluated(example_set(), config)

    assert len(result.folds) == 20
    refused = [one for one in result.folds if one.refused is not None]
    assert refused
    assert "training holds" in (refused[0].refused or "")
    assert refused[0].brier is None


def test_each_folds_reference_is_its_own_training_base_rate() -> None:
    found = example_set()

    result = evaluated(found)
    splits = rolling_origin(found.examples, until=VALIDATE_UNTIL, folds=CONFIG.folds)

    for fold, split in zip(result.folds, splits, strict=True):
        base = base_rate_of(split.train)
        assert fold.base_brier == pytest.approx(
            brier((base, one.positive) for one in split.test), abs=1e-15
        )


def test_one_fold_asked_for_is_one_fold_run() -> None:
    result = evaluated(example_set(), replace(CONFIG, folds=1))

    assert len(result.folds) == 1
    assert result.folds[0].brier is not None


def test_no_folds_are_run_when_the_configuration_asks_for_none() -> None:
    result = evaluated(example_set(), replace(CONFIG, folds=0))

    assert result.folds == ()
    assert result.fold_brier is None


# --- the report ------------------------------------------------------------------


def completeness() -> EvaluationResult:
    return EvaluationResult(
        population="own",
        completeness=summarise((), "own", CompletenessConfig()),
        weighting=NotWeighted("no eligible passes in this fixture"),
    )


PROVENANCE = Provenance(
    model_sha256="a" * 64, dataset_sha256="b" * 64, config_sha256="c" * 64
)


def test_the_report_states_everything_a_figure_needs() -> None:
    result = evaluated(example_set())

    printed = "\n".join(evaluation_lines(result, CONFIG, PROVENANCE, completeness()))

    for needed in (
        "a" * 64,
        "b" * 64,
        "c" * 64,
        "train until        2026-03-02T06:00:00+00:00",
        "validate until     2026-03-22T06:00:00+00:00",
        "seed               0",
        f"Brier              {result.calibration.brier:.4f}",
        "from training",
        "skill",
        "0.0–0.1",
        "0.9–1.0",
        "station          st_0",
        "band             vhf",
        "rolling-origin folds (3 asked",
        "Brier across folds",
        "completeness of the dataset",
        "not weighted: no eligible passes in this fixture",
    ):
        assert needed in printed, needed


def test_the_report_keeps_empty_bins_and_says_when_folds_were_not_run() -> None:
    config = replace(CONFIG, folds=0)
    result = evaluated(example_set(), config)

    lines = evaluation_lines(result, config, PROVENANCE, completeness())

    bins = [line for line in lines if line.startswith("  0.") and "–" in line[:12]]
    assert len(bins) == 10
    assert "rolling-origin folds: not run (folds = 0)" in lines


def test_b_is_reported_as_as_probabilities() -> None:
    config = replace(CONFIG, configuration="B")
    result = evaluated(example_set(), config)

    lines = evaluation_lines(result, config, PROVENANCE, completeness())

    assert any("B's probabilities are A's" in line for line in lines)


def test_a_refused_fold_is_printed_with_its_reason() -> None:
    config = replace(CONFIG, folds=20)
    result = evaluated(example_set(), config)

    printed = "\n".join(evaluation_lines(result, config, PROVENANCE, completeness()))

    assert "not fitted: training holds" in printed
