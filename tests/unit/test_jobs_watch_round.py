"""The watch half of a round: timed, survived, and last.

Reference: docs/DECISIONS.md D-110, D-293, D-294.
"""

from __future__ import annotations

from datetime import UTC, datetime

from prometheus_client import REGISTRY

from meridian.jobs.job_metrics import DIAGNOSIS, TASKS, WATCH
from meridian.jobs.watch_round import run_watch_round
from meridian.reliability.watch_build import BaselineRunReport
from meridian.reliability.watch_run import WatchRunReport

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
REPORT = WatchRunReport(
    method="watch-1",
    config_sha256=bytes(32),
    baselines=BaselineRunReport(
        method="baseline-1",
        config_sha256=bytes(32),
        trained_from=NOW,
        trained_until=NOW,
        chains=1,
        written=0,
        kept=1,
        held=0,
    ),
    judged=1,
    no_baseline=0,
    too_few=0,
    raised=("st-a capability 1: 4.0 dB short",),
    open=1,
)


class _Work:
    def __init__(self, *, fails: bool = False) -> None:
        self.fails = fails

    def watch(self, now: datetime) -> WatchRunReport:
        if self.fails:
            raise RuntimeError("database gone")
        assert now == NOW
        return REPORT


def sample(name: str) -> float:
    return REGISTRY.get_sample_value(name, {"task": WATCH}) or 0.0


def test_a_round_returns_the_report_and_records_success() -> None:
    assert run_watch_round(_Work(), NOW) == REPORT
    assert sample("meridian_job_last_success_timestamp_seconds") > 0


def test_a_failure_is_counted_and_survived() -> None:
    before = sample("meridian_job_failures_total")

    assert run_watch_round(_Work(fails=True), NOW) is None
    assert sample("meridian_job_failures_total") == before + 1


def test_the_watch_runs_last_after_the_diagnosis_it_reads() -> None:
    assert TASKS[-2:] == (DIAGNOSIS, WATCH)
