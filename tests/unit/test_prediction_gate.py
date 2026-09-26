"""Stage 17's gate, end to end: snapshot, label, fit, score, report.

The roadmap states it as: *configurations A–D run from one config interface,
use temporal splits, handle cold start, and produce reproducible calibrated
probabilities.* Each clause is tested here through the commands an operator
runs — ``meridian snapshot label``, ``meridian model fit`` and ``meridian model
evaluate`` — over a raw snapshot built in this file, so every feature, label
and split is the pipeline's own and nothing is handed in.

The world is 21 days of one satellite over two stations, four passes a day
each, decoding more often the higher the pass climbs. A third station joins
on day 16, inside the test span, so cold start is met by the pipeline rather
than set up by hand. Its rows are written here and are not the simulator's;
no simulated row is fitted on (D-078).

**Each claim has its positive control**, since a test that cannot fail proves
nothing: an earlier outcome does move the model, an established station does
take the configured route, one changed outcome is another model, and the raw
logit is not a probability until the calibrated sigmoid makes it one.

Reference: docs/DECISIONS.md D-155 to D-164; docs/EVALUATION.md §3, §7, §8.
"""

from __future__ import annotations

import ast
import json
import math
import os
import random
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from meridian.cli import main
from meridian.datasets.publish import read_directory
from meridian.prediction.configurations import CONFIGURATIONS, FALLBACK
from meridian.prediction.lineage import config_of, dataset_of, examples_of, raw_of
from meridian.prediction.model_config import parse_model_config
from meridian.prediction.model_files import MODEL_FILE, read_model
from meridian.prediction.score import GEOMETRY_FALLBACK, Model, predict, sigmoid

SINCE = datetime(2026, 9, 1, tzinfo=UTC)
TRAIN_UNTIL = datetime(2026, 9, 11, tzinfo=UTC)
VALIDATE_UNTIL = datetime(2026, 9, 16, tzinfo=UTC)
DAYS = 21
NEW_STATION_FROM = 16
SATELLITE = "norad:57166"

REPO = Path(__file__).resolve().parents[2]
PREDICTION = REPO / "platform" / "src" / "meridian" / "prediction"

SETTINGS = (
    "min_station_history = 5\n"
    "folds = 2\n"
    "train_until = 2026-09-11T00:00:00Z\n"
    "validate_until = 2026-09-16T00:00:00Z\n"
)

FRESH = (
    sys.executable,
    "-c",
    "import sys; from meridian.cli import main; sys.exit(main(sys.argv[1:]))",
)
"""The ``meridian`` command in a new interpreter."""

Flip = Callable[[datetime], bool]


def never(_aos: datetime) -> bool:
    return False


def gate_world(flip: Flip = never) -> dict[str, Sequence[Mapping[str, object]]]:
    """The world, with every outcome whose pass ``flip`` names reversed.

    Outcomes are drawn before flipping, from one seeded generator, so a flip
    changes those passes and nothing else.
    """
    rng = random.Random(17)
    rows: dict[str, list[Mapping[str, object]]] = {
        "passes": [],
        "assignments": [],
        "observations": [],
    }
    stations = {"st_a": 0, "st_b": 1, "st_new": 2}
    pass_id = 0
    for day in range(DAYS):
        for slot in range(4):
            for station, offset in stations.items():
                if station == "st_new" and day < NEW_STATION_FROM:
                    continue
                pass_id += 1
                aos = SINCE + timedelta(days=day, hours=6 * slot + offset + 1)
                elevation = rng.uniform(5.0, 85.0)
                decoded = rng.random() < sigmoid((elevation - 35.0) / 10.0)
                predicted: dict[str, object] = {
                    "id": pass_id,
                    "satellite_id": SATELLITE,
                    "station_id": station,
                    "aos": aos,
                    "los": aos + timedelta(minutes=12),
                    "max_elevation_deg": elevation,
                    "aos_azimuth_deg": rng.uniform(0.0, 360.0),
                    "los_azimuth_deg": rng.uniform(0.0, 360.0),
                    "element_set_id": day,
                    "computed_at": aos - timedelta(hours=5),
                    "simulated": False,
                }
                _report(rows, predicted, decoded != flip(aos))
    return rows | {
        "element_sets": [
            {"id": day, "satellite_id": SATELLITE, "epoch": SINCE + timedelta(days=day)}
            for day in range(DAYS)
        ],
        "stations": [{"station_id": station, "lon_deg": 77.6} for station in stations],
        "transmitters": [
            {
                "id": 1,
                "satellite_id": SATELLITE,
                "centre_freq_hz": 137_900_000,
                "active": True,
                "deleted_at": None,
            }
        ],
    }


