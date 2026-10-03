"""The diagnosis half of a round: always run, recorded, survived, and last.

Reference: docs/DECISIONS.md D-110, D-272.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from prometheus_client import REGISTRY

from meridian.cli_diagnosis import explain_lines, run_lines
from meridian.jobs.diagnosis_round import run_diagnosis_round
from meridian.jobs.job_metrics import DIAGNOSIS, TASKS, VERDICTS
from meridian.reliability.diagnosis_run import DiagnosisRunReport
from meridian.store.loss_diagnoses import StoredDiagnosis

NOW = datetime(2026, 9, 14, 6, 0, tzinfo=UTC)
REPORT = DiagnosisRunReport(
    method="diagnosis-1",
    config_sha256=bytes.fromhex("ab" * 32),
    verdict_method=None,
    diagnosed=4,
    written=4,
    by_cause={"interference": 1, "timing_fault": 0, "undetermined": 3},
    simulated=4,
)


class _Work:
    def __init__(self, *, fails: bool = False) -> None:
        self.fails = fails
        self.asked: list[datetime] = []

    def diagnose(self, now: datetime) -> DiagnosisRunReport:
        self.asked.append(now)
        if self.fails:
            raise RuntimeError("the database went away")
        return REPORT


def sample(name: str) -> float:
    return REGISTRY.get_sample_value(name, {"task": DIAGNOSIS}) or 0.0


def test_a_round_diagnoses_at_the_rounds_instant() -> None:
    work = _Work()

    assert run_diagnosis_round(work, NOW) == REPORT
    assert work.asked == [NOW]
    assert sample("meridian_job_last_success_timestamp_seconds") > 0


def test_a_failure_is_counted_and_survived() -> None:
    before = sample("meridian_job_failures_total")

    assert run_diagnosis_round(_Work(fails=True), NOW) is None
    assert sample("meridian_job_failures_total") == before + 1


def test_the_diagnosis_runs_last_after_the_verdicts_it_reads() -> None:
    assert TASKS[-2:] == (VERDICTS, DIAGNOSIS)


def test_a_run_says_what_it_did_and_that_no_decode_was_partial() -> None:
    assert run_lines(REPORT) == [
        "diagnoses by diagnosis-1, thresholds abababababab",
        "  diagnosed          4 (4 simulated)",
        "  written            4",
        "  causes             interference 1 · undetermined 3",
        "  partial            no verdict model: no decode is diagnosed as partial",
    ]


def test_a_run_names_what_it_could_not_read() -> None:
    lines = run_lines(replace(REPORT, unreadable=("as_x: element set 7 is gone",)))

    assert "  unreadable         as_x: element set 7 is gone" in lines


def test_explain_prints_every_cause_tested() -> None:
    row = StoredDiagnosis(
        diagnosis_id=1,
        assignment_id="as_one",
        revision=None,
        station_id="st_one",
        classification_id=7,
        cause="timing_fault",
        candidates_json=[
            {"cause": "timing_fault", "fired": True, "support": 0.97, "found": {}},
            {"cause": "obstruction", "fired": False, "support": 0.0, "found": {}},
        ],
        evidence_json={},
        method="diagnosis-1",
        config_sha256=bytes(32),
        verdict_method=None,
        computed_at=NOW,
        simulated=True,
    )

    lines = explain_lines("as_one", [row])

    assert lines[0].startswith("as_one no report (simulated): timing_fault")
    assert "fired  timing_fault" in lines[1]
    assert "obstruction" in lines[2]
    assert "fired" not in lines[2]


def test_explain_says_when_there_is_nothing() -> None:
    assert explain_lines("as_none", []) == [
        "as_none: no diagnosis (not lost, or not yet classified)"
    ]
