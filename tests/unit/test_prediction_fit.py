"""Splits, fitting and scoring — temporal, reproducible, and scored without numpy.

Four claims from Stage 17's gate live here:

* **temporal splits** — a pass is placed by its ``aos`` and nothing else, and
  no outcome after ``train_until`` reaches the coefficients (D-162,
  ``CLAUDE.md`` rule 6);
* **refusals with counts** — too few examples, one outcome only, or every
  usable pass simulated, each refused with what was found (D-078, D-156);
* **the scorer is the model** — plain Python over ``model.json`` gives what
  scikit-learn gives, within the rounding D-163 states;
* **a model is a published directory** — the same fit names the same
  directory, and a changed byte is refused (D-163).

Every model here is fitted on examples built in this file. No measured data
exists yet, and the simulator's are never fitted on.

Reference: docs/DECISIONS.md D-078, D-155, D-156, D-161, D-162, D-163.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from sklearn.linear_model import LogisticRegression

from meridian.datasets.canonical import canonical_bytes, canonical_line
from meridian.datasets.evaluation import build_evaluation_dataset
from meridian.datasets.label_config import LabelConfig
from meridian.datasets.manifest import MalformedManifestError, Manifest, content_sha256
from meridian.datasets.publish import DamagedSnapshotError, read_directory
from meridian.prediction.configurations import CONFIGURATIONS, FALLBACK
from meridian.prediction.examples import Example, ExampleSet, weighted
from meridian.prediction.features import FEATURES
from meridian.prediction.fit import ModelFitError, fit_model, rounded
from meridian.prediction.model_config import ModelConfig
from meridian.prediction.model_files import MODEL_FILE, publish_model, read_model
from meridian.prediction.score import (
    Linear,
    MalformedModelError,
    parse_model,
    predict,
    sigmoid,
)
from meridian.prediction.splits import SplitError, rolling_origin, temporal_split

DAY0 = datetime(2026, 1, 1, 6, 0, tzinfo=UTC)
TRAIN_UNTIL = DAY0 + timedelta(days=60)
VALIDATE_UNTIL = DAY0 + timedelta(days=80)
AS_OF = DAY0 + timedelta(days=100)
CREATED = datetime(2026, 9, 24, 7, 0, tzinfo=UTC)
CONFIG = ModelConfig(
    configuration="D",
    min_station_history=5,
    train_until=TRAIN_UNTIL,
    validate_until=VALIDATE_UNTIL,
)
PER_DAY = 2


def example(n: int, rng: random.Random, **fields: Any) -> Example:
    """Pass ``n``: two a day, decoding more often the higher it climbs."""
    features = {one.name: rng.uniform(-1.0, 1.0) for one in FEATURES}
    features["max_elevation_deg"] = rng.uniform(5.0, 85.0)
    chance = sigmoid((features["max_elevation_deg"] - 35.0) / 12.0)
    return replace(
        Example(
            population="own",
            station_id=f"st_{n % 3}",
            satellite_id="norad:57166",
            aos=DAY0 + timedelta(hours=24 * n / PER_DAY),
            positive=rng.random() < chance,
            features=features,
            station_history=n // 3,
        ),
        **fields,
    )


def example_set(count: int = 200, seed: int = 7) -> ExampleSet:
    rng = random.Random(seed)
    return ExampleSet("own", tuple(example(n, rng) for n in range(count)), 0)


# --- temporal splits ----------------------------------------------------------------


def test_a_split_places_each_example_by_date_whatever_the_order() -> None:
    found = example_set().examples
    shuffled = list(found)
    random.Random(1).shuffle(shuffled)

    one = temporal_split(
        found, train_until=TRAIN_UNTIL, validate_until=VALIDATE_UNTIL, as_of=AS_OF
    )
    other = temporal_split(
        shuffled, train_until=TRAIN_UNTIL, validate_until=VALIDATE_UNTIL, as_of=AS_OF
    )

    assert one == other
    assert all(ex.aos < TRAIN_UNTIL for ex in one.train)
    assert all(TRAIN_UNTIL <= ex.aos < VALIDATE_UNTIL for ex in one.validate)
    assert all(ex.aos >= VALIDATE_UNTIL for ex in one.test)
    assert len(one.train) + len(one.validate) + len(one.test) == len(found)


def test_a_pass_rising_at_train_until_is_not_training() -> None:
    rng = random.Random(0)
    at = replace(example(0, rng), aos=TRAIN_UNTIL)

    split = temporal_split(
        [at], train_until=TRAIN_UNTIL, validate_until=VALIDATE_UNTIL, as_of=AS_OF
    )

    assert split.train == ()
    assert split.validate == (at,)


@pytest.mark.parametrize(
    ("train_until", "validate_until", "as_of"),
    [
        (VALIDATE_UNTIL, TRAIN_UNTIL, AS_OF),
        (TRAIN_UNTIL, TRAIN_UNTIL, AS_OF),
        (TRAIN_UNTIL, AS_OF + timedelta(days=1), AS_OF),
    ],
)
def test_dates_out_of_order_are_refused(
    train_until: datetime, validate_until: datetime, as_of: datetime
) -> None:
    with pytest.raises(SplitError, match="train_until < validate_until <= as_of"):
        temporal_split(
            [], train_until=train_until, validate_until=validate_until, as_of=as_of
        )


def test_an_example_after_as_of_is_refused() -> None:
    late = replace(example(0, random.Random(0)), aos=AS_OF)

    with pytest.raises(SplitError, match="1 examples rise at or after as_of"):
        temporal_split(
            [late], train_until=TRAIN_UNTIL, validate_until=VALIDATE_UNTIL, as_of=AS_OF
        )


def test_rolling_origin_folds_are_whole_splits_moving_forward() -> None:
    found = example_set().examples

    folds = rolling_origin(list(reversed(found)), until=VALIDATE_UNTIL, folds=3)

    assert len(folds) == 3
    assert folds[-1].as_of == VALIDATE_UNTIL
    for earlier, later in pairwise(folds):
        assert earlier.train_until < later.train_until
        assert earlier.validate_until == later.train_until
        assert earlier.as_of == later.validate_until
        assert len(earlier.train) < len(later.train)
    for fold in folds:
        assert fold.train
        assert fold.validate
        assert fold.test
        assert all(ex.aos < fold.train_until for ex in fold.train)
        assert all(
            fold.train_until <= ex.aos < fold.validate_until for ex in fold.validate
        )
        assert all(fold.validate_until <= ex.aos < fold.as_of for ex in fold.test)


def test_no_fold_reads_the_test_span() -> None:
    found = example_set().examples

    folds = rolling_origin(found, until=VALIDATE_UNTIL, folds=4)
    held = [ex for fold in folds for ex in (*fold.train, *fold.validate, *fold.test)]

    assert held
    assert all(ex.aos < VALIDATE_UNTIL for ex in held)


def test_folds_do_not_depend_on_the_order_examples_arrive_in() -> None:
    found = list(example_set().examples)
    shuffled = list(found)
    random.Random(3).shuffle(shuffled)

    assert rolling_origin(found, until=VALIDATE_UNTIL, folds=3) == rolling_origin(
        shuffled, until=VALIDATE_UNTIL, folds=3
    )


def test_rolling_origin_refuses_no_folds_and_has_none_for_one_instant() -> None:
    one = example(0, random.Random(0))

    with pytest.raises(SplitError, match="at least one fold"):
        rolling_origin([one], until=VALIDATE_UNTIL, folds=0)
    assert rolling_origin([one], until=VALIDATE_UNTIL, folds=2) == ()


def test_passes_after_until_do_not_make_a_span_to_fold() -> None:
    """One instant before ``until`` is nothing to cut, however much follows."""
    rng = random.Random(0)
    later = [
        replace(example(n, rng), aos=VALIDATE_UNTIL + timedelta(hours=n))
        for n in range(10)
    ]

    assert (
        rolling_origin([example(0, rng), *later], until=VALIDATE_UNTIL, folds=2) == ()
    )


# --- refusals, with the counts ------------------------------------------------------


def test_a_fit_without_split_dates_is_refused() -> None:
    with pytest.raises(ModelFitError, match="names no train_until"):
        fit_model(example_set(), ModelConfig(), as_of=AS_OF)


def test_a_fit_on_another_population_is_refused() -> None:
    archive = replace(example_set(), population="archive")

    with pytest.raises(ModelFitError, match="archive population"):
        fit_model(archive, CONFIG, as_of=AS_OF)


def test_a_dataset_of_simulated_passes_is_refused_as_simulated() -> None:
    with pytest.raises(ModelFitError, match="all 40 usable passes are simulated"):
        fit_model(ExampleSet("own", (), 40), CONFIG, as_of=AS_OF)


def test_too_few_training_examples_are_refused_with_the_count() -> None:
    found = replace(example_set(), examples=example_set().examples[:12], simulated=3)

    with pytest.raises(
        ModelFitError, match=r"training holds 12 examples, \d+ of them decoded"
    ) as refused:
        fit_model(found, CONFIG, as_of=AS_OF)
    assert "(3 simulated passes were not counted)" in str(refused.value)


def test_a_validation_span_with_one_outcome_is_refused() -> None:
    found = example_set()
    one_sided = tuple(
        replace(ex, positive=True) if TRAIN_UNTIL <= ex.aos < VALIDATE_UNTIL else ex
        for ex in found.examples
    )

    with pytest.raises(ModelFitError, match="validation holds 40 examples, 40 of"):
        fit_model(replace(found, examples=one_sided), CONFIG, as_of=AS_OF)


# --- the fit ------------------------------------------------------------------------


def test_the_configured_model_reads_its_configuration_s_features() -> None:
    fitted = fit_model(example_set(), CONFIG, as_of=AS_OF)
    model = parse_model(canonical_bytes(fitted.document))

    assert model.configured.features == CONFIGURATIONS["D"].features
    assert model.fallback is not None
    assert model.fallback.features == FALLBACK.features


def test_a_configuration_reading_no_history_has_no_fallback() -> None:
    fitted = fit_model(example_set(), replace(CONFIG, configuration="A"), as_of=AS_OF)

    assert fitted.document["fallback"] is None
    assert parse_model(canonical_bytes(fitted.document)).configured.features == (
        "max_elevation_deg",
    )


def test_b_fits_a_s_model() -> None:
    a = fit_model(example_set(), replace(CONFIG, configuration="A"), as_of=AS_OF)
    b = fit_model(example_set(), replace(CONFIG, configuration="B"), as_of=AS_OF)

    assert b.document["configured"] == a.document["configured"]
    assert b.document["weighted_by_priority"] is True


def test_two_fits_give_the_same_bytes() -> None:
    one = fit_model(example_set(), CONFIG, as_of=AS_OF)
    two = fit_model(example_set(), CONFIG, as_of=AS_OF)

    assert canonical_bytes(one.document) == canonical_bytes(two.document)


def test_every_stored_number_has_twelve_significant_figures() -> None:
    configured = fit_model(example_set(), CONFIG, as_of=AS_OF).document["configured"]

    assert isinstance(configured, dict)
    numbers = [
        *configured["mean"],
        *configured["scale"],
        *configured["coefficients"],
        configured["intercept"],
        configured["calibration"]["a"],
        configured["calibration"]["b"],
    ]
    assert all(rounded(one) == one for one in numbers)


def test_no_outcome_after_train_until_reaches_the_coefficients() -> None:
    """Flip every later outcome: the coefficients cannot move, the map can."""
    found = example_set()
    flipped = replace(
        found,
        examples=tuple(
            replace(ex, positive=not ex.positive) if ex.aos >= TRAIN_UNTIL else ex
            for ex in found.examples
        ),
    )

    one = fit_model(found, CONFIG, as_of=AS_OF).document["configured"]
    two = fit_model(flipped, CONFIG, as_of=AS_OF).document["configured"]

    assert isinstance(one, dict)
    assert isinstance(two, dict)
    assert one["coefficients"] == two["coefficients"]
    assert one["calibration"] != two["calibration"]


def test_an_earlier_outcome_does_move_the_coefficients() -> None:
    """The positive control: the leak test can fail."""
    found = example_set()
    flipped = replace(
        found,
        examples=tuple(
            replace(ex, positive=not ex.positive)
            if ex.aos < DAY0 + timedelta(20)
            else ex
            for ex in found.examples
        ),
    )

    one = fit_model(found, CONFIG, as_of=AS_OF).document["configured"]
    two = fit_model(flipped, CONFIG, as_of=AS_OF).document["configured"]

    assert isinstance(one, dict)
    assert isinstance(two, dict)
    assert one["coefficients"] != two["coefficients"]


def test_the_model_learns_that_elevation_helps() -> None:
    model = parse_model(
        canonical_bytes(
            fit_model(
                example_set(), replace(CONFIG, configuration="A"), as_of=AS_OF
            ).document
        )
    )

    assert model.configured.coefficients[0] > 0
    assert model.configured.calibration_a > 0


def test_weights_change_the_fit_and_unweighted_examples_are_counted() -> None:
    found = example_set()
    rows = [
        {
            "population": ex.population,
            "station": ex.station_id,
            "satellite_id": ex.satellite_id,
            "aos": ex.aos,
            "weight": None if n % 10 == 0 else 1.0 + (n % 4),
        }
        for n, ex in enumerate(found.examples)
    ]
    propensities = b"".join(canonical_line(row) for row in rows)

    heavy = weighted(found, propensities)
    level = replace(
        heavy, examples=tuple(replace(ex, weight=1.0) for ex in heavy.examples)
    )
    one = fit_model(level, CONFIG, as_of=AS_OF)
    two = fit_model(heavy, replace(CONFIG, weighting="ipw"), as_of=AS_OF)

    assert heavy.without_weight == 20
    assert {ex.weight for ex in heavy.examples} == {1.0, 2.0, 3.0, 4.0}
    assert two.counts["examples.without_weight"] == 20
    level_fit, heavy_fit = one.document["configured"], two.document["configured"]
    assert isinstance(level_fit, dict)
    assert isinstance(heavy_fit, dict)
    assert level_fit["coefficients"] != heavy_fit["coefficients"]
    assert level_fit["calibration"] != heavy_fit["calibration"]


def test_validation_weights_move_the_calibration_alone() -> None:
    found = example_set()
    reweighted = replace(
        found,
        examples=tuple(
            replace(ex, weight=1.0 + 3.0 * ex.positive)
            if TRAIN_UNTIL <= ex.aos < VALIDATE_UNTIL
            else ex
            for ex in found.examples
        ),
    )

    one = fit_model(found, CONFIG, as_of=AS_OF).document["configured"]
    two = fit_model(reweighted, CONFIG, as_of=AS_OF).document["configured"]

    assert isinstance(one, dict)
    assert isinstance(two, dict)
    assert one["coefficients"] == two["coefficients"]
    assert two["calibration"]["b"] > one["calibration"]["b"]


def test_the_calibration_is_platt_s_weighted_fit_on_validation() -> None:
    """Refit Platt's soft-label map from the stored model and compare."""
    found = example_set()
    reweighted = replace(
        found,
        examples=tuple(
            replace(ex, weight=1.0 + (n % 5)) for n, ex in enumerate(found.examples)
        ),
    )
    fitted = fit_model(reweighted, CONFIG, as_of=AS_OF)
    configured = parse_model(canonical_bytes(fitted.document)).configured
    span = fitted.split.validate
    logits = np.array([configured.logit(ex.features) for ex in span])
    y = np.array([int(ex.positive) for ex in span])
    w = np.array([ex.weight for ex in span])
    up, down = w[y == 1].sum(), w[y == 0].sum()
    target = np.where(y == 1, (up + 1) / (up + 2), 1 / (down + 2))
    reference = LogisticRegression(C=np.inf, max_iter=10_000, tol=1e-10).fit(
        np.concatenate([logits, logits]).reshape(-1, 1),
        np.concatenate([np.ones_like(y), np.zeros_like(y)]),
        sample_weight=np.concatenate([w * target, w * (1 - target)]),
    )

    assert configured.calibration_a == pytest.approx(reference.coef_[0][0], abs=1e-6)
    assert configured.calibration_b == pytest.approx(reference.intercept_[0], abs=1e-6)


