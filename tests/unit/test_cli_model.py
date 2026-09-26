"""``meridian model`` — fit, evaluate and show, with their refusals and exit codes.

Run in process through ``meridian.cli.main`` against datasets labelled from the
conftest worlds. Those worlds hold two measured passes, which is what every
dataset holds before a station reports, so a real fit refuses — and that
refusal, with its counts, is asserted as the designed behaviour. Where a fit
has to succeed, ``examples_of`` is handed synthetic examples inside the
dataset's dates; the lineage, publishing, reading back and printing around it
are the command's own.

One test runs a subprocess with scikit-learn made unimportable, to show that
``meridian model show`` works where the ``fit`` extra is not installed — the
platform image — and that ``fit`` says what to install (D-155).

Reference: docs/DECISIONS.md D-155, D-162, D-163, D-164.
"""

from __future__ import annotations

import os
import random
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from meridian.cli import main
from meridian.cli_snapshot import EXIT_CORRUPT
from meridian.datasets.manifest import content_sha256
from meridian.datasets.publish import read_directory
from meridian.prediction.examples import Example, ExampleSet
from meridian.prediction.features import FEATURES
from meridian.prediction.lineage import Inputs
from meridian.prediction.model_config import ModelConfig
from meridian.prediction.score import sigmoid

EXIT_FAILED = 1
SINCE = datetime(2026, 9, 1, tzinfo=UTC)
TRAIN_UNTIL = datetime(2026, 9, 12, tzinfo=UTC)
VALIDATE_UNTIL = datetime(2026, 9, 18, tzinfo=UTC)

CONFIG_TEXT = (
    'configuration = "D"\n'
    "min_station_history = 5\n"
    "folds = 2\n"
    "train_until = 2026-09-12T00:00:00Z\n"
    "validate_until = 2026-09-18T00:00:00Z\n"
)


def model_command(root: Path, *args: str) -> int:
    return main(["model", "--root", str(root), *args])


def printed_path(printed: str) -> Path:
    """The directory the first line of a report names."""
    first = printed.splitlines()[0]
    return Path(first.split(": ", 1)[1].rsplit(" (", 1)[0])


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "model.toml"
    path.write_text(CONFIG_TEXT, encoding="utf-8")
    return path


