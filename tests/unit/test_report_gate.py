"""Stage 22's gate: every number and figure regenerated from its inputs.

The roadmap states it as: *every number and figure can be regenerated from a
snapshot, configuration, seed, and code version.* And beside it: *no report
command may silently fetch mutable external data.* Each clause is asserted
here through ``meridian report build`` and ``meridian report verify``, with
every database connection and socket refused, over one world in which every
section has something to say:

* the fittable world of ``tests/unit/conftest.py`` — 22 days, two stations,
  frames on every decode — with first detections, clock offsets and orbital
  regimes added, so the orbit section measures;
* a sealed fault run of one station outage, so the reliability section times
  a detection.

**Each claim has its positive control:** another seed moves the intervals and
leaves every fitted figure alone; one changed row changes the hash and names
the file; a run whose numbers were rewritten and resealed passes its own
manifest and fails verification; a changed setting is caught the same way.

Stage 24 adds one clause, *every result includes sample size and
uncertainty*: every estimate in every results file carries its interval, and
every interval the count it was drawn from (D-254).

Reference: docs/DECISIONS.md D-234 to D-240, D-254; ``EVALUATION.md`` §9, §10.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from meridian.cli import main
from meridian.datasets.fault_runs import publish_fault_run
from meridian.datasets.manifest import content_sha256, file_entry, parse_manifest
from meridian.datasets.publish import publish_directory, read_directory
from meridian.reliability.fault_ledger import read_fault_ledger
from meridian.reliability.fault_model import Gathered, StationEvidence
from meridian.reliability.faults import judge_gathered
from meridian.reports.render import render_report
from meridian.reports.render_orbit import orbit_figures
from meridian.reports.render_prediction import prediction_figures
from meridian.reports.render_reliability import reliability_figures
from meridian.reports.render_scheduling import scheduling_figures

REPO = Path(__file__).resolve().parents[2]

SETTINGS = """
[prediction]
min_station_history = 5
folds = 2
train_until = 2026-09-11T00:00:00Z
validate_until = 2026-09-16T00:00:00Z
resamples = 200

[scheduling]
time_limit_s = 5.0
resamples = 200

[orbit]
resamples = 200
min_young = 3

