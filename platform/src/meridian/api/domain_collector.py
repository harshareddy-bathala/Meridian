"""The network's state as Prometheus sees it, read when Prometheus asks.

A custom collector registered on the API's scrape (D-109). Each scrape borrows one
pooled connection and reads, in one transaction:

- stations by liveness and provenance, classified by ``meridian.registry``;
- scheduled assignments by state and provenance;
- assignments whose report is overdue;
- whether the database's migration revision is the one this code expects;
- passes classified inside the SLO window, by class, and what is left of each
  population's loss budget (Stage 20, D-186).

It also reports the answering process's connection pool, and whether the database
answered at all.

**When the database does not answer, only that is reported** —
``meridian_database_reachable 0`` and the pool figures, and no station or
assignment series. A zero in those would read as "no station is offline" when
the truth is "not measured", which is the one number this project refuses to
publish (D-086, D-111).

In multiprocess mode this runs in whichever worker answers the scrape, so the
database figures are the same from any worker and the pool figures describe that
worker's pool.

**A population with nothing classified in the window publishes no reliability
series**, for the same reason: a column of zeros would read as "no misses" when
the truth is that nothing has settled yet (D-086, D-186).

Reference: docs/DECISIONS.md D-054, D-086, D-109, D-111, D-186.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta

import psycopg
from prometheus_client.metrics_core import GaugeMetricFamily, Metric
from prometheus_client.registry import Collector
from psycopg import Connection
from psycopg_pool import ConnectionPool

from meridian.api import platform_clock
from meridian.registry.liveness_counts import count_by_liveness
from meridian.reliability.budget import loss_budget
from meridian.reliability.classification import METHOD, PASS_CLASSES
from meridian.reliability.config import (
    ReliabilityConfig,
    ReliabilityConfigError,
    load_deployed_reliability_config,
)
from meridian.reliability.slis import PassRecord
from meridian.store.monitoring import MonitoringSnapshot, read_monitoring_snapshot
from meridian.store.reliability_reads import ClassifiedRow, find_classified_between
from meridian.store.schema_revision import find_current_revision, find_head_revision

__all__ = ["OVERDUE_AFTER_S", "SCRAPE_CONNECTION_TIMEOUT_S", "DomainCollector"]

_log = logging.getLogger(__name__)

Pool = ConnectionPool[Connection[tuple[object, ...]]]

OVERDUE_AFTER_S = 900
"""How long after an assignment's window closes its report counts as overdue.

A station decodes after the pass and reports afterwards. LRPT decoding of an
eleven-minute capture takes a few minutes on a Raspberry Pi, and the client
retries a failed upload on its next loop; fifteen minutes covers both with room to
spare, so an overdue count is a report that is genuinely late rather than one
still being produced. MSP §6 allows a later submission, which is why the alert
built on this waits a further thirty minutes before firing.
"""

SCRAPE_CONNECTION_TIMEOUT_S = 2.0
"""How long a scrape waits for a pooled connection before reporting unreachable.

Shorter than the pool's five seconds for requests: Prometheus times a scrape out
after ten by default, and a scrape that waited out the full pool timeout while
the database was down would risk reporting nothing at all — not even the
``meridian_database_reachable 0`` that the database alert reads.
"""

_POOL_STATISTICS = {
    "size": "pool_size",
    "available": "pool_available",
    "waiting": "requests_waiting",
}
"""Label value → the key ``psycopg_pool``'s ``get_stats()`` reports it under."""


@dataclass(frozen=True, slots=True)
class _Reading:
    """One scrape's database read, taken in one transaction."""

    snapshot: MonitoringSnapshot
    current_revision: str | None
    classified: list[ClassifiedRow] | None
    """Passes classified inside the SLO window; None without a configuration."""


def _simulated_label(simulated: bool) -> str:
    return "true" if simulated else "false"


class DomainCollector(Collector):
    """Reports the network's state from the database at scrape time."""

    def __init__(
        self,
        pool_of: Callable[[], Pool | None],
        *,
        now: Callable[[], datetime] = platform_clock.utc_now,
        head_revision_of: Callable[[], str | None] = find_head_revision,
        reliability_of: Callable[
            [], ReliabilityConfig
        ] = load_deployed_reliability_config,
    ) -> None:
        """Build a collector.

        Args:
            pool_of: Returns the API's pool, or ``None`` before the lifespan has
                opened it. A callable because the pool is opened after the
                application, and this collector, are built.
            now: The platform clock, injectable so liveness is testable.
            head_revision_of: Returns the revision the code expects; called
                once, on the first scrape.
            reliability_of: Returns the deployment's reliability configuration,
                whose classification and window the reliability series read.
                Called once, on the first scrape; a file it refuses is logged
                and the reliability series are left out, never zeroed.
        """
        self._pool_of = pool_of
        self._now = now
        self._head_revision_of = head_revision_of
        self._head_revision: str | None = None
        self._head_revision_read = False
        self._reliability_of = reliability_of
        self._reliability: ReliabilityConfig | None = None
        self._reliability_read = False

    def collect(self) -> Iterator[Metric]:
        """Yield the pool figures, reachability, and — if reachable — the rest."""
        pool = self._pool_of()
        if pool is None:
            yield _reachable_family(reachable=False)
            return
        yield _pool_family(pool)
        now = self._now()
        reliability = self._reliability_config()
        reading = _read(pool, now, reliability)
        yield _reachable_family(reachable=reading is not None)
        if reading is None:
            return
        yield _stations_family(reading.snapshot, now)
        yield _assignments_family(reading.snapshot)
        yield _overdue_family(reading.snapshot)
        schema = self._schema_family(reading.current_revision)
        if schema is not None:
            yield schema
        if reliability is not None and reading.classified is not None:
            yield from _reliability_families(reading.classified, reliability)

    def _reliability_config(self) -> ReliabilityConfig | None:
        """The deployment's reliability configuration, read once."""
        if not self._reliability_read:
            self._reliability_read = True
            try:
                self._reliability = self._reliability_of()
            except ReliabilityConfigError:
                _log.warning(
                    "reliability configuration refused; reliability series "
                    "not reported",
                    exc_info=True,
                )
        return self._reliability

    def _schema_family(self, current_revision: str | None) -> Metric | None:
        """Whether the database is at the expected revision, when both are known."""
        if not self._head_revision_read:
            self._head_revision = self._head_revision_of()
            self._head_revision_read = True
            if self._head_revision is None:
                _log.warning("no migration scripts found; schema currency not reported")
        if self._head_revision is None:
            return None
        family = GaugeMetricFamily(
            "meridian_schema_up_to_date",
            "1 when the database is at the migration revision this code expects.",
        )
        family.add_metric([], 1.0 if current_revision == self._head_revision else 0.0)
        return family


