"""The reports half of a round: timed, survived, and last.

Reference: docs/DECISIONS.md D-110, D-304.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from prometheus_client import REGISTRY

from meridian.jobs.job_metrics import REPORTS, TASKS, WATCH
from meridian.jobs.report_round import DatabaseReportWork, run_report_round
from meridian.notifications.compose import Deployment, QueueReport
from meridian.notifications.config import NotificationsConfig

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
REPORT = QueueReport(passes=2, weeks=1)
DEPLOYMENT = Deployment(
    classification_method="classification-1",
    classification_sha256=bytes(32),
    diagnosis_method="diagnosis-1",
    diagnosis_sha256=bytes(32),
    verdict_method=None,
)


class _Work:
    def __init__(self, *, fails: bool = False) -> None:
        self.fails = fails

    def queue(self, now: datetime) -> QueueReport:
        if self.fails:
            raise RuntimeError("database gone")
        assert now == NOW
        return REPORT


def sample(name: str) -> float:
    return REGISTRY.get_sample_value(name, {"task": REPORTS}) or 0.0


def test_a_round_returns_the_report_and_records_success() -> None:
    assert run_report_round(_Work(), NOW) == REPORT
    assert sample("meridian_job_last_success_timestamp_seconds") > 0


def test_a_failure_is_counted_and_survived() -> None:
    before = sample("meridian_job_failures_total")

    assert run_report_round(_Work(fails=True), NOW) is None
    assert sample("meridian_job_failures_total") == before + 1


def test_reports_run_last_after_everything_they_say() -> None:
    assert TASKS[-2:] == (WATCH, REPORTS)


def test_a_deployment_listing_no_station_never_connects() -> None:
    def refuse() -> Any:
        raise AssertionError("connected with no station to report on")

    work = DatabaseReportWork(
        refuse, config=NotificationsConfig(), deployment=DEPLOYMENT
    )

    assert run_report_round(work, NOW) == QueueReport(passes=0, weeks=0)