def _report(
    rows: dict[str, list[Mapping[str, object]]],
    predicted: dict[str, object],
    decoded: bool,
) -> None:
    """The pass, its assignment and the station's report on it."""
    number = predicted["id"]
    rows["passes"].append(predicted)
    rows["assignments"].append(
        {
            "assignment_id": f"as_{number}",
            "pass_id": number,
            "station_id": predicted["station_id"],
            "start_at": predicted["aos"],
            "end_at": predicted["los"],
            "decision": "scheduled",
            "state": "reported",
            "model_config": "A",
            "simulated": False,
        }
    )
    rows["observations"].append(
        {
            "assignment_id": f"as_{number}",
            "revision": 1,
            "outcome": "decoded" if decoded else "signal_no_decode",
            "first_detection_at": None,
            "noise_floor_dbfs": None,
            "simulated": False,
        }
    )


def held_under(raw: Path, root: Path) -> Path:
    """The raw snapshot where ``root`` keeps its own, copied there if need be.

    The fixture publishes under one root; a second root is a second machine,
    and a model's lineage is followed within the root it was fitted under.
    """
    home = root / "snapshots" / raw.name
    if home != raw and not home.exists():
        shutil.copytree(raw, home)
    return home


def printed_path(out: str) -> Path:
    first = out.splitlines()[0]
    return Path(first.split(": ", 1)[1].rsplit(" (", 1)[0])