def _read(
    pool: Pool, now: datetime, reliability: ReliabilityConfig | None
) -> _Reading | None:
    """Everything the database contributes to one scrape, or ``None`` if silent."""
    try:
        with pool.connection(timeout=SCRAPE_CONNECTION_TIMEOUT_S) as conn:
            snapshot = read_monitoring_snapshot(
                conn, overdue_ended_before=now - timedelta(seconds=OVERDUE_AFTER_S)
            )
            classified = None
            if reliability is not None:
                classified = find_classified_between(
                    conn,
                    classified_under=(METHOD, reliability.classification.sha256()),
                    window=(now - timedelta(days=reliability.slo.window_days), now),
                )
            return _Reading(snapshot, find_current_revision(conn), classified)
    except (psycopg.Error, OSError):
        # The same narrow pair `is_database_reachable` catches, for its reason:
        # psycopg.Error already covers pool exhaustion and a closed pool, and a
        # bug in this module must not be reported as an outage.
        _log.warning("scrape-time database read failed", exc_info=True)
        return None


def _reachable_family(*, reachable: bool) -> Metric:
    family = GaugeMetricFamily(
        "meridian_database_reachable",
        "1 when the database answered this scrape's read, 0 when it did not.",
    )
    family.add_metric([], 1.0 if reachable else 0.0)
    return family


def _pool_family(pool: Pool) -> Metric:
    family = GaugeMetricFamily(
        "meridian_db_pool_connections",
        "The answering process's connection pool: open, idle and waited for.",
        labels=["kind"],
    )
    statistics = pool.get_stats()
    for kind, key in _POOL_STATISTICS.items():
        family.add_metric([kind], float(statistics.get(key, 0)))
    return family


def _stations_family(snapshot: MonitoringSnapshot, now: datetime) -> Metric:
    family = GaugeMetricFamily(
        "meridian_stations",
        "Registered stations by liveness and whether simulated.",
        labels=["liveness", "simulated"],
    )
    counts = count_by_liveness(
        ((one.last_heartbeat_at, one.simulated) for one in snapshot.stations), now=now
    )
    for (liveness, simulated), count in counts.items():
        family.add_metric([liveness, _simulated_label(simulated)], float(count))
    return family


def _assignments_family(snapshot: MonitoringSnapshot) -> Metric:
    family = GaugeMetricFamily(
        "meridian_assignments",
        "Scheduled assignments by state and whether simulated.",
        labels=["state", "simulated"],
    )
    for (state, simulated), count in snapshot.assignments.items():
        family.add_metric([state, _simulated_label(simulated)], float(count))
    return family


def _overdue_family(snapshot: MonitoringSnapshot) -> Metric:
    family = GaugeMetricFamily(
        "meridian_assignments_overdue",
        "Held or started assignments whose window closed over 15 minutes ago "
        "with no report. Reports not yet arrived; not misses.",
        labels=["simulated"],
    )
    for simulated, count in snapshot.overdue.items():
        family.add_metric([_simulated_label(simulated)], float(count))
    return family


def _reliability_families(
    classified: list[ClassifiedRow], config: ReliabilityConfig
) -> Iterator[Metric]:
    """Passes by class, and the budget left, for each population that has any.

    Counted by ``meridian.reliability``'s own functions from the stored
    classifications, so a scrape and ``meridian reliability report`` over the
    same window agree (D-186).
    """
    passes = [
        PassRecord(
            reference=row.assignment_id,
            station_id=row.station_id,
            window_end=row.window_end,
            classification=row.classification,
            listening_confirmed=row.listening_confirmed,
            outcome=row.outcome,
            simulated=row.simulated,
        )
        for row in classified
    ]
    counts = GaugeMetricFamily(
        "meridian_passes_classified",
        "Settled passes classified inside the SLO window, by class and whether "
        "simulated. Only confirmed_miss is a miss.",
        labels=["classification", "simulated"],
    )
    remaining = GaugeMetricFamily(
        "meridian_loss_budget_remaining_ratio",
        "Share of the loss budget SC-4's capture target leaves that is unspent; "
        "negative once the target is broken.",
        labels=["simulated"],
    )
    for simulated in (False, True):
        mine = [one for one in passes if one.simulated == simulated]
        if not mine:
            continue
        label = _simulated_label(simulated)
        for name in PASS_CLASSES:
            count = sum(1 for one in mine if one.classification == name)
            counts.add_metric([name, label], float(count))
        budget = loss_budget(mine, capture_target=config.slo.capture_rate_min)
        if budget.remaining_ratio is not None:
            remaining.add_metric([label], budget.remaining_ratio)
    yield counts
    yield remaining