[reliability]
history_step_days = 7
"""

FRESH = (
    sys.executable,
    "-c",
    "import sys; from meridian.cli import main; sys.exit(main(sys.argv[1:]))",
)
"""The ``meridian`` command in a new interpreter."""

FIGURES: tuple[
    tuple[str, Callable[[Sequence[Mapping[str, object]]], dict[str, bytes]]], ...
] = (
    ("prediction", prediction_figures),
    ("scheduling", scheduling_figures),
    ("orbit", orbit_figures),
    ("reliability", reliability_figures),
)

T0 = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def gate_world(
    evaluation_world: Mapping[str, Sequence[Mapping[str, object]]],
) -> dict[str, Any]:
    """The fittable world, with every detection timed and every clock stated."""
    world: dict[str, Any] = {
        name: list(rows) for name, rows in evaluation_world.items()
    }
    passes = {one["id"]: one for one in world["passes"]}
    beats, observations, assignments = [], [], []
    for number, one in enumerate(world["observations"]):
        assignment = next(
            a
            for a in world["assignments"]
            if a["assignment_id"] == one["assignment_id"]
        )
        aos = passes[assignment["pass_id"]]["aos"]
        heard = (
            aos + timedelta(seconds=20 + number % 7)
            if one["outcome"] == "decoded"
            else None
        )
        observations.append(dict(one) | {"first_detection_at": heard})
        assignments.append(dict(assignment) | {"timing_uncertainty_s": 30.0})
        beats.append(
            {
                "station_id": assignment["station_id"],
                "received_at": aos + timedelta(minutes=1),
                "clock_offset_s": -0.5,
                "clock_uncertainty_s": 0.05,
            }
        )
    world["observations"] = observations
    world["assignments"] = assignments
    world["heartbeats"] = beats
    world["satellites"] = [
        dict(one) | {"orbital_regime": "leo"} for one in world["satellites"]
    ]
    return world


def sealed_fault_run(root: Path) -> Path:
    """One station outage: offline 90 s after its last heartbeat, heard again."""
    ledger = "\n".join(
        json.dumps(
            {
                "ledger": 1,
                "event": event,
                "run_id": "gate",
                "kind": "network_down",
                "target": "station:1",
                "at": at.isoformat().replace("+00:00", "Z"),
            }
            | ({"station_id": "st_a"} if event == "open" else {})
        )
        for event, at in (("open", T0), ("close", T0 + timedelta(minutes=10)))
    )
    fault = read_fault_ledger(ledger.splitlines())[0]
    evidence = StationEvidence(
        heartbeats=(
            *(T0 - timedelta(seconds=30 * n) for n in range(5, -1, -1)),
            T0 + timedelta(minutes=10, seconds=30),
        ),
        work=(),
        rounds=(),
        classifications={},
        reported=frozenset(),
        as_of=T0 + timedelta(hours=1),
    )
    gathered = (Gathered(fault, evidence),)
    verdicts = [judge_gathered(one) for one in gathered]
    return publish_fault_run(
        ledger, gathered, verdicts, root=root, stamp=("0025", T0 + timedelta(hours=1))
    ).path


class Gate:
    """``meridian report`` against one datasets root, one world, one fault run."""

    def __init__(self, root: Path, snapshot: Path, tmp: Path, capsys: Any) -> None:
        self.root, self.snapshot, self.capsys = root, snapshot, capsys
        self.config = tmp / "evaluation.toml"
        self.config.write_text(SETTINGS, encoding="utf-8")
        self.faults = sealed_fault_run(root)

    def arguments(self, seed: int, root: Path | None = None) -> list[str]:
        return [
            "report",
            "--root",
            str(root or self.root),
            "build",
            "--snapshot",
            str(self.snapshot),
            "--config",
            str(self.config),
            "--seed",
            str(seed),
            "--faults",
            str(self.faults),
        ]

    def build(self, seed: int = 4471) -> Path:
        self.capsys.readouterr()
        code = main(self.arguments(seed))
        out = self.capsys.readouterr()
        assert code == 0, out.err
        return printed(out.out)

    def verify(self, run: Path) -> int:
        self.capsys.readouterr()
        return main(["report", "--root", str(self.root), "verify", str(run)])


def printed(out: str) -> Path:
    return Path(out.splitlines()[0].split(": ", 1)[1].rsplit(" (", 1)[0])


@pytest.fixture
def guarded(no_network: Any, network_guard: Any) -> Any:
    network_guard()
    return no_network


@pytest.fixture
def gate(
    raw_snapshot: Any,
    evaluation_world: Any,
    datasets_root: Path,
    tmp_path: Path,
    capsys: Any,
) -> Gate:
    return Gate(
        datasets_root, raw_snapshot(gate_world(evaluation_world)), tmp_path, capsys
    )


def parsed(run: Path) -> dict[str, list[dict[str, Any]]]:
    return {
        path.name.removesuffix(".jsonl"): [
            json.loads(line) for line in path.read_bytes().splitlines()
        ]
        for path in sorted(run.glob("*.jsonl"))
    }


def of(rows: Sequence[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [one for one in rows if one["row"] == kind]


COUNTS = ("n", "denominator", "station_days")
"""What an interval was drawn from: passes, a proportion's denominator, or the
station-days a bootstrap resampled."""


def _intervals(value: object, where: str) -> list[tuple[str, Mapping[str, Any]]]:
    """Every mapping inside ``value`` that states an estimate or an interval."""
    if isinstance(value, list):
        return [
            found
            for i, one in enumerate(value)
            for found in _intervals(one, f"{where}[{i}]")
        ]
    if not isinstance(value, Mapping):
        return []
    found = [
        (f"{where}.{key}", value)
        for key, held in value.items()
        if (key == "interval" or key.endswith("_interval")) and held is not None
    ]
    if value.get("estimate") is not None:
        found.append((f"{where}.estimate", value))
    return found + [
        one for key, held in value.items() for one in _intervals(held, f"{where}.{key}")
    ]


def unstated(rows: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[str]:
    """Each estimate without its interval, and each interval without its count."""
    problems = []
    for name, held in rows.items():
        for row in held:
            for where, holder in _intervals(row, f"{name}:{row['row']}"):
                has_interval = holder.get("interval") is not None or (
                    "low" in holder and "high" in holder
                )
                if where.endswith(".estimate") and not has_interval:
                    problems.append(f"{where} has no interval")
                if not any(key in holder or key in row for key in COUNTS):
                    problems.append(f"{where} has no count")
    return problems


# --- every section has something to say ----------------------------------------


def test_every_section_measures_and_draws(gate: Gate, guarded: Any) -> None:
    """Not a gate over empty sections: each one's claim is measured here."""
    found = parsed(gate.build())

    assert of(found["prediction"], "sc2")[0]["status"] == "measured"
    assert of(found["scheduling"], "sc1")[0]["status"] == "measured"
    assert of(found["orbit"], "sc3")[0]["status"] == "measured"
    assert of(found["reliability"], "sc4")[0]["status"] == "measured"
    assert of(found["reliability"], "sc5")[0]["status"] == "measured"
    figures = {path.name for path in gate.build().glob("*.svg")}
    assert {
        "reliability_d.svg",
        "scheduling_gains.svg",
        "orbit_timing_measured.svg",
        "capture_history.svg",
        "fault_detection.svg",
    } <= figures
    assert guarded.attempts == []


