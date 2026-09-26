"""Configurations A to D, the examples they fit on, and the cold-start route.

Three claims from Stage 17's gate live here:

* **one config interface** — A to D are one key in ``model.toml``, and each
  chooses a fixed set of feature groups; B's model is A's, and priority
  weights its objective, never its inputs (D-160);
* **cold start** — the roadmap's four cases: a new station takes the
  geometry-only route, and an unseen satellite, a station with no interference
  history and one with no health history all get finite features at their
  priors (D-161);
* **whose examples** — our stations' usable passes and the archive's matched
  receptions, apart, with simulated passes counted and never used (D-078,
  D-156).

Reference: docs/DECISIONS.md D-078, D-156, D-160, D-161.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from meridian.datasets.canonical import canonical_bytes
from meridian.datasets.evaluation import build_evaluation_dataset
from meridian.datasets.label_config import LabelConfig
from meridian.datasets.label_rows import read_labels
from meridian.datasets.labels import LabelledPass
from meridian.datasets.publish import read_directory
from meridian.datasets.snapshot_rows import parse_rows
from meridian.prediction.configurations import (
    CONFIGURATIONS,
    FALLBACK,
    GROUPS,
    objective,
    route,
)
from meridian.prediction.examples import archive_examples, own_examples
from meridian.prediction.feature_rows import (
    FeatureRows,
    PassGeometry,
    Reading,
    read_feature_rows,
)
from meridian.prediction.features import FEATURES
from meridian.prediction.model_config import (
    ModelConfig,
    ModelConfigError,
    config_from_parameters,
    load_model_config,
    model_config_sha256,
    parse_model_config,
)

REPO = Path(__file__).resolve().parents[2]
CREATED = datetime(2026, 9, 23, 7, 0, tzinfo=UTC)
SINCE = datetime(2026, 9, 1, tzinfo=UTC)
DAY0 = datetime(2026, 9, 1, 6, 0, tzinfo=UTC)
MARGIN_S = 86_400


# --- the configuration file -----------------------------------------------------


def test_the_example_file_documents_the_defaults() -> None:
    assert load_model_config(REPO / "deploy" / "model.toml.example") == ModelConfig()


@pytest.mark.parametrize(
    ("text", "refusal"),
    [
        ('configuraton = "A"', "unknown model settings \\['configuraton'\\]"),
        ('configuration = "E"', "configuration must be one of"),
        ('population = "both"', "population must be one of"),
        ('population = "archive"', "configuration D cannot be fitted on the archive"),
        (
            'configuration = "B"\npopulation = "archive"',
            "its model is A's and priority weights only the scheduler's objective",
        ),
        ("min_station_history = true", "must be a whole number"),
        ("min_station_history = -1", "outside 0..10000"),
        ("configuration = ", "not TOML"),
        ("train_until = 2026-09-01T00:00:00", "with a UTC offset"),
        ("train_until = 2026-09-01", "with a UTC offset"),
        (
            "train_until = 2026-10-01T00:00:00Z\nvalidate_until = 2026-09-01T00:00:00Z",
            "is not before validate_until",
        ),
        ("inverse_regularisation = 0.0", "must be a positive number"),
        ("inverse_regularisation = inf", "must be a positive number"),
        ('weighting = "propensity"', "weighting must be one of"),
        ("seed = -1", "seed = -1 is outside"),
        ("seed = 1.5", "seed must be a whole number"),
        ("folds = -1", "folds = -1 is outside"),
        ("folds = 21", "folds = 21 is outside"),
        ("folds = 1.5", "folds must be a whole number"),
        ("folds = true", "folds must be a whole number"),
    ],
)
def test_a_setting_that_cannot_be_obeyed_is_refused_by_name(
    text: str, refusal: str
) -> None:
    with pytest.raises(ModelConfigError, match=refusal):
        parse_model_config(text)


def test_the_archive_can_be_fitted_under_a() -> None:
    config = parse_model_config('configuration = "A"\npopulation = "archive"')

    assert config.population == "archive"


def test_the_hash_is_of_the_values_not_the_file() -> None:
    one = parse_model_config('configuration = "C"\nmin_station_history = 5')
    other = parse_model_config(
        '# a comment\nmin_station_history = 5\nconfiguration = "C"'
    )

    assert model_config_sha256(one) == model_config_sha256(other)
    assert model_config_sha256(one) != model_config_sha256(
        replace(one, population="own", configuration="D")
    )


def test_the_split_dates_are_read_as_utc_instants() -> None:
    config = parse_model_config(
        "train_until = 2027-01-01T00:00:00Z\nvalidate_until = 2027-02-01T05:30:00+05:30"
    )

    assert config.train_until == datetime(2027, 1, 1, tzinfo=UTC)
    assert config.validate_until == datetime(2027, 2, 1, tzinfo=UTC)
    assert model_config_sha256(config) != model_config_sha256(ModelConfig())


def test_a_manifest_gives_back_the_configuration_it_recorded() -> None:
    config = parse_model_config(
        'configuration = "C"\nfolds = 0\nseed = 9\nweighting = "ipw"\n'
        "train_until = 2027-01-01T00:00:00Z\nvalidate_until = 2027-02-01T00:00:00Z"
    )
    recorded = json.loads(canonical_bytes(config.parameters()))

    again = config_from_parameters(recorded)

    assert again == config
    assert model_config_sha256(again) == model_config_sha256(config)


@pytest.mark.parametrize(
    ("parameters", "refusal"),
    [
        ({"learning_rate": 0.1}, "unknown model settings"),
        ({"train_until": "last tuesday"}, "not an ISO-8601 time"),
        ({"folds": 99}, "folds = 99 is outside"),
    ],
)
def test_a_recorded_configuration_is_refused_as_a_file_would_be(
    parameters: dict[str, object], refusal: str
) -> None:
    with pytest.raises(ModelConfigError, match=refusal):
        config_from_parameters(parameters)


def test_a_file_that_is_not_there_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ModelConfigError, match="cannot read"):
        load_model_config(tmp_path / "absent.toml")


# --- A to D ----------------------------------------------------------------------


def test_every_configuration_is_reached_through_the_one_key() -> None:
    for name in ("A", "B", "C", "D"):
        config = parse_model_config(f'configuration = "{name}"')
        assert CONFIGURATIONS[config.configuration].name == name


def test_a_reads_elevation_and_nothing_else() -> None:
    assert CONFIGURATIONS["A"].features == ("max_elevation_deg",)


def test_b_is_a_s_model_with_priority_on_the_objective() -> None:
    a, b = CONFIGURATIONS["A"], CONFIGURATIONS["B"]

    assert b.features == a.features
    assert "priority" not in " ".join(b.features)
    assert objective(b, 0.4, 2.0) == pytest.approx(0.8)
    assert objective(a, 0.4, 2.0) == pytest.approx(0.4)


def test_c_reads_only_our_features() -> None:
    ours = {one.name for one in FEATURES if one.group == "ours"}

    assert set(CONFIGURATIONS["C"].features) == ours
    assert "max_elevation_deg" not in CONFIGURATIONS["C"].features
    assert "duration_min" not in CONFIGURATIONS["C"].features


def test_d_reads_everything() -> None:
    assert CONFIGURATIONS["D"].features == tuple(one.name for one in FEATURES)
    assert set(CONFIGURATIONS["D"].groups) == set(GROUPS)


def test_the_public_conditions_group_is_named_and_empty() -> None:
    """Stage 31 fills it; until then D-without-conditions is D."""
    assert "conditions" in GROUPS
    assert not [one for one in FEATURES if one.group == "conditions"]


def test_every_feature_s_group_is_a_known_group() -> None:
    assert {one.group for one in FEATURES} <= set(GROUPS)


# --- cold start: the route ---------------------------------------------------------


def test_a_new_station_takes_the_geometry_route() -> None:
    taken = route(CONFIGURATIONS["D"], station_history=0, min_station_history=20)

    assert taken.path == "geometry_fallback"
    assert "new station" in taken.reason


def test_a_station_short_of_history_says_how_short() -> None:
    taken = route(CONFIGURATIONS["C"], station_history=7, min_station_history=20)

    assert taken.path == "geometry_fallback"
    assert "7 settled outcomes, below min_station_history 20" in taken.reason


def test_a_station_with_enough_history_takes_the_configured_route() -> None:
    assert route(CONFIGURATIONS["D"], 20, 20).path == "configured"


@pytest.mark.parametrize("name", ["A", "B"])
def test_a_configuration_without_history_never_falls_back(name: str) -> None:
    assert route(CONFIGURATIONS[name], 0, 20).path == "configured"


def test_the_fallback_reads_the_orbit_and_nothing_of_the_station() -> None:
    assert {one.group for one in FEATURES if one.name in FALLBACK.features} == {
        "elevation",
        "geometry",
    }
    assert not FALLBACK.reads_history


# --- cold start: the features, the roadmap's four cases --------------------------


def labelled(pass_id: int, day: int, **fields: Any) -> LabelledPass:
    aos = DAY0 + timedelta(days=day)
    return replace(
        LabelledPass(
            pass_id=pass_id,
            pass_ids=(pass_id,),
            station_id="st_a",
            satellite_id="norad:57166",
            aos=aos,
            los=aos + timedelta(minutes=11),
            label="successful_reception",
            exclusion_reason=None,
            source_outcome="decoded",
            listening_confirmed=True,
            scheduled_by=("A",),
            simulated=False,
        ),
        **fields,
    )


def rows_for(passes: list[LabelledPass], readings: dict[int, Any]) -> FeatureRows:
    return FeatureRows(
        geometry={
            one.pass_id: PassGeometry(
                aos=one.aos,
                computed_at=one.aos - timedelta(hours=1),
                max_elevation_deg=40.0,
                aos_azimuth_deg=10.0,
                los_azimuth_deg=190.0,
                element_set_epoch=one.aos - timedelta(hours=6),
                track=None,
            )
            for one in passes
        },
        bands={"norad:57166": "vhf", "norad:25544": "vhf"},
        longitudes={"st_a": 77.6},
        readings=readings,
    )


def cold(target: LabelledPass, history: list[LabelledPass]) -> dict[str, float]:
    """The target's features, with ``history`` before it and no readings."""
    passes = [*history, target]
    found = own_examples(passes, rows_for(passes, {}), settle_margin_s=MARGIN_S)
    (example,) = [one for one in found.examples if one.aos == target.aos]
    assert all(math.isfinite(value) for value in example.features.values())
    return dict(example.features)