@pytest.fixture
def dataset(
    raw_snapshot: Any,
    world: Any,
    datasets_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> Path:
    """The conftest world, exported by hand and labelled by the command."""
    raw = raw_snapshot(world)
    assert main(["snapshot", "--root", str(datasets_root), "label", str(raw)]) == 0
    return printed_path(capsys.readouterr().out)


def synthetic(config: ModelConfig) -> ExampleSet:
    """Four passes a day inside the dataset's dates, decoding with elevation."""
    rng = random.Random(11)
    examples = []
    for n in range(88):
        features = {one.name: rng.uniform(-1.0, 1.0) for one in FEATURES}
        features["max_elevation_deg"] = rng.uniform(5.0, 85.0)
        features["element_set_age_h"] = rng.uniform(0.0, 100.0)
        chance = sigmoid((features["max_elevation_deg"] - 35.0) / 12.0)
        examples.append(
            Example(
                population=config.population,
                station_id=f"st_{n % 2}",
                satellite_id="norad:57166",
                aos=SINCE + timedelta(hours=6 * n),
                positive=rng.random() < chance,
                features=features,
                station_history=n // 2,
            )
        )
    return ExampleSet(config.population, tuple(examples), 1)


def hand_over_enough(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hand the command synthetic examples in place of the world's two."""

    def examples_of(_dataset: Any, _raw: Any, config: ModelConfig) -> Inputs:
        return Inputs(examples=synthetic(config), bands={"norad:57166": "vhf"})

    monkeypatch.setattr("meridian.cli_model.examples_of", examples_of)


@pytest.fixture
def other_raw(raw_snapshot: Any, archive_world: Any) -> Path:
    """A second raw snapshot: not the one the dataset was labelled from."""
    path: Path = raw_snapshot(archive_world)
    return path


@pytest.fixture
def model(
    monkeypatch: pytest.MonkeyPatch,
    dataset: Path,
    config_file: Path,
    datasets_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> Path:
    hand_over_enough(monkeypatch)
    fitted = model_command(
        datasets_root, "fit", str(dataset), "--config", str(config_file)
    )
    assert fitted == 0
    return printed_path(capsys.readouterr().out)


# --- fit ---------------------------------------------------------------------------


def test_fitting_twice_names_one_directory(
    monkeypatch: pytest.MonkeyPatch,
    dataset: Path,
    config_file: Path,
    datasets_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """D-163 at a prompt: run it twice and read the name."""
    hand_over_enough(monkeypatch)
    args = ("fit", str(dataset), "--config", str(config_file))

    assert model_command(datasets_root, *args) == 0
    first = capsys.readouterr().out
    assert model_command(datasets_root, *args) == 0
    second = capsys.readouterr().out

    assert printed_path(first) == printed_path(second)
    assert printed_path(first).parent == datasets_root / "models"
    assert "(written)" in first
    assert "already held, identically" in second
    assert "train until 2026-09-12T00:00:00+00:00" in first
    assert "examples.train" in first
    assert "examples.simulated" in first


def test_a_fit_on_the_passes_that_exist_refuses_with_their_count(
    dataset: Path,
    config_file: Path,
    datasets_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No station has reported enough to fit on: the refusal is the design."""
    assert (
        model_command(datasets_root, "fit", str(dataset), "--config", str(config_file))
        == EXIT_FAILED
    )

    refusal = capsys.readouterr().err
    assert "meridian model fit: training holds 0 examples" in refusal
    assert "1 simulated passes were not counted" in refusal
    assert not (datasets_root / "models").exists()


def test_a_configuration_without_dates_is_refused(
    dataset: Path, datasets_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert model_command(datasets_root, "fit", str(dataset)) == EXIT_FAILED

    assert "names no train_until and validate_until" in capsys.readouterr().err


def test_a_raw_snapshot_other_than_the_one_labelled_is_refused(
    dataset: Path,
    config_file: Path,
    other_raw: Path,
    datasets_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = model_command(
        datasets_root,
        "fit",
        str(dataset),
        "--config",
        str(config_file),
        "--snapshot",
        str(other_raw),
    )

    assert code == EXIT_FAILED
    assert "was made from the raw_snapshot" in capsys.readouterr().err


def test_a_raw_snapshot_not_under_the_root_is_asked_for(
    dataset: Path,
    config_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    elsewhere = tmp_path / "elsewhere"

    code = model_command(elsewhere, "fit", str(dataset), "--config", str(config_file))

    assert code == EXIT_FAILED
    assert "name its directory with --snapshot" in capsys.readouterr().err


def test_fitting_a_raw_snapshot_is_refused(
    raw_snapshot: Any,
    world: Any,
    config_file: Path,
    datasets_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw = raw_snapshot(world)

    code = model_command(datasets_root, "fit", str(raw), "--config", str(config_file))

    assert code == EXIT_FAILED
    assert "is a raw_snapshot, not a dataset" in capsys.readouterr().err


def test_a_path_that_is_not_a_directory_is_refused(
    datasets_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert model_command(datasets_root, "show", str(tmp_path / "nope")) == EXIT_FAILED
    assert "is not a directory" in capsys.readouterr().err


# --- evaluate ------------------------------------------------------------------------


def test_evaluate_prints_the_calibration_report(
    model: Path, datasets_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert model_command(datasets_root, "evaluate", str(model)) == 0

    printed = capsys.readouterr().out
    for needed in (
        "configuration      D: the shipped system",
        "train until        2026-09-12T00:00:00+00:00",
        "validate until     2026-09-18T00:00:00+00:00",
        "test until         2026-09-23T06:00:00+00:00 (the dataset's as_of)",
        "seed               0",
        "Brier              ",
        "base rate          ",
        "skill              ",
        "reliability (predicted probability in tenths)",
        "  0.0–0.1",
        "station          st_0",
        "band             vhf",
        "rolling-origin folds (2 asked",
        "completeness of the dataset",
        "our stations",
    ):
        assert needed in printed, needed


def test_evaluate_finds_a_moved_dataset_by_the_path_given(
    model: Path,
    dataset: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Under another root nothing is found, until the dataset is named."""
    elsewhere = tmp_path / "elsewhere"
    (raw,) = (dataset.parent.parent / "snapshots").glob("20*-*")

    assert model_command(elsewhere, "evaluate", str(model)) == EXIT_FAILED
    assert "name its directory with --dataset" in capsys.readouterr().err
    named = model_command(
        elsewhere,
        "evaluate",
        str(model),
        "--dataset",
        str(dataset),
        "--snapshot",
        str(raw),
    )
    assert named == 0
    assert "reliability" in capsys.readouterr().out


def test_evaluate_refuses_a_dataset_the_model_was_not_fitted_on(
    model: Path,
    other_raw: Path,
    datasets_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    main(["snapshot", "--root", str(datasets_root), "label", str(other_raw)])
    other_dataset = printed_path(capsys.readouterr().out)

    code = model_command(
        datasets_root, "evaluate", str(model), "--dataset", str(other_dataset)
    )

    assert code == EXIT_FAILED
    assert "was made from the evaluation_dataset" in capsys.readouterr().err


# --- show, and a damaged model ------------------------------------------------


def test_show_prints_what_the_model_is(
    model: Path, datasets_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert model_command(datasets_root, "show", str(model)) == 0

    printed = capsys.readouterr().out
    assert "configuration      D on own" in printed
    assert "below 5 settled outcomes" in printed
    assert "configured model" in printed
    assert "geometry fallback model" in printed
    assert "max_elevation_deg" in printed
    assert "calibration        sigmoid(" in printed
    assert "scikit-learn" in printed


def test_a_changed_model_is_refused_as_damaged(
    model: Path, datasets_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = model / "model.json"
    model.chmod(0o700)
    target.chmod(0o600)
    target.write_bytes(target.read_bytes().replace(b'"seed":0', b'"seed":1'))

    assert model_command(datasets_root, "show", str(model)) == EXIT_CORRUPT
    assert "meridian model show" in capsys.readouterr().err


def test_a_dataset_is_not_shown_as_a_model(
    dataset: Path, datasets_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert model_command(datasets_root, "show", str(dataset)) == EXIT_FAILED
    assert "not a model" in capsys.readouterr().err


# --- without the fit extra ---------------------------------------------------------


WITHOUT_THE_EXTRA = (
    "import sys\n"
    "sys.modules['sklearn'] = None\n"
    "from meridian.cli import main\n"
    "sys.exit(main(sys.argv[1:]))\n"
)
"""scikit-learn made unimportable, as it is in the platform image."""


def without_the_extra(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", WITHOUT_THE_EXTRA, *args],
        capture_output=True,
        text=True,
        check=False,
        env=os.environ | {"PYTHONDONTWRITEBYTECODE": "1"},
    )


def test_show_runs_and_fit_says_what_to_install_without_the_extra(
    model: Path, dataset: Path, config_file: Path, datasets_root: Path
) -> None:
    show = without_the_extra("model", "--root", str(datasets_root), "show", str(model))
    fit = without_the_extra(
        "model",
        "--root",
        str(datasets_root),
        "fit",
        str(dataset),
        "--config",
        str(config_file),
    )

    assert show.returncode == 0, show.stderr
    assert "configured model" in show.stdout
    assert fit.returncode == EXIT_FAILED
    assert "needs the fit extra" in fit.stderr
    assert "Traceback" not in fit.stderr


def test_the_published_model_names_its_dataset(model: Path, dataset: Path) -> None:
    held = read_directory(model)

    assert held.manifest.kind == "model"
    assert held.manifest.derived_from == content_sha256(
        read_directory(dataset).manifest
    )
