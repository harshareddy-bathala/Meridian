"""``deploy/tools/scale_probe.py`` — what the platform showed of itself, decided
without a platform.

The probe scrapes ``/metrics``; everything it concludes is arithmetic over two
scrapes, pinned here on stated exposition text: that a quantile is a bucket's
upper bound over the run and not over all time, that counters are compared
rather than read, and that the series count is the one D-197 holds constant.

Marked as a unit test by living in ``tests/unit``: no network.

Reference: docs/DECISIONS.md D-197.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "deploy/tools"

BEFORE = """
# HELP meridian_http_request_duration_seconds Time.
# TYPE meridian_http_request_duration_seconds histogram
meridian_http_request_duration_seconds_bucket{le="0.01",route="/msp/v0/heartbeat"} 90.0
meridian_http_request_duration_seconds_bucket{le="0.05",route="/msp/v0/heartbeat"} 100.0
meridian_http_request_duration_seconds_bucket{le="+Inf",route="/msp/v0/heartbeat"} 100.0
meridian_http_request_duration_seconds_count{route="/msp/v0/heartbeat"} 100.0
meridian_msp_heartbeats_total{simulated="true"} 100.0
meridian_db_pool_connections{kind="size"} 2.0
meridian_db_pool_connections{kind="available"} 2.0
meridian_db_pool_connections{kind="waiting"} 0.0
meridian_stations{liveness="online",simulated="true"} 5.0
"""

AFTER = """
meridian_http_request_duration_seconds_bucket{le="0.01",route="/msp/v0/heartbeat"} 100.0
meridian_http_request_duration_seconds_bucket{le="0.05",route="/msp/v0/heartbeat"} 180.0
meridian_http_request_duration_seconds_bucket{le="+Inf",route="/msp/v0/heartbeat"} 200.0
meridian_http_request_duration_seconds_count{route="/msp/v0/heartbeat"} 200.0
meridian_msp_heartbeats_total{simulated="true"} 200.0
meridian_db_pool_connections{kind="size"} 4.0
meridian_db_pool_connections{kind="available"} 1.0
meridian_db_pool_connections{kind="waiting"} 3.0
meridian_stations{liveness="online",simulated="true"} 4.0
meridian_stations{liveness="offline",simulated="true"} 1.0
"""


@pytest.fixture(scope="module")
def probe() -> Iterator[ModuleType]:
    """The tool, imported as running it does."""
    spec = importlib.util.spec_from_file_location(
        "scale_probe", TOOLS / "scale_probe.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["scale_probe"] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop("scale_probe", None)


def test_a_quantile_is_over_the_run_not_all_time(probe: ModuleType) -> None:
    """Before the run 90 of 100 were fast; during it, 10 of 100 were."""
    before, after = probe.parse(BEFORE), probe.parse(AFTER)
    duration = "meridian_http_request_duration_seconds"

    assert probe.quantile(before, after, duration, 0.1) == 0.01
    assert probe.quantile(before, after, duration, 0.5) == 0.05
    assert probe.quantile(before, after, duration, 0.95) == float("inf")


def test_nothing_observed_has_no_quantile(probe: ModuleType) -> None:
    """None, never zero: zero seconds would be a measurement."""
    same = probe.parse(AFTER)

    assert (
        probe.quantile(same, same, "meridian_http_request_duration_seconds", 0.5)
        is None
    )


def test_a_counter_is_compared_between_scrapes(probe: ModuleType) -> None:
    """What the run did, not what the process has done since it started."""
    assert (
        probe.delta(
            probe.parse(BEFORE), probe.parse(AFTER), "meridian_msp_heartbeats_total"
        )
        == 100.0
    )


def test_the_report_reads_pool_pressure_at_its_worst(probe: ModuleType) -> None:
    """The fewest idle and the most waiting at any scrape, not at the last."""
    middle = probe.parse(BEFORE.replace('kind="waiting"} 0.0', 'kind="waiting"} 7.0'))
    report = probe.summarise(
        [probe.parse(BEFORE), middle, probe.parse(AFTER)], [], 60.0
    )

    assert report["simulated"] is True
    assert report["pool_available_min"] == 1.0
    assert report["pool_waiting_max"] == 7.0
    assert report["pool_size_max"] == 4.0
    assert report["heartbeats_accepted_per_s"] == round(100 / 60, 3)
    assert report["routes"]["/msp/v0/heartbeat"]["requests"] == 100.0
    assert report["stations_by_liveness"] == {"online": 4.0, "offline": 1.0}


def test_series_are_counted_by_prefix(probe: ModuleType) -> None:
    """The number D-197 holds constant as the fleet grows."""
    samples = probe.parse(AFTER + "process_cpu_seconds_total 1.0\n")

    assert probe.series_count(samples) == 10


def test_a_probe_scrapes_first_and_last_and_between(probe: ModuleType) -> None:
    """Every ``every`` seconds for ``seconds``, both ends included."""
    scrapes: list[str] = []
    texts = iter([BEFORE, BEFORE, AFTER])

    def fetch(url: str, _token: str) -> object:
        scrapes.append(url)
        return probe.parse(next(texts))

    report = probe.probe(
        "http://api", None, "t", 30.0, 15.0, sleep=lambda _s: None, fetch=fetch
    )

    assert scrapes == ["http://api"] * 3
    assert report["scrapes"] == 3
    assert report["window_s"] == 30.0


JOBS_BEFORE = """
meridian_job_duration_seconds_bucket{le="1.0",task="schedule"} 1.0
meridian_job_duration_seconds_bucket{le="5.0",task="schedule"} 1.0
meridian_job_duration_seconds_bucket{le="+Inf",task="schedule"} 1.0
meridian_job_duration_seconds_count{task="schedule"} 1.0
meridian_job_duration_seconds_sum{task="schedule"} 0.5
meridian_scheduler_runs_total{status="optimal"} 1.0
meridian_scheduler_candidates 3.0
meridian_scheduler_solver_seconds 0.01
"""

JOBS_AFTER = """
meridian_job_duration_seconds_bucket{le="1.0",task="schedule"} 1.0
meridian_job_duration_seconds_bucket{le="5.0",task="schedule"} 3.0
meridian_job_duration_seconds_bucket{le="+Inf",task="schedule"} 3.0
meridian_job_duration_seconds_count{task="schedule"} 3.0
meridian_job_duration_seconds_sum{task="schedule"} 6.5
meridian_scheduler_runs_total{status="optimal"} 2.0
meridian_scheduler_runs_total{status="fallback"} 1.0
meridian_scheduler_candidates 2.0
meridian_scheduler_solver_seconds 0.02
"""


def test_the_jobs_summary_reads_the_run_and_the_largest_gauge(
    probe: ModuleType,
) -> None:
    """Rounds and durations over the run; the last-run gauges at their largest."""
    middle = probe.parse(JOBS_BEFORE.replace("candidates 3.0", "candidates 95.0"))
    report = probe.summarise(
        [probe.parse(BEFORE)] * 3,
        [probe.parse(JOBS_BEFORE), middle, probe.parse(JOBS_AFTER)],
        60.0,
    )

    assert report["scheduler_runs"] == 2.0
    assert report["scheduler_candidates_max"] == 95.0
    assert report["solver_seconds_max"] == 0.02
    assert report["job_tasks"]["schedule"] == {
        "rounds": 2.0,
        "mean_s": 3.0,
        "p95_s": 5.0,
    }
    assert report["job_tasks"]["pass_generation"]["mean_s"] is None