def test_cold_start_a_new_station() -> None:
    features = cold(labelled(1, 0, station_id="st_new"), [])

    assert features["station_decode_rate"] == 0.5
    assert features["station_decode_n"] == 0.0


def test_cold_start_an_unseen_satellite() -> None:
    features = cold(
        labelled(9, 10, satellite_id="norad:25544"),
        [labelled(n, n) for n in range(5)],
    )

    assert features["station_decode_n"] == 5.0
    assert features["satellite_decode_rate"] == 0.5
    assert features["satellite_decode_n"] == 0.0


def test_cold_start_missing_interference_history() -> None:
    """Five settled passes, none of them with a noise floor reported."""
    features = cold(labelled(9, 10), [labelled(n, n) for n in range(5)])

    assert features["interference_db"] == 0.0
    assert features["interference_n"] == 0.0
    assert features["horizon_n"] == 0.0


def test_cold_start_missing_health_history() -> None:
    features = cold(labelled(1, 0), [])

    assert features["station_availability"] == 0.5
    assert features["station_availability_n"] == 0.0


def test_an_example_carries_its_station_s_settled_record() -> None:
    passes = [labelled(n, n) for n in range(6)]

    found = own_examples(passes, rows_for(passes, {}), settle_margin_s=MARGIN_S)

    assert [one.station_history for one in found.examples] == [0, 0, 1, 2, 3, 4]


