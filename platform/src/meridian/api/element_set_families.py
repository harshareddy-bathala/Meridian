"""Element-set age as Prometheus sees it: how old each satellite's current set is.

Read by :mod:`meridian.api.domain_collector` in a borrow of its own, so a failed
read loses this series and nothing else.

``meridian_element_set_age_seconds{satellite_id}`` is the time since the epoch
of the set the next round would propagate. A set's along-track error grows with
its age, so this is the number that says how far the dashboard's acquisition
times can be trusted; ``ElementSetsStale`` fires on it (D-321). The label is a
satellite, never a station, so the series count is the catalogue's size and
does not grow with the fleet (D-197). A satellite with no set publishes
nothing, because it has no age.

Reference: docs/DECISIONS.md D-109, D-197, D-320, D-321.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import datetime

import psycopg
from prometheus_client.metrics_core import GaugeMetricFamily, Metric
from psycopg import Connection
from psycopg_pool import ConnectionPool

from meridian.store.element_sets import find_current_epochs

__all__ = ["element_set_age_family", "read_current_epochs"]

_log = logging.getLogger(__name__)

Pool = ConnectionPool[Connection[tuple[object, ...]]]


def read_current_epochs(
    pool: Pool, now: datetime, timeout_s: float
) -> dict[str, datetime] | None:
    """Each tracked satellite's current epoch, or ``None`` when the read fails."""
    try:
        with pool.connection(timeout=timeout_s) as conn:
            return find_current_epochs(conn, now)
    except (psycopg.Error, OSError):
        _log.warning("scrape-time element-set read failed", exc_info=True)
        return None


def element_set_age_family(
    epochs: dict[str, datetime], now: datetime
) -> Iterator[Metric]:
    """One gauge: seconds since each satellite's current epoch."""
    family = GaugeMetricFamily(
        "meridian_element_set_age_seconds",
        "Seconds since the epoch of the element set the next round propagates, "
        "by satellite.",
        labels=["satellite_id"],
    )
    for satellite_id in sorted(epochs):
        family.add_metric([satellite_id], (now - epochs[satellite_id]).total_seconds())
    yield family