def test_a_stronger_penalty_shrinks_the_coefficients() -> None:
    loose = fit_model(example_set(), CONFIG, as_of=AS_OF)
    tight = fit_model(
        example_set(), replace(CONFIG, inverse_regularisation=0.01), as_of=AS_OF
    )

    def size(fitted: Any) -> float:
        return sum(one**2 for one in fitted.document["configured"]["coefficients"])

    assert size(tight) < size(loose) / 4


def test_the_counts_name_each_span() -> None:
    counts = fit_model(example_set(), CONFIG, as_of=AS_OF).counts

    assert counts["examples.train"] == 120
    assert counts["examples.validate"] == 40
    assert counts["examples.test"] == 40
    assert 0 < counts["examples.train_decoded"] < 120


# --- the scorer is the model --------------------------------------------------------


def test_the_scorer_gives_what_scikit_learn_gives() -> None:
    """Refit in scikit-learn from the stored scaler; compare every probability."""
    found = example_set()
    fitted = fit_model(found, CONFIG, as_of=AS_OF)
    configured = parse_model(canonical_bytes(fitted.document)).configured
    names = configured.features
    mean, scale = np.array(configured.mean), np.array(configured.scale)
    train = fitted.split.train
    x = np.array([[ex.features[n] for n in names] for ex in train])
    y = np.array([int(ex.positive) for ex in train])
    reference = LogisticRegression(C=1.0, random_state=0, max_iter=10_000, tol=1e-10)
    reference.fit((x - mean) / scale, y)
    identity = replace(configured, calibration_a=1.0, calibration_b=0.0)

    for ex in found.examples:
        row = np.array([[ex.features[n] for n in names]])
        expected = reference.predict_proba((row - mean) / scale)[0][1]
        assert identity.probability(ex.features) == pytest.approx(expected, abs=1e-9)


