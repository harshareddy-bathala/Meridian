"""What the scrape-time collector reports, and what it refuses to report.

The SQL is covered against a real database in
``tests/integration/test_domain_collector_reads.py``. This file pins the
composition with no database at all: that liveness is classified against one
clock reading, that every label combination is present, and — the rule that
matters most — that an unreachable database produces a reachability of zero and
**no** station or assignment series, never a column of zeros.

Marked as a unit test by living in ``tests/unit``: no network, no database.

Reference: docs/DECISIONS.md D-086, D-109, D-111.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from prometheus_client.metrics_core import Metric

from meridian.api import domain_collector, report_families
from meridian.api.domain_collector import DomainCollector
from meridian.registry.liveness_counts import count_by_liveness
from meridian.reliability.classification import PASS_CLASSES
from meridian.reliability.config import ReliabilityConfigError
from meridian.store.monitoring import (
    ASSIGNMENT_STATES,
    MonitoringSnapshot,
    StationHeartbeat,
)
from meridian.store.report_deliveries import DeliveryCount
from meridian.store.watch_reads import WatchCount

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


class _Pool:
    """Enough of a pool for the collector: statistics and a connection."""

    def __init__(self, *, reachable: bool) -> None:
        self.reachable = reachable

    def get_stats(self) -> dict[str, int]:
        return {"pool_size": 3, "pool_available": 2, "requests_waiting": 0}

    @contextmanager
    def connection(self, **_options: float) -> Iterator[object]:
        if not self.reachable:
            raise psycopg.OperationalError("the database is not answering")
        yield object()


def _snapshot() -> MonitoringSnapshot:
    """Two measured stations, one online and one offline, and one simulated."""
    return MonitoringSnapshot(
        stations=(
            StationHeartbeat(NOW - timedelta(seconds=10), simulated=False),
            StationHeartbeat(NOW - timedelta(minutes=10), simulated=False),
            StationHeartbeat(None, simulated=True),
        ),
        assignments={
            (state, simulated): (4 if state == "held" and not simulated else 0)
            for state in ASSIGNMENT_STATES
            for simulated in (False, True)
        },
        overdue={False: 1, True: 0},
    )


Counts = dict[tuple[str, bool], int]


@pytest.fixture(autouse=True)
def classified(monkeypatch: pytest.MonkeyPatch) -> Counts:
    """Classifications the fake database holds, by class and population."""
    held: Counts = {}
    monkeypatch.setattr(
        domain_collector, "count_classified_between", lambda *_, **__: held
    )
    return held


@pytest.fixture(autouse=True)
def watched(monkeypatch: pytest.MonkeyPatch) -> list[WatchCount]:
    """Chains with a baseline, and warnings open, as the fake database holds."""
    held: list[WatchCount] = []
    monkeypatch.setattr(domain_collector, "count_watched", lambda *_, **__: held)
    return held


@pytest.fixture(autouse=True)
def delivered(monkeypatch: pytest.MonkeyPatch) -> list[DeliveryCount]:
    """Owner reports by population and state, as the fake database holds."""
    held: list[DeliveryCount] = []
    monkeypatch.setattr(report_families, "count_deliveries", lambda *_, **__: held)
    return held


def _families(collector: DomainCollector) -> dict[str, Metric]:
    return {family.name: family for family in collector.collect()}


def _value(family: Metric, labels: dict[str, str]) -> float | None:
    for sample in family.samples:
        if sample.labels == labels:
            return float(sample.value)
    return None


@pytest.fixture
def reachable(monkeypatch: pytest.MonkeyPatch) -> DomainCollector:
    """A collector whose database answers with :func:`_snapshot` at revision 0014."""
    monkeypatch.setattr(
        domain_collector, "read_monitoring_snapshot", lambda *_, **__: _snapshot()
    )
    monkeypatch.setattr(domain_collector, "find_current_revision", lambda _: "0014")
    pool = _Pool(reachable=True)
    return DomainCollector(
        lambda: pool,  # type: ignore[arg-type, return-value]
        now=lambda: NOW,
        head_revision_of=lambda: "0014",
    )


def test_every_liveness_and_provenance_is_counted_including_zeros() -> None:
    """Eight series always, so "none offline" is a zero and not a gap."""
    counts = count_by_liveness(
        [(NOW - timedelta(seconds=5), False), (None, True)], now=NOW
    )

    assert len(counts) == 8
    assert counts[("online", False)] == 1
    assert counts[("never_seen", True)] == 1
    assert counts[("offline", False)] == 0


def test_a_reachable_database_reports_stations_by_liveness(
    reachable: DomainCollector,
) -> None:
    """Classified against the injected clock, split by provenance."""
    stations = _families(reachable)["meridian_stations"]

    assert _value(stations, {"liveness": "online", "simulated": "false"}) == 1.0
    assert _value(stations, {"liveness": "offline", "simulated": "false"}) == 1.0
    assert _value(stations, {"liveness": "never_seen", "simulated": "true"}) == 1.0
    assert _value(stations, {"liveness": "stale", "simulated": "true"}) == 0.0


def test_a_reachable_database_reports_assignments_and_overdue(
    reachable: DomainCollector,
) -> None:
    """Both families carry every label combination the snapshot holds."""
    families = _families(reachable)

    assignments = families["meridian_assignments"]
    assert len(assignments.samples) == len(ASSIGNMENT_STATES) * 2
    assert _value(assignments, {"state": "held", "simulated": "false"}) == 4.0
    overdue = families["meridian_assignments_overdue"]
    assert _value(overdue, {"simulated": "false"}) == 1.0
    assert _value(families["meridian_database_reachable"], {}) == 1.0


def test_the_schema_is_reported_current_only_when_the_revisions_agree(
    reachable: DomainCollector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Behind by one revision reads 0."""
    assert _value(_families(reachable)["meridian_schema_up_to_date"], {}) == 1.0

    monkeypatch.setattr(domain_collector, "find_current_revision", lambda _: "0013")

    assert _value(_families(reachable)["meridian_schema_up_to_date"], {}) == 0.0


