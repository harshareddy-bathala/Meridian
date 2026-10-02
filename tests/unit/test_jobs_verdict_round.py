"""The verdict half of a round: run only when configured, recorded, survived.

Reference: docs/DECISIONS.md D-110, D-263.
"""

from __future__ import annotations

from datetime import UTC, datetime

from prometheus_client import REGISTRY

from meridian.cli_verdict import apply_lines
from meridian.jobs.job_metrics import TASKS, VERDICTS
from meridian.jobs.verdict_round import run_verdict_round
from meridian.verdict_build import VerdictBuildReport

NOW = datetime(2026, 9, 14, 6, 0, tzinfo=UTC)
REPORT = VerdictBuildReport(
    method="verdict-1:0123456789ab",
    scored=3,
    written=3,
    routes={"full": 2, "outcome": 1},
    simulated=1,
)


class _Work:
    def __init__(self, *, fails: bool = False) -> None:
        self.fails = fails
        self.asked: list[datetime] = []

    def apply(self, now: datetime) -> VerdictBuildReport:
        self.asked.append(now)
        if self.fails:
            raise RuntimeError("the database went away")
        return REPORT


def sample(name: str) -> float:
    return REGISTRY.get_sample_value(name, {"task": VERDICTS}) or 0.0


def test_no_model_configured_runs_nothing_and_records_nothing() -> None:
    before = sample("meridian_job_duration_seconds_count")

    assert run_verdict_round(None, NOW) is None
    assert sample("meridian_job_duration_seconds_count") == before


def test_a_configured_round_applies_at_the_rounds_instant() -> None:
    work = _Work()

    assert run_verdict_round(work, NOW) == REPORT
    assert work.asked == [NOW]
    assert sample("meridian_job_last_success_timestamp_seconds") > 0


def test_a_failure_is_counted_and_survived() -> None:
    before = sample("meridian_job_failures_total")

    assert run_verdict_round(_Work(fails=True), NOW) is None
    assert sample("meridian_job_failures_total") == before + 1


def test_the_verdict_task_runs_before_the_diagnosis_that_reads_it() -> None:
    assert TASKS[-2] == VERDICTS


def test_apply_says_what_it_did() -> None:
    assert apply_lines(REPORT) == [
        "verdicts by verdict-1:0123456789ab",
        "  scored             3 (1 simulated)",
        "  written            3",
        "  routes             full 2 · outcome 1",
    ]