# --- regenerated from a snapshot, a configuration and a seed --------------------


def test_built_twice_it_is_the_same_run(gate: Gate) -> None:
    assert gate.build() == gate.build()


def test_a_new_interpreter_on_a_new_root_builds_the_same_run(
    gate: Gate, tmp_path: Path
) -> None:
    """Another process, another hash seed, a root holding none of the models."""
    other = tmp_path / "elsewhere"
    run = gate.build()
    environment = {**os.environ, "PYTHONHASHSEED": "7"}

    done = subprocess.run(
        [*FRESH, *gate.arguments(4471, other)[:-2], "--faults", str(gate.faults)],
        capture_output=True,
        text=True,
        cwd=REPO,
        env=environment,
        check=False,
    )

    assert done.returncode == 0, done.stderr
    assert printed(done.stdout).name == run.name


def test_verify_regenerates_it(gate: Gate, guarded: Any) -> None:
    run = gate.build()

    assert gate.verify(run) == 0
    out = gate.capsys.readouterr().out
    assert "regenerates identically" in out
    assert "environment" not in out, "the same machine reported a change"
    assert guarded.attempts == []


def test_the_seed_moves_the_intervals_and_nothing_fitted(gate: Gate) -> None:
    first, second = parsed(gate.build(1)), parsed(gate.build(2))

    def fitted(found: dict[str, list[dict[str, Any]]]) -> list[object]:
        return [
            *(one["brier"] for one in of(found["prediction"], "model")),
            *(one["frames"] for one in of(found["scheduling"], "scheduler")),
            *(one["slope_s_per_day"] for one in of(found["orbit"], "regime")),
            of(found["reliability"], "sc4")[0]["capture"],
        ]

    def intervals(found: dict[str, list[dict[str, Any]]]) -> list[object]:
        return [
            *(one["brier_interval"] for one in of(found["prediction"], "model")),
            *(one["per_hour_interval"] for one in of(found["scheduling"], "regret")),
            *(one["slope_interval"] for one in of(found["orbit"], "regime")),
        ]

    assert fitted(first) == fitted(second)
    assert intervals(first) != intervals(second)


def test_one_changed_row_is_another_run(
    gate: Gate, raw_snapshot: Any, evaluation_world: Any
) -> None:
    run = gate.build()
    world = gate_world(evaluation_world)
    world["observations"][0] = dict(world["observations"][0]) | {"outcome": "no_signal"}
    gate.snapshot = raw_snapshot(world)

    other = gate.build()

    assert other != run
    changed = {
        one.name: one.sha256 for one in read_directory(other).manifest.files
    }.items() ^ {
        one.name: one.sha256 for one in read_directory(run).manifest.files
    }.items()
    assert "data.jsonl" in {name for name, _ in changed}


# --- every number and figure comes from the hashed results ----------------------


def test_the_report_is_rendered_from_the_results_files_alone(gate: Gate) -> None:
    run = gate.build()

    assert render_report(parsed(run)) == (run / "report.md").read_bytes()