def test_an_unreachable_database_publishes_no_counts_at_all() -> None:
    """Reachability zero and the pool figures; no zeros standing in for unknowns."""
    pool = _Pool(reachable=False)
    collector = DomainCollector(
        lambda: pool,  # type: ignore[arg-type, return-value]
        now=lambda: NOW,
        head_revision_of=lambda: "0014",
    )

    families = _families(collector)

    assert set(families) == {
        "meridian_database_reachable",
        "meridian_db_pool_connections",
    }
    assert _value(families["meridian_database_reachable"], {}) == 0.0


def test_before_the_pool_is_open_only_reachability_is_reported() -> None:
    """A scrape during start-up says the database is not reachable yet."""
    collector = DomainCollector(lambda: None, now=lambda: NOW)

    families = _families(collector)

    assert list(families) == ["meridian_database_reachable"]
    assert _value(families["meridian_database_reachable"], {}) == 0.0


def test_missing_migration_scripts_omit_the_schema_series(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown is omitted rather than reported as behind."""
    monkeypatch.setattr(
        domain_collector, "read_monitoring_snapshot", lambda *_, **__: _snapshot()
    )
    monkeypatch.setattr(domain_collector, "find_current_revision", lambda _: "0014")
    pool = _Pool(reachable=True)
    collector = DomainCollector(
        lambda: pool,  # type: ignore[arg-type, return-value]
        now=lambda: NOW,
        head_revision_of=lambda: None,
    )

    assert "meridian_schema_up_to_date" not in _families(collector)


def test_nothing_classified_publishes_no_reliability_series(
    reachable: DomainCollector,
) -> None:
    """No passes settled yet is "not measured", not "no misses" (D-086)."""
    families = _families(reachable)

    assert families["meridian_passes_classified"].samples == []
    assert families["meridian_loss_budget_remaining_ratio"].samples == []


def test_a_population_with_passes_reports_every_class_and_its_budget(
    reachable: DomainCollector, classified: Counts
) -> None:
    """Nineteen captures and one miss: one pass lost of two allowed."""
    classified[("successful_reception", False)] = 19
    classified[("confirmed_miss", False)] = 1

    families = _families(reachable)

    counts = families["meridian_passes_classified"]
    assert len(counts.samples) == len(PASS_CLASSES)
    assert (
        _value(counts, {"classification": "confirmed_miss", "simulated": "false"})
        == 1.0
    )
    assert (
        _value(counts, {"classification": "station_unavailable", "simulated": "false"})
        == 0.0
    )
    remaining = families["meridian_loss_budget_remaining_ratio"]
    assert _value(remaining, {"simulated": "false"}) == pytest.approx(0.5)
    assert _value(remaining, {"simulated": "true"}) is None


def test_a_refused_reliability_file_leaves_the_series_out(
    monkeypatch: pytest.MonkeyPatch, classified: Counts
) -> None:
    monkeypatch.setattr(
        domain_collector, "read_monitoring_snapshot", lambda *_, **__: _snapshot()
    )
    monkeypatch.setattr(domain_collector, "find_current_revision", lambda _: "0014")
    classified[("confirmed_miss", False)] = 1
    pool = _Pool(reachable=True)

    def refused() -> None:
        raise ReliabilityConfigError("capture_rate_min must be between 0 and 1")

    collector = DomainCollector(
        lambda: pool,  # type: ignore[arg-type, return-value]
        now=lambda: NOW,
        head_revision_of=lambda: "0014",
        reliability_of=refused,  # type: ignore[arg-type]
    )

    families = _families(collector)

    assert "meridian_passes_classified" not in families
    assert _value(families["meridian_database_reachable"], {}) == 1.0


def test_a_failed_reliability_read_loses_only_the_reliability_series(
    reachable: DomainCollector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A migration not yet applied leaves no ``pass_classifications`` table.

    The database answered, so it is reachable, and the schema series is what
    says the migration is pending; neither may go with the reliability read.
    """

    def missing(*_: object, **__: object) -> Counts:
        raise psycopg.errors.UndefinedTable('relation "pass_classifications"')

    monkeypatch.setattr(domain_collector, "count_classified_between", missing)

    families = _families(reachable)

    assert _value(families["meridian_database_reachable"], {}) == 1.0
    assert "meridian_schema_up_to_date" in families
    assert "meridian_stations" in families
    assert "meridian_passes_classified" not in families


def test_no_baseline_publishes_no_watch_series(reachable: DomainCollector) -> None:
    """Nothing watched yet is not "no warnings" (D-086, D-295)."""
    families = _families(reachable)

    assert families["meridian_receive_chains_watched"].samples == []
    assert families["meridian_receive_chain_warnings_open"].samples == []


def test_each_watched_population_reports_its_chains_and_open_warnings(
    reachable: DomainCollector, watched: list[WatchCount]
) -> None:
    watched.append(WatchCount(simulated=True, chains=4, open=1))

    families = _families(reachable)

    open_now = families["meridian_receive_chain_warnings_open"]
    assert _value(open_now, {"simulated": "true"}) == 1.0
    assert _value(open_now, {"simulated": "false"}) is None
    chains = families["meridian_receive_chains_watched"]
    assert _value(chains, {"simulated": "true"}) == 4.0


def test_a_failed_watch_read_loses_only_the_watch_series(
    reachable: DomainCollector, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(*_: object, **__: object) -> list[WatchCount]:
        raise psycopg.errors.UndefinedTable('relation "signal_baselines"')

    monkeypatch.setattr(domain_collector, "count_watched", missing)

    families = _families(reachable)

    assert "meridian_receive_chain_warnings_open" not in families
    assert "meridian_passes_classified" in families
    assert _value(families["meridian_database_reachable"], {}) == 1.0


def test_no_report_publishes_no_delivery_series(reachable: DomainCollector) -> None:
    """Nothing reported yet is not "nothing failing" (D-086, D-305)."""
    assert _families(reachable)["meridian_report_deliveries"].samples == []


def test_a_population_with_reports_publishes_every_state(
    reachable: DomainCollector, delivered: list[DeliveryCount]
) -> None:
    delivered.append(DeliveryCount(simulated=True, state="sent", reports=3))
    delivered.append(DeliveryCount(simulated=True, state="retrying", reports=1))

    reports = _families(reachable)["meridian_report_deliveries"]

    assert _value(reports, {"state": "sent", "simulated": "true"}) == 3.0
    assert _value(reports, {"state": "retrying", "simulated": "true"}) == 1.0
    assert _value(reports, {"state": "abandoned", "simulated": "true"}) == 0.0
    assert _value(reports, {"state": "sent", "simulated": "false"}) is None


def test_a_failed_report_read_loses_only_the_delivery_series(
    reachable: DomainCollector, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(*_: object, **__: object) -> list[DeliveryCount]:
        raise psycopg.errors.UndefinedTable('relation "report_deliveries"')

    monkeypatch.setattr(report_families, "count_deliveries", missing)

    families = _families(reachable)

    assert "meridian_report_deliveries" not in families
    assert "meridian_stations" in families