def test_every_probability_is_finite_and_in_the_unit_interval() -> None:
    model = parse_model(
        canonical_bytes(fit_model(example_set(), CONFIG, as_of=AS_OF).document)
    )
    extreme = {one.name: 1e6 for one in FEATURES}

    scored = [
        predict(model, ex.features, ex.station_history).probability
        for ex in example_set(seed=99).examples
    ]
    scored.append(predict(model, extreme, 100).probability)
    scored.append(predict(model, {k: -v for k, v in extreme.items()}, 0).probability)

    assert all(math.isfinite(p) and 0.0 <= p <= 1.0 for p in scored)


def test_sigmoid_does_not_overflow() -> None:
    assert sigmoid(1e4) == 1.0
    assert sigmoid(-1e4) == 0.0
    assert sigmoid(0.0) == 0.5


def test_a_station_short_of_history_is_scored_by_the_fallback() -> None:
    model = parse_model(
        canonical_bytes(fit_model(example_set(), CONFIG, as_of=AS_OF).document)
    )
    features = example_set().examples[0].features
    assert model.fallback is not None

    young = predict(model, features, station_history=2)
    settled = predict(model, features, station_history=5)

    assert (young.path, settled.path) == ("geometry_fallback", "configured")
    assert "2 settled outcomes, below min_station_history 5" in young.reason
    assert young.probability == model.fallback.probability(features)
    assert settled.probability == model.configured.probability(features)


