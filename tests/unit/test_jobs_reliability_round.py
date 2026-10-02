"""The reliability half of a round records itself and survives a failure.

The tasks are stubs; what is pinned is the supervision: the classification
still runs when the sweep fails, and both tasks' successes are visible to
Prometheus under their own ``task`` label.

Reference: docs/DECISIONS.md D-110, D-182, D-183.
"""

from __future__ import annotations

from datetime import UTC, datetime

from prometheus_client import REGISTRY

from meridian.jobs.job_metrics import EXPIRY_SWEEP, RELIABILITY, TASKS
from meridian.jobs.reliability_round import run_reliability_round
from meridian.reliability.accounting import AccountingReport

NOW = datetime(2026, 9, 14, 6, 0, tzinfo=UTC)


class _Work:
    def __init__(self, *, sweep_fails: bool = False) -> None:
        self.sweep_fails = sweep_fails
        self.asked: list[tuple[str, datetime]] = []

    def sweep(self, now: datetime) -> int:
        self.asked.append(("sweep", now))
        if self.sweep_fails:
            raise RuntimeError("the database went away")
        return 3

    def classify(self, now: datetime) -> AccountingReport:
        self.asked.append(("classify", now))
        return AccountingReport(settled_by=now, classified=2, written=2)


def failures(task: str) -> float:
    return (
        REGISTRY.get_sample_value("meridian_job_failures_total", {"task": task}) or 0.0
    )


def test_both_tasks_run_at_the_rounds_instant_in_order() -> None:
    work = _Work()

    outcome = run_reliability_round(work, NOW)

    assert work.asked == [("sweep", NOW), ("classify", NOW)]
    assert outcome.expired == 3
    assert outcome.classified is not None
    assert outcome.classified.classified == 2


def test_a_failed_sweep_is_counted_and_classification_still_runs() -> None:
    before = failures(EXPIRY_SWEEP)

    outcome = run_reliability_round(_Work(sweep_fails=True), NOW)

    assert outcome.expired is None
    assert outcome.classified is not None
    assert failures(EXPIRY_SWEEP) == before + 1


def test_each_success_is_recorded_under_its_own_task() -> None:
    run_reliability_round(_Work(), NOW)

    for task in (EXPIRY_SWEEP, RELIABILITY):
        assert REGISTRY.get_sample_value(
            "meridian_job_last_success_timestamp_seconds", {"task": task}
        )
    assert TASKS[-4:-2] == (EXPIRY_SWEEP, RELIABILITY)
