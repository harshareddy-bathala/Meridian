"""Owner reports as Prometheus sees them: how many are in each state.

Read by :mod:`meridian.api.domain_collector` in a borrow of its own, so a
table a pending migration has not created loses these series and nothing else.

``meridian_report_deliveries{state, simulated}`` counts reports queued, being
retried, sent and abandoned. A population that has never had a report
publishes nothing; one that has publishes every state, zeros included, so an
alert on a state reads a number rather than an absence (D-086, D-305).

Reference: docs/DECISIONS.md D-086, D-109, D-305.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import psycopg
from prometheus_client.metrics_core import GaugeMetricFamily, Metric
from psycopg import Connection
from psycopg_pool import ConnectionPool

from meridian.store.report_deliveries import (
    DELIVERY_STATES,
    DeliveryCount,
    count_deliveries,
)

__all__ = ["delivery_families", "read_deliveries"]

_log = logging.getLogger(__name__)

Pool = ConnectionPool[Connection[tuple[object, ...]]]


def read_deliveries(pool: Pool, timeout_s: float) -> list[DeliveryCount] | None:
    """Reports by population and state, or ``None`` when the read fails."""
    try:
        with pool.connection(timeout=timeout_s) as conn:
            return count_deliveries(conn)
    except (psycopg.Error, OSError):
        _log.warning("scrape-time report read failed", exc_info=True)
        return None


def delivery_families(counts: list[DeliveryCount]) -> Iterator[Metric]:
    """One gauge: reports in each state, for each population that has any."""
    family = GaugeMetricFamily(
        "meridian_report_deliveries",
        "Owner reports by delivery state — queued, retrying, sent, abandoned — "
        "and by whether simulated.",
        labels=["state", "simulated"],
    )
    held = {(one.simulated, one.state): one.reports for one in counts}
    for simulated in sorted({one.simulated for one in counts}):
        label = "true" if simulated else "false"
        for state in DELIVERY_STATES:
            family.add_metric([state, label], float(held.get((simulated, state), 0)))
    yield family
