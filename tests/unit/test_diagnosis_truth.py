"""A sealed diagnosis run, and the truth each of its diagnoses is judged against.

The run's files are written here by hand: a ledger, the fleet's cases and the
platform's diagnoses. Sealing refuses a measured row and reads back verified;
the truth is a cause only where exactly one fault acted and the pass came out
worse than its clean outcome (D-278).

Reference: docs/DECISIONS.md D-105, D-253, D-277, D-278.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from meridian.datasets.diagnosis_runs import (
    DIAGNOSES,
    NotADiagnosisRunError,
    publish_diagnosis_run,
    read_diagnosis_run,
)
from meridian.datasets.fault_runs import publish_fault_run
from meridian.datasets.publish import DamagedSnapshotError
from meridian.reports.diagnosis_truth import judge_run

T0 = datetime(2026, 8, 12, tzinfo=UTC)
STAMP = ("0029", T0, T0 + timedelta(days=1))
RUN = {"scenario": "diagnosis", "master_seed": 4471, "stations": 2, "simulated": True}


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


def acted(kind: str, *assignments: str, minutes: int = 0) -> list[str]:
    at = T0 + timedelta(minutes=minutes)
    return [
        line("open", kind, at, station_id="st_a", seed=11),
        line("act", kind, at + timedelta(minutes=1), assignment_ids=list(assignments)),
        line("close", kind, at + timedelta(minutes=2)),
    ]


LEDGER = (
    "\n".join(
        [
            *acted("receiver_down", "as_down", minutes=0),
            *acted("clock_step", "as_stepped", "as_quiet_anyway", minutes=10),
            *acted("decoder_degraded", "as_decoder", minutes=20),
            *acted("obstruction", "as_both", minutes=30),
            *acted("interference", "as_both", minutes=40),
        ]
    )
    + "\n"
)


def case(assignment_id: str, outcome: str | None, clean: str) -> dict[str, object]:
    return {
        "assignment_id": assignment_id,
        "station_index": 1,
        "state": "reported" if outcome else "held",
        "outcome": outcome,
        "clean_outcome": clean,
        "simulated": True,
    }


CASES = [
    case("as_down", "not_attempted", "decoded"),
    case("as_stepped", "no_signal", "decoded"),
    case("as_quiet_anyway", "no_signal", "no_signal"),
    case("as_decoder", "signal_no_decode", "decoded"),
    case("as_both", "no_signal", "decoded"),
    case("as_model", "no_signal", "no_signal"),
]


def diagnosis(assignment_id: str, cause: str) -> dict[str, object]:
    return {
        "assignment_id": assignment_id,
        "revision": 1,
        "cause": cause,
        "simulated": True,
    }


DIAGNOSED = [
    diagnosis("as_down", "station_not_listening"),
    diagnosis("as_stepped", "timing_fault"),
    diagnosis("as_quiet_anyway", "timing_fault"),
    diagnosis("as_decoder", "undetermined"),
    diagnosis("as_both", "interference"),
    diagnosis("as_model", "undetermined"),
]


@pytest.fixture
def sealed(tmp_path: Path) -> Path:
    return publish_diagnosis_run(
        LEDGER, RUN, CASES, DIAGNOSED, root=tmp_path, stamp=STAMP
    ).path


def test_a_run_reads_back_as_it_was_sealed(sealed: Path) -> None:
    run = read_diagnosis_run(sealed)

    assert sealed.parent.name == DIAGNOSES
    assert run.run == RUN
    assert len(run.faults) == 5
    assert [one["assignment_id"] for one in run.cases] == [
        c["assignment_id"] for c in CASES
    ]
    assert run.directory.manifest.counts == {
        "cases.simulated": 6,
        "diagnoses.simulated": 6,
    }


def test_each_diagnosis_meets_its_truth(sealed: Path) -> None:
    truths = {
        one.assignment_id: (one.truth, one.diagnosed)
        for one in judge_run(read_diagnosis_run(sealed))
    }

    assert truths == {
        "as_down": ("station_not_listening", "station_not_listening"),
        "as_stepped": ("timing_fault", "timing_fault"),
        # The clock moved it, and it would have heard nothing anyway.
        "as_quiet_anyway": ("acted_not_cause", "timing_fault"),
        "as_decoder": ("control", "undetermined"),
        "as_both": ("several", "interference"),
        "as_model": ("none", "undetermined"),
    }


def test_the_same_files_seal_to_the_same_name(tmp_path: Path) -> None:
    one = publish_diagnosis_run(
        LEDGER, RUN, CASES, DIAGNOSED, root=tmp_path / "a", stamp=STAMP
    )
    two = publish_diagnosis_run(
        LEDGER, RUN, CASES, DIAGNOSED, root=tmp_path / "b", stamp=STAMP
    )

    assert one.path.name == two.path.name


def test_a_measured_row_is_refused(tmp_path: Path) -> None:
    measured = [*DIAGNOSED[:-1], {**DIAGNOSED[-1], "simulated": False}]

    with pytest.raises(ValueError, match="1 rows are not simulated"):
        publish_diagnosis_run(LEDGER, RUN, CASES, measured, root=tmp_path, stamp=STAMP)


def test_a_changed_file_is_refused(sealed: Path) -> None:
    target = sealed / "diagnoses.jsonl"
    target.chmod(0o600)
    target.write_text(target.read_text().replace("interference", "obstruction"))

    with pytest.raises(DamagedSnapshotError):
        read_diagnosis_run(sealed)


def test_another_kind_of_directory_is_refused_by_name(tmp_path: Path) -> None:
    other = publish_fault_run(LEDGER, (), (), root=tmp_path, stamp=("0029", T0)).path

    with pytest.raises(NotADiagnosisRunError, match="fault run, not a diagnosis"):
        read_diagnosis_run(other)
