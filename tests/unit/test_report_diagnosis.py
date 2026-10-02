"""Stage 27's report half: SC-8 regenerated from sealed simulated fleets.

The completion gate reads: *SC-8's confusion matrix, per-cause recall,
wrong-cause fraction and undetermined fraction regenerate from the simulator's
seeds, labelled as simulated and reported apart from any real labelled cases.*
The fleets are flown in ``tests/integration/test_diagnosis_gate.py``; this is
the report, through ``meridian report build --diagnoses`` and ``verify``, with
every database connection and socket refused, and its arithmetic on a run
written by hand.

Reference: docs/DECISIONS.md D-105, D-270, D-278; ``EVALUATION.md`` §11.2.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from meridian.cli import main
from meridian.datasets.diagnosis_runs import (
    publish_diagnosis_run,
    read_diagnosis_run,
)
from meridian.datasets.publish import read_directory
from meridian.reports.diagnosis import (
    ANSWERS,
    EFFECTS_REVIEWED,
    diagnosis_rows,
)
from meridian.reports.diagnosis_truth import TRUTHS
from meridian.reports.render import render_report
from meridian.reports.render_diagnosis import render_diagnosis

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "analysis" / "configs" / "evaluation.toml.example"
T0 = datetime(2026, 8, 12, tzinfo=UTC)


def line(event: str, kind: str, at: datetime, **more: object) -> str:
    return json.dumps(
        {
            "ledger": 1,
            "event": event,
            "run_id": "run-1",
            "kind": kind,
            "target": "station:1",
            "at": at.isoformat().replace("+00:00", "Z"),
        }
        | more
    )


def acted(kind: str, *assignments: str, minutes: int) -> list[str]:
    at = T0 + timedelta(minutes=minutes)
    return [
        line("open", kind, at, station_id="st_a", seed=11),
        line("act", kind, at + timedelta(minutes=1), assignment_ids=list(assignments)),
        line("close", kind, at + timedelta(minutes=2)),
    ]


LEDGER = (
    "\n".join(
        [
            *acted("receiver_down", "as_down", "as_down_2", minutes=0),
            *acted("clock_step", "as_step", "as_quiet", minutes=10),
            *acted("decoder_degraded", "as_decoder", minutes=20),
            *acted("obstruction", "as_both", minutes=30),
            *acted("interference", "as_both", minutes=40),
        ]
    )
    + "\n"
)

LOSSES = (
    # assignment, outcome, clean outcome, diagnosed
    ("as_down", "not_attempted", "decoded", "station_not_listening"),
    ("as_down_2", "not_attempted", "decoded", "timing_fault"),
    ("as_step", "no_signal", "decoded", "timing_fault"),
    ("as_quiet", "no_signal", "no_signal", "timing_fault"),
    ("as_decoder", "signal_no_decode", "decoded", "undetermined"),
    ("as_both", "no_signal", "decoded", "interference"),
    ("as_model", "no_signal", "no_signal", "undetermined"),
    ("as_model_2", "no_signal", "no_signal", "obstruction"),
)


def seal(root: Path, seed: int = 4471) -> Path:
    cases = [
        {
            "assignment_id": name,
            "station_index": 1,
            "state": "reported",
            "outcome": outcome,
            "clean_outcome": clean,
            "simulated": True,
        }
        for name, outcome, clean, _ in LOSSES
    ]
    diagnoses = [
        {"assignment_id": name, "revision": 1, "cause": cause, "simulated": True}
        for name, _, _, cause in LOSSES
    ]
    run = {
        "scenario": "diagnosis",
        "master_seed": seed,
        "stations": 2,
        "hours": 12.0,
        "simulated": True,
    }
    return publish_diagnosis_run(
        LEDGER,
        run,
        cases,
        diagnoses,
        root=root,
        stamp=("0029", T0, T0 + timedelta(hours=12)),
    ).path


@dataclass
class Raw:
    """The one thing the section reads from a raw snapshot: its files."""

    files: dict[str, bytes] = field(default_factory=dict)


def measured_diagnoses() -> Raw:
    rows = [
        {"assignment_id": "as_real_1", "cause": "interference", "simulated": False},
        {"assignment_id": "as_real_2", "cause": "undetermined", "simulated": False},
        {"assignment_id": "as_sim", "cause": "obstruction", "simulated": True},
    ]
    return Raw(
        {
            "loss_diagnoses.jsonl": b"".join(
                json.dumps(one).encode() + b"\n" for one in rows
            )
        }
    )


def section(tmp_path: Path, raw: Raw | None = None) -> list[dict[str, Any]]:
    run = read_diagnosis_run(seal(tmp_path))
    return diagnosis_rows([run], raw or Raw())  # type: ignore[arg-type]


def of(rows: Sequence[Mapping[str, Any]], kind: str) -> list[Mapping[str, Any]]:
    return [one for one in rows if one["row"] == kind]


def test_the_matrix_is_every_truth_by_every_answer(tmp_path: Path) -> None:
    cells = {
        (one["truth"], one["diagnosed"]): one["count"]
        for one in of(section(tmp_path), "cell")
    }

    assert set(cells) == {(truth, answer) for truth in TRUTHS for answer in ANSWERS}
    assert sum(cells.values()) == len(LOSSES)
    assert cells["station_not_listening", "station_not_listening"] == 1
    assert cells["station_not_listening", "timing_fault"] == 1
    assert cells["control", "undetermined"] == 1
    assert cells["none", "obstruction"] == 1


def test_recall_counts_its_cases_and_says_when_there_are_none(tmp_path: Path) -> None:
    recall = {one["cause"]: one for one in of(section(tmp_path), "recall")}

    assert (
        recall["station_not_listening"]["cases"],
        recall["station_not_listening"]["named"],
    ) == (2, 1)
    assert recall["station_not_listening"]["recall"]["estimate"] == 0.5
    assert recall["station_not_listening"]["recall"]["low"] < 0.5
    assert recall["timing_fault"]["cases"] == 1
    assert recall["satellite_silent"]["cases"] == 0
    assert recall["satellite_silent"]["recall"] is None


def test_naming_a_fault_that_was_there_is_not_naming_a_wrong_one(
    tmp_path: Path,
) -> None:
    """as_quiet (clock acted, not the cause) and as_both (several) name a fault
    that acted; as_down_2 and as_model_2 name one that did not."""
    (fractions,) = of(section(tmp_path), "fractions")

    assert fractions["diagnoses"] == len(LOSSES)
    assert fractions["wrong"] == 2
    assert fractions["undetermined"] == 2
    assert fractions["wrong_fraction"] == 2 / len(LOSSES)


def test_sc8_is_not_met_and_never_claimed_unreviewed(tmp_path: Path) -> None:
    (sc8,) = of(section(tmp_path), "sc8")

    assert sc8["status"] == "measured"
    assert sc8["met"] is False
    assert sc8["claimable"] is False
    # as_both is two faults' loss: obstruction and interference have no case alone.
    assert set(sc8["causes_without_cases"]) == {
        "satellite_silent",
        "obstruction",
        "interference",
    }


def test_real_cases_are_the_measured_ones_alone_and_none_is_labelled(
    tmp_path: Path,
) -> None:
    real = {
        one["cause"]: one for one in of(section(tmp_path, measured_diagnoses()), "real")
    }

    assert real["interference"]["diagnosed"] == 1
    assert real["undetermined"]["diagnosed"] == 1
    assert real["obstruction"]["diagnosed"] == 0
    assert all(
        one["labelled"] == 0 and one["simulated"] is False for one in real.values()
    )


def test_every_simulated_row_says_so(tmp_path: Path) -> None:
    for one in section(tmp_path):
        if one["row"] != "real":
            assert one["simulated"] is True, one


def test_without_runs_it_is_not_measured_and_says_why() -> None:
    rows = diagnosis_rows([], Raw())  # type: ignore[arg-type]

    (sc8,) = of(rows, "sc8")
    assert sc8["status"] == "not_measured"
    text = "\n".join(render_diagnosis(rows))
    assert "Not measured: no sealed diagnosis run was given." in text
    assert "### Real cases (measured, apart)" in text


def test_the_section_states_its_claim_and_its_missing_review(tmp_path: Path) -> None:
    text = "\n".join(render_diagnosis(section(tmp_path)))

    assert "Fault effects not independently reviewed (D-270)" in text
    assert "does not claim real-world diagnostic accuracy" in text
    assert "### Confusion matrix (simulated)" in text
    assert "**not met**, and not claimed (D-270)" in text


def test_the_review_flag_follows_the_specification() -> None:
    """D-270: SC-8 can only be claimed once the review is recorded there."""
    spec = (REPO / "docs" / "SCALE-AND-FAULTS.md").read_text()

    assert EFFECTS_REVIEWED is ("**Review:** pending" not in spec)


# --- through `meridian report` -------------------------------------------


@pytest.fixture
def guarded(no_network: Any, network_guard: Any) -> Any:
    network_guard()
    return no_network


def report(root: Path, *args: str) -> int:
    return main(["report", "--root", str(root), *args])


def test_built_with_sealed_runs_it_verifies_and_regenerates(
    raw_snapshot: Any,
    archive_world: Any,
    datasets_root: Path,
    guarded: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    snapshot = raw_snapshot(archive_world)
    runs = [seal(datasets_root, seed) for seed in (4471, 4472)]
    extra = [arg for one in runs for arg in ("--diagnoses", str(one))]

    assert (
        report(
            datasets_root,
            "build",
            "--snapshot",
            str(snapshot),
            "--config",
            str(EXAMPLE),
            "--seed",
            "4471",
            *extra,
        )
        == 0
    )
    first = capsys.readouterr().out.splitlines()[0]
    run = Path(first.split(": ", 1)[1].rsplit(" (", 1)[0])

    assert report(datasets_root, "verify", str(run)) == 0
    assert "regenerates identically" in capsys.readouterr().out
    files = read_directory(run).files
    parsed = {
        name.removesuffix(".jsonl"): [json.loads(one) for one in data.splitlines()]
        for name, data in files.items()
        if name.endswith(".jsonl")
    }
    assert render_report(parsed) == files["report.md"]
    assert "## Loss diagnosis" in files["report.md"].decode()
    (described,) = of(parsed["diagnosis"], "runs")
    assert [one["master_seed"] for one in described["runs"]] == [4471, 4472]
    assert guarded.attempts == []


def test_a_run_whose_diagnosis_run_is_gone_cannot_be_verified(
    raw_snapshot: Any,
    archive_world: Any,
    datasets_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    snapshot = raw_snapshot(archive_world)
    sealed = seal(tmp_path / "kept")
    copy = tmp_path / "copy"
    shutil.copytree(sealed, copy)
    assert (
        report(
            datasets_root,
            "build",
            "--snapshot",
            str(snapshot),
            "--config",
            str(EXAMPLE),
            "--seed",
            "4471",
            "--diagnoses",
            str(copy),
        )
        == 0
    )
    printed = capsys.readouterr().out.splitlines()[0]
    run = Path(printed.split(": ", 1)[1].rsplit(" (", 1)[0])
    for path in (copy, *copy.rglob("*")):
        path.chmod(0o700)
    shutil.rmtree(copy)

    assert report(datasets_root, "verify", str(run)) == 1
    assert "name it with --diagnoses" in capsys.readouterr().err
    assert report(datasets_root, "verify", str(run), "--diagnoses", str(sealed)) == 0
