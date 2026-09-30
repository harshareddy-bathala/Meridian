"""The scheduling section: seven schedulers replayed, SC-1, and oracle regret.

Built through ``meridian report build`` over ``evaluation_world``, with every
database connection and socket refused.

**Each claim has its positive control:** the section's figures are the ones
``replay_schedules`` and ``paired_gain`` give on the same models and seeds, so
they are not a second implementation; runtimes are measured and never in a
hashed file; a snapshot kept outside the datasets root is still replayed; a
schedule that breaks a constraint publishes nothing; and a run with nothing to
replay still reports, saying why.

Reference: docs/DECISIONS.md D-172, D-235, D-238.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import pytest

from meridian.cli import main
from meridian.datasets.manifest import parse_manifest
from meridian.datasets.publish import read_directory
from meridian.prediction.replay import load_replay
from meridian.reports import scheduling as scheduling_module
from meridian.scheduler.comparison import paired_gain
from meridian.scheduler.replay import SCHEDULERS, ReplayInvalidError, replay_schedules
from meridian.scheduler.schedule_config import ScheduleConfig

SETTINGS = """
[prediction]
min_station_history = 5
folds = 0
train_until = 2026-09-11T00:00:00Z
validate_until = 2026-09-16T00:00:00Z
resamples = 200

[scheduling]
time_limit_s = 5.0
resamples = 300
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

    def build(self, seed: int = 4471, snapshot: Path | None = None) -> Path:
        code, out = self.attempt(seed, snapshot)
        assert code == 0, out.err
        return Path(out.out.splitlines()[0].split(": ", 1)[1].rsplit(" (", 1)[0])

    def attempt(self, seed: int = 4471, snapshot: Path | None = None) -> Any:
        self.capsys.readouterr()
        code = main(
            [
                "report",
                "--root",
                str(self.root),
                "build",
                "--snapshot",
                str(snapshot or self.snapshot),
                "--config",
                str(self.config),
                "--seed",
                str(seed),
            ]
        )
        return code, self.capsys.readouterr()


@pytest.fixture
def runs(
    raw_snapshot: Any,
    evaluation_world: Any,
    datasets_root: Path,
    tmp_path: Path,
    capsys: Any,
) -> Runs:
    return Runs(datasets_root, raw_snapshot(evaluation_world), tmp_path, capsys)


def rows(run: Path) -> list[dict[str, Any]]:
    data = (run / "scheduling.jsonl").read_bytes()
    return [json.loads(line) for line in data.splitlines()]