def test_a_feature_the_pass_lacks_is_named() -> None:
    model = parse_model(
        canonical_bytes(
            fit_model(
                example_set(), replace(CONFIG, configuration="A"), as_of=AS_OF
            ).document
        )
    )

    with pytest.raises(MalformedModelError, match="'max_elevation_deg'"):
        predict(model, {}, 0)


def _document() -> dict[str, Any]:
    fitted = fit_model(example_set(), CONFIG, as_of=AS_OF)
    loaded: dict[str, Any] = json.loads(canonical_bytes(fitted.document))
    return loaded


@pytest.mark.parametrize(
    ("damage", "refusal"),
    [
        (lambda d: d.update(model_format=2), "unknown model format 2"),
        (lambda d: d.pop("configured"), "no 'configured'"),
        (lambda d: d["configured"]["mean"].pop(), "mean has 25 values for 26"),
        (lambda d: d.update(fallback=None), "fallback exactly when"),
        (lambda d: d["configured"]["scale"].__setitem__(0, 0.0), "not positive"),
        (lambda d: d["configured"].update(intercept="0"), "intercept is '0'"),
        (lambda d: d.update(min_station_history=True), "not a whole number"),
    ],
)
def test_a_damaged_model_file_is_refused_by_name(damage: Any, refusal: str) -> None:
    document = _document()
    damage(document)

    with pytest.raises(MalformedModelError, match=refusal):
        parse_model(json.dumps(document).encode())