def test_every_figure_is_regenerated_byte_for_byte_from_the_results(
    gate: Gate,
) -> None:
    run = gate.build()
    found = parsed(run)
    drawn: dict[str, bytes] = {}
    for section, draw in FIGURES:
        drawn |= draw(found[section])

    assert drawn
    assert set(drawn) == {path.name for path in run.glob("*.svg")}
    for name, data in drawn.items():
        assert (run / name).read_bytes() == data, name


def test_the_run_records_its_code_and_what_it_drew_from(gate: Gate) -> None:
    run = gate.build()
    manifest = parse_manifest((run / "manifest.json").read_bytes())
    code = manifest.environment["code"]

    assert isinstance(code, dict)
    assert set(code) == {"commit", "dirty", "source"}
    assert set(manifest.environment["dependencies"]) >= {"meridian", "highspy"}  # type: ignore[arg-type]
    assert manifest.parameters["seed"] == 4471
    assert set(manifest.parameters["seeds"]) >= {  # type: ignore[arg-type]
        "model.D",
        "solver",
        "bootstrap.prediction",
        "bootstrap.scheduling",
        "bootstrap.orbit",
    }
    assert manifest.parameters["fault_runs"] == [
        content_sha256(read_directory(gate.faults).manifest).hex()
    ]


# --- positive controls -------------------------------------------------------------


def reseal(run: Path, name: str, edit: Callable[[bytes], bytes]) -> Path:
    """A copy of ``run`` with one file edited and its manifest made to match."""
    held = read_directory(run)
    files = dict(held.files)
    files[name] = edit(files[name])
    assert files[name] != held.files[name]
    manifest = replace(
        held.manifest,
        files=tuple(file_entry(one, data) for one, data in sorted(files.items())),
    )
    return publish_directory(run.parent, "forged", manifest, files).path


def test_a_rewritten_and_resealed_number_fails_verification(gate: Gate) -> None:
    forged = reseal(
        gate.build(),
        "scheduling.jsonl",
        lambda data: data.replace(b'"violations":0', b'"violations":1'),
    )

    assert read_directory(forged).manifest.kind == "evaluation_report"
    assert gate.verify(forged) == 1
    assert "scheduling.jsonl" in gate.capsys.readouterr().err


def test_a_changed_setting_resealed_fails_verification(gate: Gate) -> None:
    forged = reseal(
        gate.build(),
        "config.toml",
        lambda data: data.replace(
            b"resamples = 200\n\n[orbit]", b"resamples = 300\n\n[orbit]"
        ),
    )

    assert gate.verify(forged) == 1


def test_a_run_whose_fault_run_is_gone_cannot_be_verified(gate: Gate) -> None:
    run = gate.build()
    for path in [gate.faults, *gate.faults.rglob("*")]:
        path.chmod(0o700 if path.is_dir() else 0o600)
    shutil.rmtree(gate.faults)

    assert gate.verify(run) == 1
    assert "no fault run" in gate.capsys.readouterr().err


# --- every result states its sample size and its uncertainty -------------------


def test_every_estimate_carries_its_interval_and_its_count(gate: Gate) -> None:
    rows = parsed(gate.build())

    assert len(unstated({"": [{"row": "x", "interval": {}}]})) == 1
    assert sum(len(_intervals(one, "")) for held in rows.values() for one in held)
    assert unstated(rows) == []


@pytest.mark.parametrize(
    ("row", "found"),
    [
        ({"row": "sc", "interval": {"low": 0.1, "high": 0.2}}, "has no count"),
        ({"row": "rate", "n": 9, "share": {"estimate": 0.5}}, "has no interval"),
        (
            {"row": "rate", "share": {"estimate": 0.5, "low": 0.2, "high": 0.8}},
            "has no count",
        ),
    ],
)
def test_an_estimate_missing_its_interval_or_its_count_is_found(
    row: dict[str, Any], found: str
) -> None:
    assert any(found in one for one in unstated({"results": [row]}))


def test_an_estimate_stated_whole_is_not() -> None:
    row = {"row": "rate", "share": {"estimate": 0.5, "low": 0.2, "high": 0.8, "n": 9}}

    assert unstated({"results": [row]}) == []
