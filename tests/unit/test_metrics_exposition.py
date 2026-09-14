"""One scrape: the process's counters, then whatever is computed at scrape time.

``prometheus_client`` chooses how values are stored when it is first imported, so
a test process cannot switch into multiprocess mode partway through. What is
tested here is the decision and the composition: which mode an environment
selects, and that extra collectors are reported without being registered on the
shared default registry.

Marked as a unit test by living in ``tests/unit``: no network, no database.

Reference: docs/DECISIONS.md D-109.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from prometheus_client import REGISTRY
from prometheus_client.metrics_core import GaugeMetricFamily, Metric
from prometheus_client.registry import Collector

from meridian.metrics.exposition import (
    MULTIPROCESS_DIRECTORY_VARIABLE,
    build_scrape_source,
    exposition,
    is_multiprocess,
)

EXTRA_NAME = "meridian_unit_exposition_extra"


class _OneGauge(Collector):
    """A scrape-time collector reporting a single fixed value."""

    def collect(self) -> Iterator[Metric]:
        family = GaugeMetricFamily(EXTRA_NAME, "A value computed at scrape time.")
        family.add_metric([], 7.0)
        yield family


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({}, False),
        ({MULTIPROCESS_DIRECTORY_VARIABLE: ""}, False),
        ({MULTIPROCESS_DIRECTORY_VARIABLE: "/run/meridian/metrics"}, True),
    ],
)
def test_multiprocess_mode_follows_the_library_variable(
    environ: dict[str, str], expected: bool
) -> None:
    """Set and non-empty means multiprocess, exactly as the library decides."""
    assert is_multiprocess(environ) is expected


def test_a_scrape_carries_the_process_counters_and_the_extra_collector() -> None:
    """Both halves reach the text a scrape returns."""
    text = exposition(build_scrape_source([_OneGauge()], environ={})).decode()

    assert f"{EXTRA_NAME} 7.0" in text
    assert "python_info" in text


def test_building_a_scrape_twice_registers_nothing_globally() -> None:
    """Two applications in one process must not collide on a registered name."""
    build_scrape_source([_OneGauge()], environ={})
    build_scrape_source([_OneGauge()], environ={})

    assert REGISTRY.get_sample_value(EXTRA_NAME) is None