def test_a_model_holding_nan_is_refused() -> None:
    document = _document()
    document["configured"]["intercept"] = math.nan

    with pytest.raises(MalformedModelError, match="not finite"):
        parse_model(json.dumps(document).encode())


def test_a_model_file_that_is_not_json_is_refused() -> None:
    with pytest.raises(MalformedModelError, match="not readable JSON"):
        parse_model(b"\xff")


def test_the_scorer_reads_features_by_name_not_position() -> None:
    linear = Linear(("a", "b"), (0.0, 0.0), (1.0, 1.0), (1.0, -1.0), 0.0, 1.0, 0.0)

    assert linear.logit({"b": 2.0, "a": 5.0}) == 3.0


def test_the_calibration_map_is_applied_to_the_logit() -> None:
    linear = Linear(("a",), (1.0,), (2.0,), (3.0,), 0.5, 2.0, -1.0)
    logit = 0.5 + 3.0 * (5.0 - 1.0) / 2.0

    assert linear.logit({"a": 5.0}) == logit
    assert linear.probability({"a": 5.0}) == sigmoid(2.0 * logit - 1.0)


# --- the published directory -------------------------------------------------------


def test_a_model_is_published_once_under_its_hash(
    raw_snapshot: Any, world: Any, datasets_root: Path
) -> None:
    raw = read_directory(raw_snapshot(world))
    dataset = read_directory(
        build_evaluation_dataset(
            raw, LabelConfig(), root=datasets_root, created_at=CREATED
        ).path
    )
    fitted = fit_model(example_set(), CONFIG, as_of=AS_OF)

    first = publish_model(
        fitted, dataset=dataset, config=CONFIG, root=datasets_root, created_at=CREATED
    )
    again = publish_model(
        fitted,
        dataset=dataset,
        config=CONFIG,
        root=datasets_root,
        created_at=CREATED + timedelta(hours=1),
    )
    back = read_model(first.path)

    assert first.written
    assert not again.written
    assert again.path == first.path
    assert back.directory.manifest.kind == "model"
    assert back.directory.manifest.derived_from == content_sha256(dataset.manifest)
    stored = json.loads(back.directory.files[MODEL_FILE])
    assert stored["dataset_sha256"] == content_sha256(dataset.manifest).hex()
    features = example_set().examples[0].features
    assert back.model.configured.probability(features) == parse_model(
        canonical_bytes(fitted.document)
    ).configured.probability(features)