def of(found: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [one for one in found if one["row"] == kind]


def seeds_of(run: Path) -> dict[str, int]:
    seeds: dict[str, int] = read_directory(run).manifest.parameters["seeds"]  # type: ignore[assignment]
    return seeds


def test_every_scheduler_is_replayed_and_every_schedule_checked(
    runs: Runs, guarded: Any
) -> None:
    found = rows(runs.build())
    replay = of(found, "replay")[0]

    assert replay["status"] == "replayed"
    assert [one["name"] for one in of(found, "scheduler")] == list(SCHEDULERS)
    assert replay["schedules_checked"] == len(SCHEDULERS) * replay["station_days"]
    assert replay["violations"] == 0
    assert guarded.attempts == []


def test_the_figures_are_the_replays_own(runs: Runs) -> None:
    """Not a second implementation: the same functions, models and seeds agree."""
    run = runs.build()
    parameters = read_directory(run).manifest.parameters
    models = {
        name: runs.root / "models" / str(digest)[:12]
        for name, digest in parameters["models"].items()  # type: ignore[union-attr]
        if name in ("A", "C", "D")
    }
    dataset = runs.root / "evaluation" / str(parameters["evaluation_dataset"])[:12]
    seeds = seeds_of(run)
    replay = load_replay(dataset, models, root=runs.root)
    results = replay_schedules(
        replay, ScheduleConfig(time_limit_s=5.0, seed=seeds["solver"])
    ).results
    expected = paired_gain(
        results["D"],
        results["B"],
        [day.hours for day in replay.days],
        seed=seeds["bootstrap.scheduling"],
        resamples=300,
    )

    sc1 = next(one for one in of(rows(run), "gain") if one["second"] == "B")
    assert sc1["per_hour"] == round(expected.per_hour, 6)
    assert sc1["per_hour_interval"] == {
        "low": round(expected.per_hour_interval.low, 6),
        "high": round(expected.per_hour_interval.high, 6),
    }
    frames = {one["name"]: one["frames"] for one in of(rows(run), "scheduler")}
    assert frames == {name: sum(d.frames for d in results[name]) for name in SCHEDULERS}


def test_sc1_is_the_relative_d_minus_b_gain(runs: Runs) -> None:
    found = rows(runs.build())
    sc1 = of(found, "sc1")[0]
    gain = next(one for one in of(found, "gain") if one["label"] == "SC-1")

    assert sc1["status"] == "measured"
    assert sc1["relative"] == gain["relative"]
    assert sc1["target"] == 0.2
    assert sc1["point_meets"] == (sc1["relative"] >= 0.2)


def test_the_oracle_takes_at_least_every_schedulers_frames(runs: Runs) -> None:
    found = rows(runs.build())

    for one in of(found, "regret"):
        assert one["per_hour"] >= 0
    assert {one["scheduler"] for one in of(found, "regret")} == set(SCHEDULERS) - {
        "oracle"
    }


def test_runtimes_are_recorded_and_never_hashed(runs: Runs) -> None:
    run = runs.build()
    environment = parse_manifest((run / "manifest.json").read_bytes()).environment
    runtimes = environment["runtime_s"]

    assert isinstance(runtimes, dict)
    assert "build" in runtimes
    assert set(runtimes["scheduling"]) == {"A", "B", "C", "D", "oracle"}
    assert str(environment["solver"]).startswith("highs ")
    assert b"runtime" not in (run / "scheduling.jsonl").read_bytes()
    assert b"highs" not in (run / "scheduling.jsonl").read_bytes()


def test_another_seed_moves_the_intervals_and_not_the_frames(runs: Runs) -> None:
    first, second = rows(runs.build(1)), rows(runs.build(2))

    assert of(first, "scheduler") == of(second, "scheduler")
    assert of(first, "regret") != of(second, "regret")


def test_a_snapshot_outside_the_datasets_root_is_still_replayed(
    runs: Runs, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "elsewhere" / runs.snapshot.name
    shutil.copytree(runs.snapshot, elsewhere)

    replay = of(rows(runs.build(snapshot=elsewhere)), "replay")[0]

    assert replay["status"] == "replayed"


def test_a_schedule_that_breaks_a_constraint_publishes_nothing(
    runs: Runs, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_: object) -> None:
        raise ReplayInvalidError("D broke turnaround on st_a 2026-09-17")

    monkeypatch.setattr(scheduling_module, "replay_schedules", broken)

    code, out = runs.attempt()

    assert code == 1
    assert "broke turnaround" in out.err
    assert not (runs.root / "reports").exists()


def test_nothing_to_replay_is_a_row_that_says_why(
    raw_snapshot: Any, archive_world: Any, datasets_root: Path, capsys: Any
) -> None:
    example = Path(__file__).resolve().parents[2] / "analysis/configs"
    code = main(
        [
            "report",
            "--root",
            str(datasets_root),
            "build",
            "--snapshot",
            str(raw_snapshot(archive_world)),
            "--config",
            str(example / "evaluation.toml.example"),
            "--seed",
            "1",
        ]
    )
    out = capsys.readouterr().out
    run = Path(out.splitlines()[0].split(": ", 1)[1].rsplit(" (", 1)[0])

    assert code == 0
    found = rows(run)
    assert of(found, "replay")[0]["status"] == "not replayed"
    assert "not fitted" in of(found, "replay")[0]["reason"]
    assert of(found, "sc1")[0]["status"] == "not measured"
    assert not list(run.glob("scheduling_*.svg"))


def test_both_figures_are_drawn_and_named_in_the_report(runs: Runs) -> None:
    run = runs.build()
    report = (run / "report.md").read_text(encoding="utf-8")

    for name in ("scheduling_gains.svg", "scheduling_regret.svg"):
        assert ElementTree.fromstring((run / name).read_bytes()).tag.endswith("svg")
        assert f"]({name})" in report