def test_a_reading_with_no_noise_floor_is_not_interference_history() -> None:
    passes = [labelled(n, n) for n in range(3)]
    readings = {
        0: (
            Reading(
                assignment_id="as_0",
                outcome="decoded",
                first_detection_at=None,
                noise_floor_dbfs=None,
                simulated=False,
            ),
        )
    }

    found = own_examples(passes, rows_for(passes, readings), settle_margin_s=MARGIN_S)

    assert found.examples[-1].features["interference_n"] == 0.0


# --- whose examples ---------------------------------------------------------------


def test_our_examples_are_measured_yield_labels_with_simulated_counted(
    raw_snapshot: Any, world: Any, datasets_root: Path
) -> None:
    raw = read_directory(raw_snapshot(world))
    dataset = build_evaluation_dataset(
        raw, LabelConfig(), root=datasets_root, created_at=CREATED
    )
    labels = read_labels(read_directory(dataset.path).files)

    found = own_examples(labels, read_feature_rows(raw.files), settle_margin_s=MARGIN_S)

    assert [(one.station_id, one.positive) for one in found.examples] == [
        ("st_a", True),
        ("st_b", False),
    ]
    assert found.simulated == 1
    assert {one.population for one in found.examples} == {"own"}


def test_archive_examples_are_matched_receptions_with_an_outcome(
    raw_snapshot: Any, archive_world: Any
) -> None:
    """Decoded, no_data and unknown: two examples, one positive, peak only."""
    rows = parse_rows(read_directory(raw_snapshot(archive_world)).files)

    found = archive_examples(rows, tolerance_s=120, since=SINCE)

    assert [one.positive for one in found.examples] == [True, False]
    assert {one.station_id for one in found.examples} == {"archive:7"}
    assert {tuple(one.features) for one in found.examples} == {("max_elevation_deg",)}
    assert found.simulated == 0


def test_an_archive_pass_before_since_is_no_example(
    raw_snapshot: Any, archive_world: Any
) -> None:
    rows = parse_rows(read_directory(raw_snapshot(archive_world)).files)

    later = archive_examples(
        rows, tolerance_s=120, since=datetime(2026, 9, 10, 3, tzinfo=UTC)
    )

    assert [one.positive for one in later.examples] == [False]