def test_another_configuration_is_another_model(
    raw_snapshot: Any, world: Any, datasets_root: Path
) -> None:
    raw = read_directory(raw_snapshot(world))
    dataset = read_directory(
        build_evaluation_dataset(
            raw, LabelConfig(), root=datasets_root, created_at=CREATED
        ).path
    )
    other = replace(CONFIG, seed=1)

    one = publish_model(
        fit_model(example_set(), CONFIG, as_of=AS_OF),
        dataset=dataset,
        config=CONFIG,
        root=datasets_root,
        created_at=CREATED,
    )
    two = publish_model(
        fit_model(example_set(), other, as_of=AS_OF),
        dataset=dataset,
        config=other,
        root=datasets_root,
        created_at=CREATED,
    )

    assert one.path != two.path


def test_a_changed_model_file_is_refused(
    raw_snapshot: Any, world: Any, datasets_root: Path
) -> None:
    raw = read_directory(raw_snapshot(world))
    dataset = read_directory(
        build_evaluation_dataset(
            raw, LabelConfig(), root=datasets_root, created_at=CREATED
        ).path
    )
    published = publish_model(
        fit_model(example_set(), CONFIG, as_of=AS_OF),
        dataset=dataset,
        config=CONFIG,
        root=datasets_root,
        created_at=CREATED,
    )
    target = published.path / MODEL_FILE
    published.path.chmod(0o755)
    target.chmod(0o644)
    target.write_bytes(target.read_bytes().replace(b'"seed":0', b'"seed":9'))

    with pytest.raises(DamagedSnapshotError, match="does not match the digest"):
        read_model(published.path)


def test_a_dataset_is_not_a_model(
    raw_snapshot: Any, world: Any, datasets_root: Path
) -> None:
    raw = read_directory(raw_snapshot(world))
    dataset = build_evaluation_dataset(
        raw, LabelConfig(), root=datasets_root, created_at=CREATED
    )

    with pytest.raises(MalformedModelError, match="not a model"):
        read_model(dataset.path)


def test_a_model_manifest_must_name_its_inputs() -> None:
    with pytest.raises(MalformedManifestError, match="kind model names the directory"):
        Manifest(
            kind="model",
            schema_revision="0001",
            since=DAY0,
            as_of=AS_OF,
            files=(),
            created_at=CREATED,
        )
