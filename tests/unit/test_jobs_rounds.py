"""A round of scheduled work records itself, survives a failure, and stops.

The tasks are stubs: what is pinned here is the supervision around them — that a
failed pass generation still lets scheduling run, that success and failure are
both visible to Prometheus, and that the loop ends when told to.

Marked as a unit test by living in ``tests/unit``: no network, no database.

Reference: docs/DECISIONS.md D-109, D-110.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from prometheus_client import REGISTRY

from meridian.jobs.job_metrics import PASS_GENERATION, SCHEDULE
from meridian.jobs.rounds import RoundPlan, run_round, run_until_stopped
from meridian.pass_generation import GenerationHorizon, GenerationReport
from meridian.scheduler.run import ScheduleReport, ScheduleRequest

NOW = datetime(2026, 9, 14, 6, 0, tzinfo=UTC)
PLAN = RoundPlan(horizon=timedelta(hours=6), model_config="A", turnaround_s=0.0)


class _Work:
    """Two tasks that record what they were asked and can be made to fail."""

    def __init__(self, *, generation_fails: bool = False) -> None:
        self.generation_fails = generation_fails
        self.horizons: list[GenerationHorizon] = []
        self.requests: list[ScheduleRequest] = []

    def generate(self, horizon: GenerationHorizon) -> GenerationReport:
        self.horizons.append(horizon)
        if self.generation_fails:
            raise RuntimeError("the database went away")
        return GenerationReport(
            stations_considered=1,
            pairs_propagated=3,
            passes_computed=7,
            passes_stored=7,
            satellites_without_element_set=(),
        )

    def schedule(self, request: ScheduleRequest) -> ScheduleReport:
        self.requests.append(request)
        return ScheduleReport(
            model_config=request.model_config,
            stations_considered=1,
            candidates_considered=7,
            scheduled=5,
            skipped=2,
            rows_written=7,
            passes_without_a_usable_transmitter=(),
        )


def sample(name: str, task: str | None = None) -> float | None:
    """One sample from the process registry, labelled by task when given."""
    return REGISTRY.get_sample_value(name, {"task": task} if task else {})


def test_a_round_covers_the_horizon_from_now_for_both_tasks() -> None:
    """Both tasks see the same half-open interval, under configuration A."""
    work = _Work()

    outcome = run_round(work, PLAN, NOW)

    assert work.horizons == [GenerationHorizon(start=NOW, end=NOW + PLAN.horizon)]
    (request,) = work.requests
    assert (request.start, request.end, request.model_config) == (
        NOW,
        NOW + PLAN.horizon,
        "A",
    )
    assert outcome.generated is not None
    assert outcome.scheduled is not None


def test_a_successful_round_is_visible_to_prometheus() -> None:
    """Last success, duration and the two size gauges all move."""
    before = sample("meridian_job_duration_seconds_count", SCHEDULE) or 0.0

    run_round(_Work(), PLAN, NOW)

    assert sample("meridian_job_last_success_timestamp_seconds", SCHEDULE)
    assert sample("meridian_job_duration_seconds_count", SCHEDULE) == before + 1
    assert sample("meridian_scheduler_candidates") == 7.0
    assert sample("meridian_passes_computed") == 7.0


def test_a_failed_generation_is_counted_and_scheduling_still_runs(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stored horizon from earlier rounds is still worth scheduling.

    The logger is re-enabled for this test: a migration test run earlier in the
    same process applies alembic's ``fileConfig``, whose default disables every
    logger that already exists, and the log line is part of what is asserted.
    """
    monkeypatch.setattr(logging.getLogger("meridian.jobs.rounds"), "disabled", False)
    failures = sample("meridian_job_failures_total", PASS_GENERATION) or 0.0
    work = _Work(generation_fails=True)

    outcome = run_round(work, PLAN, NOW)

    assert outcome.generated is None
    assert outcome.scheduled is not None
    assert len(work.requests) == 1
    assert sample("meridian_job_failures_total", PASS_GENERATION) == failures + 1
    assert "pass_generation failed" in caplog.text


def test_the_loop_repeats_rounds_until_the_stop_event_is_set() -> None:
    """Rounds keep coming at the interval, and the third one's stop is honoured."""
    stop = threading.Event()
    calls: list[int] = []

    def round_once() -> None:
        calls.append(len(calls))
        if len(calls) == 3:
            stop.set()

    assert run_until_stopped(round_once, 0.001, stop) == 3


def test_a_stop_during_the_wait_ends_it_at_once() -> None:
    """SIGTERM between rounds must not wait out the interval.

    The interval is an hour; the stop arrives from another thread, as the signal
    handler's does, a moment into the wait. A wait that ignored the event would
    hold this test for the hour, so the elapsed time is asserted too.
    """
    stop = threading.Event()
    timer = threading.Timer(0.05, stop.set)
    timer.start()
    started = time.monotonic()

    rounds = run_until_stopped(lambda: None, 3600.0, stop)

    timer.join()
    assert rounds == 1
    assert time.monotonic() - started < 5.0


def test_a_stop_before_the_first_round_runs_nothing() -> None:
    """SIGTERM during start-up exits without starting a round."""
    stop = threading.Event()
    stop.set()

    assert run_until_stopped(lambda: None, 1.0, stop) == 0
