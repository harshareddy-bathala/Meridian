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

from meridian.api import domain_collector
from meridian.api.domain_collector import DomainCollector
from meridian.registry.liveness_counts import count_by_liveness
from meridian.store.monitoring import (
    ASSIGNMENT_STATES,
    MonitoringSnapshot,
    StationHeartbeat,
)

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