class Pipeline:
    """The operator's commands, run in process against one datasets root."""

    def __init__(
        self, root: Path, raw_snapshot: Any, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self.root = root
        self.raw_snapshot = raw_snapshot
        self.capsys = capsys
        self.configs = 0

    def run(self, *args: str) -> str:
        self.capsys.readouterr()
        code = main([*args[:1], "--root", str(self.root), *args[1:]])
        captured = self.capsys.readouterr()
        assert code == 0, captured.err
        return captured.out

    def dataset(self, flip: Flip = never) -> Path:
        raw = held_under(self.raw_snapshot(gate_world(flip)), self.root)
        return printed_path(self.run("snapshot", "label", str(raw)))

    def fit(self, dataset: Path, settings: str) -> Path:
        self.configs += 1
        config = self.root.parent / f"{self.root.name}-model-{self.configs}.toml"
        config.write_text(settings, encoding="utf-8")
        return printed_path(
            self.run("model", "fit", str(dataset), "--config", str(config))
        )


@pytest.fixture
def pipeline(
    datasets_root: Path, raw_snapshot: Any, capsys: pytest.CaptureFixture[str]
) -> Pipeline:
    return Pipeline(datasets_root, raw_snapshot, capsys)


def configured(name: str) -> str:
    return f'configuration = "{name}"\n{SETTINGS}'


def document(model: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(read_directory(model).files[MODEL_FILE])
    return loaded


LEARNED_ON_TRAINING = ("features", "mean", "scale", "coefficients", "intercept")


def learned(model: Path, which: str) -> dict[str, Any]:
    """What training alone decides: everything but the calibration map."""
    block = document(model)[which]
    return {key: block[key] for key in LEARNED_ON_TRAINING}


# --- A to D from one interface ------------------------------------------------


def test_a_to_d_run_from_one_key(pipeline: Pipeline) -> None:
    """Four configuration files differing in one line; four models, each
    reading its configuration's features, each judged by the same command."""
    dataset = pipeline.dataset()
    texts = {name: configured(name) for name in CONFIGURATIONS}
    models = {name: pipeline.fit(dataset, text) for name, text in texts.items()}

    lines = [set(text.splitlines()) for text in texts.values()]
    assert len(set.union(*lines) - set.intersection(*lines)) == len(CONFIGURATIONS)
    for name, path in models.items():
        model = read_model(path).model
        assert model.configuration == name
        assert model.configured.features == CONFIGURATIONS[name].features
        assert (model.fallback is not None) == CONFIGURATIONS[name].reads_history
        report = pipeline.run("model", "evaluate", str(path))
        assert f"configuration      {name}:" in report
    assert len(set(models.values())) == len(CONFIGURATIONS)


def test_b_is_a_s_model_and_its_report_says_so(pipeline: Pipeline) -> None:
    dataset = pipeline.dataset()
    a = read_model(pipeline.fit(dataset, configured("A"))).model
    b_path = pipeline.fit(dataset, configured("B"))

    assert read_model(b_path).model.configured == a.configured
    assert "B's probabilities are A's" in pipeline.run("model", "evaluate", str(b_path))


def test_a_configuration_the_key_does_not_name_is_refused() -> None:
    """Positive control: the one key is checked, not taken on trust."""
    with pytest.raises(ValueError, match="configuration must be one of"):
        parse_model_config(configured("E"))


# --- temporal splits ----------------------------------------------------------


def test_no_outcome_after_train_until_reaches_what_training_learns(
    pipeline: Pipeline,
) -> None:
    """Every validation and test outcome reversed, in the raw snapshot itself:
    the scaler and coefficients of both models are unmoved."""
    original = pipeline.fit(pipeline.dataset(), configured("D"))
    reversed_later = pipeline.fit(
        pipeline.dataset(lambda aos: aos >= TRAIN_UNTIL), configured("D")
    )

    assert original != reversed_later
    for which in ("configured", "fallback"):
        assert learned(original, which) == learned(reversed_later, which)


def test_an_outcome_before_train_until_does_reach_it(pipeline: Pipeline) -> None:
    """Positive control: the comparison above can fail."""
    first_day = SINCE + timedelta(days=1)
    original = pipeline.fit(pipeline.dataset(), configured("D"))
    reversed_early = pipeline.fit(
        pipeline.dataset(lambda aos: aos < first_day), configured("D")
    )

    assert learned(original, "configured") != learned(reversed_early, "configured")


SHUFFLES = frozenset(
    {"shuffle", "train_test_split", "KFold", "ShuffleSplit", "permutation"}
)


def shuffling(path: Path) -> list[str]:
    """Every parameter, keyword, call or import that could reorder by chance."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.arg in SHUFFLES:
            found.append(f"parameter {node.arg}")
        elif isinstance(node, ast.keyword) and node.arg in SHUFFLES:
            found.append(f"keyword {node.arg}")
        elif isinstance(node, ast.Attribute) and node.attr in SHUFFLES:
            found.append(f"call {node.attr}")
        elif isinstance(node, ast.Name) and node.id in SHUFFLES:
            found.append(f"name {node.id}")
        elif isinstance(node, ast.alias) and node.name in SHUFFLES:
            found.append(f"import {node.name}")
    return found


def test_nothing_in_prediction_can_shuffle() -> None:
    """``CLAUDE.md`` rule 6, for every line: no split takes a shuffle."""
    paths = sorted(PREDICTION.rglob("*.py"))

    assert paths
    assert {path.name: shuffling(path) for path in paths if shuffling(path)} == {}


def test_the_shuffle_scan_would_notice_one(tmp_path: Path) -> None:
    offender = tmp_path / "offender.py"
    offender.write_text(
        "from sklearn.model_selection import train_test_split\n"
        "import random\n"
        "def split(rows, shuffle=True):\n"
        "    random.shuffle(rows)\n"
        "    return train_test_split(rows, shuffle=shuffle)\n",
        encoding="utf-8",
    )

    found = shuffling(offender)

    assert {one.split()[0] for one in found} == {
        "import",
        "parameter",
        "keyword",
        "call",
        "name",
    }


# --- cold start ---------------------------------------------------------------


def scored_examples(pipeline: Pipeline, model_path: Path) -> list[Any]:
    """Every example of the model's dataset, with the prediction it gets."""
    held = read_model(model_path)
    dataset = dataset_of(held.directory, root=pipeline.root)
    raw = raw_of(dataset, root=pipeline.root)
    found = examples_of(dataset, raw, config_of(held.directory))
    return [
        (one, predict(held.model, one.features, one.station_history))
        for one in found.examples.examples
    ]


def test_a_station_that_joins_late_is_scored_by_geometry_and_says_why(
    pipeline: Pipeline,
) -> None:
    model_path = pipeline.fit(pipeline.dataset(), configured("D"))

    scored = scored_examples(pipeline, model_path)
    newcomer = [(one, p) for one, p in scored if one.station_id == "st_new"]
    first, first_prediction = newcomer[0]

    assert first.station_history == 0
    assert first_prediction.path == GEOMETRY_FALLBACK
    assert first_prediction.reason == "a new station: no settled outcomes"
    assert any(p.path == "configured" for _, p in newcomer)
    report = pipeline.run("model", "evaluate", str(model_path))
    assert "geometry_fallback" in report
    fallback = read_model(model_path).model.fallback
    assert fallback is not None
    assert fallback.features == FALLBACK.features


def test_an_established_station_takes_the_configured_route(
    pipeline: Pipeline,
) -> None:
    """Positive control: the fallback is a route taken for a reason, not always."""
    model_path = pipeline.fit(pipeline.dataset(), configured("D"))

    late = [
        p
        for one, p in scored_examples(pipeline, model_path)
        if one.station_id == "st_a" and one.aos >= VALIDATE_UNTIL
    ]

    assert late
    assert {p.path for p in late} == {"configured"}


def test_a_configuration_reading_no_history_never_falls_back(
    pipeline: Pipeline,
) -> None:
    model_path = pipeline.fit(pipeline.dataset(), configured("A"))

    paths = {p.path for _, p in scored_examples(pipeline, model_path)}

    assert paths == {"configured"}


# --- reproducible -------------------------------------------------------------


def test_two_fits_name_one_model_with_the_network_refused(
    pipeline: Pipeline, no_network: Any, datasets_root: Path, raw_snapshot: Any
) -> None:
    """Twice into one root, once into another: one name, one ``model.json``."""
    first = pipeline.fit(pipeline.dataset(), configured("D"))
    again = pipeline.fit(pipeline.dataset(), configured("D"))
    elsewhere = Pipeline(datasets_root / "other", raw_snapshot, pipeline.capsys)
    other = elsewhere.fit(elsewhere.dataset(), configured("D"))

    assert no_network.attempts == []
    assert first == again
    assert first.name == other.name
    assert (first / MODEL_FILE).read_bytes() == (other / MODEL_FILE).read_bytes()


def test_the_model_does_not_depend_on_the_process(
    pipeline: Pipeline, datasets_root: Path
) -> None:
    """Two interpreters with different hash seeds label and fit: one model."""
    raw = pipeline.raw_snapshot(gate_world())
    config = datasets_root.parent / "model.toml"
    config.write_text(configured("D"), encoding="utf-8")
    names = []
    for seed, root in (("0", "p"), ("4471", "q")):
        where = str(datasets_root / root)
        copied = held_under(raw, datasets_root / root)
        env = os.environ | {"PYTHONHASHSEED": seed}
        labelled = subprocess.run(
            [*FRESH, "snapshot", "--root", where, "label", str(copied)],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
        dataset = str(printed_path(labelled.stdout))
        fitted = subprocess.run(
            [*FRESH, "model", "--root", where, "fit", dataset, "--config", str(config)],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
        names.append(printed_path(fitted.stdout).name)

    assert names[0] == names[1]


def test_the_report_is_the_same_twice(pipeline: Pipeline) -> None:
    model_path = pipeline.fit(pipeline.dataset(), configured("D"))

    first = pipeline.run("model", "evaluate", str(model_path))
    second = pipeline.run("model", "evaluate", str(model_path))

    assert first == second


def test_one_changed_training_outcome_is_another_model(pipeline: Pipeline) -> None:
    """Positive control: the name is not so forgiving it never changes."""
    first_pass = SINCE + timedelta(hours=1)
    original = pipeline.fit(pipeline.dataset(), configured("D"))
    changed = pipeline.fit(
        pipeline.dataset(lambda aos: aos == first_pass), configured("D")
    )

    assert original.name != changed.name


# --- calibrated probabilities -------------------------------------------------


@pytest.mark.parametrize("name", sorted(CONFIGURATIONS))
def test_every_prediction_is_a_probability(pipeline: Pipeline, name: str) -> None:
    model_path = pipeline.fit(pipeline.dataset(), configured(name))

    probabilities = [p.probability for _, p in scored_examples(pipeline, model_path)]

    assert probabilities
    assert all(math.isfinite(one) and 0.0 <= one <= 1.0 for one in probabilities)
    assert len(set(probabilities)) > 1


def test_even_an_impossible_pass_gets_a_probability(pipeline: Pipeline) -> None:
    """Features far outside anything trained on still give a number in [0, 1],
    though the logit behind it is nowhere near that range — the control."""
    model: Model = read_model(pipeline.fit(pipeline.dataset(), configured("D"))).model
    for elevation in (-1e6, 1e6):
        features = dict.fromkeys(model.configured.features, 0.0) | {
            "max_elevation_deg": elevation
        }
        logit = model.configured.logit(features)
        probability = predict(model, features, station_history=100).probability

        assert abs(logit) > 1.0
        assert 0.0 <= probability <= 1.0


def test_the_report_carries_what_calibration_is_judged_by(pipeline: Pipeline) -> None:
    report = pipeline.run(
        "model", "evaluate", str(pipeline.fit(pipeline.dataset(), configured("D")))
    )

    for needed in (
        "train until        2026-09-11T00:00:00+00:00",
        "validate until     2026-09-16T00:00:00+00:00",
        "Brier              ",
        "from training, Brier",
        "reliability (predicted probability in tenths)",
        "station          st_new",
        "band             vhf",
        "element_set_age  <24 h",
        "rolling-origin folds (2 asked",
        "completeness of the dataset",
    ):
        assert needed in report, needed
    assert sum(line.startswith("  0.") for line in report.splitlines()) == 10
