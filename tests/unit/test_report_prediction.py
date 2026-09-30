"""The prediction section: every configuration fitted, judged and compared.

Built through ``meridian report build`` over a world every configuration fits
on (``evaluation_world`` in ``tests/unit/conftest.py``), with every database
connection and socket refused.

**Each claim has its positive control:** a model the report fitted is the
model ``meridian model fit`` fits from the same settings and seed; another
master seed moves the intervals and leaves the fitted figures alone; D∖conditions
really reads fewer features than D; and a world with nothing to fit on still
gives a report, saying why.

Reference: docs/DECISIONS.md D-155, D-160, D-164, D-224, D-237.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import pytest

from meridian.cli import main
from meridian.datasets.publish import read_directory
from meridian.prediction.configurations import CONFIGURATIONS
from meridian.prediction.model_files import read_model
from meridian.reports import build as build_module

SETTINGS = """
[prediction]
min_station_history = 5
folds = 2
train_until = 2026-09-11T00:00:00Z
validate_until = 2026-09-16T00:00:00Z
resamples = 200
"""


@pytest.fixture
def guarded(no_network: Any, network_guard: Any) -> Any:
    network_guard()
    return no_network


class Runs:
    """``meridian report build`` against one datasets root."""

    def __init__(self, root: Path, snapshot: Path, tmp: Path, capsys: Any) -> None:
        self.root, self.snapshot, self.capsys = root, snapshot, capsys
        self.config = tmp / "evaluation.toml"
        self.config.write_text(SETTINGS, encoding="utf-8")

    def build(self, seed: int = 4471) -> Path:
        self.capsys.readouterr()
        code = main(
            [
                "report",
                "--root",
                str(self.root),
                "build",
                "--snapshot",
                str(self.snapshot),
                "--config",
                str(self.config),
                "--seed",
                str(seed),
            ]
        )
        out = self.capsys.readouterr()
        assert code == 0, out.err
        return Path(out.out.splitlines()[0].split(": ", 1)[1].rsplit(" (", 1)[0])


@pytest.fixture
def runs(
    raw_snapshot: Any,
    evaluation_world: Any,
    datasets_root: Path,
    tmp_path: Path,
    capsys: Any,
) -> Runs:
    return Runs(datasets_root, raw_snapshot(evaluation_world), tmp_path, capsys)


def rows(run: Path, name: str = "prediction.jsonl") -> list[dict[str, Any]]:
    return [json.loads(line) for line in (run / name).read_bytes().splitlines()]


def of(found: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [one for one in found if one["row"] == kind]


def models(run: Path) -> dict[str, dict[str, Any]]:
    return {one["name"]: one for one in of(rows(run), "model")}


def test_every_configuration_is_fitted_judged_and_verifies(
    runs: Runs, guarded: Any
) -> None:
    run = runs.build()
    fitted = models(run)

    assert set(fitted) == {"A", "C", "D", "D-conditions"}
    assert {one["status"] for one in fitted.values()} == {"fitted"}
    for one in fitted.values():
        assert one["test"] > 0
        assert one["brier_interval"]["low"] <= one["brier"]
        assert one["brier"] <= one["brier_interval"]["high"]
    assert main(["report", "--root", str(runs.root), "verify", str(run)]) == 0
    assert guarded.attempts == []


def test_a_reported_model_is_the_one_model_fit_makes_with_its_seed(
    runs: Runs, tmp_path: Path, capsys: Any
) -> None:
    """The report fits through ``meridian model fit``'s functions, not a copy."""
    run = runs.build()
    d = models(run)["D"]
    dataset = read_directory(run).manifest.parameters["evaluation_dataset"]
    config = tmp_path / "model.toml"
    config.write_text(
        SETTINGS.replace(
            "[prediction]", f'configuration = "D"\nseed = {d["seed"]}'
        ).replace("resamples = 200\n", ""),
        encoding="utf-8",
    )
    capsys.readouterr()
    dataset_dir = runs.root / "evaluation" / bytes.fromhex(dataset).hex()[:12]

    assert (
        main(
            [
                "model",
                "--root",
                str(runs.root),
                "fit",
                str(dataset_dir),
                "--config",
                str(config),
            ]
        )
        == 0
    )
    printed = capsys.readouterr().out
    assert d["model_sha256"] in printed
    assert "already held, identically" in printed


def test_every_seed_is_derived_and_recorded(runs: Runs) -> None:
    run = runs.build()
    seeds = {
        one["component"]: one["seed"] for one in of(rows(run, "run.jsonl"), "seed")
    }

    assert set(seeds) == {
        "bootstrap.orbit",
        "bootstrap.prediction",
        "bootstrap.scheduling",
        "solver",
        "model.A",
        "model.C",
        "model.D",
        "model.D-conditions",
    }
    assert seeds == read_directory(run).manifest.parameters["seeds"]
    assert {name: one["seed"] for name, one in models(run).items()} == {
        name.removeprefix("model."): value
        for name, value in seeds.items()
        if name.startswith("model.")
    }


def test_another_seed_moves_the_intervals_and_not_the_fitted_figures(
    runs: Runs,
) -> None:
    first, second = models(runs.build(1)), models(runs.build(2))

    for name, one in first.items():
        assert one["brier"] == second[name]["brier"]
        assert one["skill"] == second[name]["skill"]
    assert any(
        one["brier_interval"] != second[name]["brier_interval"]
        for name, one in first.items()
    )


def test_d_without_conditions_reads_everything_d_reads_but_the_group(
    runs: Runs,
) -> None:
    run = runs.build()
    shipped, without = (
        read_model(runs.root / "models" / models(run)[name]["model_sha256"][:12]).model
        for name in ("D", "D-conditions")
    )
    left_out = {
        feature
        for feature in CONFIGURATIONS["D"].features
        if feature.startswith(("kp_", "cloud_"))
    }

    assert left_out
    assert set(shipped.configured.features) - set(without.configured.features) == (
        left_out
    )


def test_kp_is_untested_below_the_stated_minimum(runs: Runs) -> None:
    """No Kp was published in this world, so nothing may be said about it."""
    conditions = of(rows(runs.build()), "conditions")[0]

    assert conditions["kp_known"] == 0
    assert conditions["kp_verdict"] == "untested"
    assert "**untested**" in (runs.build() / "report.md").read_text(encoding="utf-8")


def test_sc2_is_read_from_d_with_its_interval(runs: Runs) -> None:
    run = runs.build()
    sc2 = of(rows(run), "sc2")[0]

    assert sc2["status"] == "measured"
    assert sc2["skill"] == models(run)["D"]["skill"]
    assert sc2["point_meets"] == (sc2["skill"] >= 0.25)


def test_every_comparison_pairs_the_same_test_passes(runs: Runs) -> None:
    run = runs.build()
    fitted = models(run)

    for one in of(rows(run), "comparison"):
        assert one["status"] == "compared"
        assert one["n"] == fitted[one["first"]]["test"]
        expected = fitted[one["second"]]["brier"] - fitted[one["first"]]["brier"]
        assert one["reduction"] == pytest.approx(expected, abs=2e-6)


def test_each_fitted_model_has_a_reliability_diagram_that_parses(
    runs: Runs,
) -> None:
    run = runs.build()
    figures = sorted(path.name for path in run.glob("reliability_*.svg"))

    assert figures == [
        "reliability_a.svg",
        "reliability_c.svg",
        "reliability_d.svg",
        "reliability_d_conditions.svg",
    ]
    for name in figures:
        root = ElementTree.fromstring((run / name).read_bytes())
        assert root.tag.endswith("svg")
        assert f"]({name})" in (run / "report.md").read_text(encoding="utf-8")


def test_a_world_with_nothing_to_fit_still_reports_why(
    raw_snapshot: Any, archive_world: Any, datasets_root: Path, capsys: Any
) -> None:
    """The small world holds a handful of passes, and one simulated."""
    config = Path(__file__).resolve().parents[2] / "analysis/configs"
    code = main(
        [
            "report",
            "--root",
            str(datasets_root),
            "build",
            "--snapshot",
            str(raw_snapshot(archive_world)),
            "--config",
            str(config / "evaluation.toml.example"),
            "--seed",
            "1",
        ]
    )
    run = Path(
        capsys.readouterr().out.splitlines()[0].split(": ", 1)[1].rsplit(" (")[0]
    )

    assert code == 0
    refused = models(run)
    assert {one["status"] for one in refused.values()} == {"refused"}
    assert all(one["reason"] for one in refused.values())
    assert of(rows(run), "sc2")[0]["status"] == "not measured"
    assert not list(run.glob("reliability_*.svg"))


def test_without_the_fit_extra_it_says_what_to_install(
    runs: Runs, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    def missing(*_: object, **__: object) -> None:
        raise ModuleNotFoundError("No module named 'sklearn'", name="sklearn")

    monkeypatch.setattr(build_module, "fit_variants", missing)

    code = main(
        [
            "report",
            "--root",
            str(runs.root),
            "build",
            "--snapshot",
            str(runs.snapshot),
            "--config",
            str(runs.config),
            "--seed",
            "1",
        ]
    )

    assert code == 1
    assert "fit extra" in capsys.readouterr().err
