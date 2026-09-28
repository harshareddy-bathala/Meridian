"""``/api/v1/reliability`` against a real database and the real application.

Pins the computed body: two populations that never meet, every figure a count
over a count, the budget counted by reason and never listed pass by pass, and
the one figure the platform cannot give yet saying so rather than printing a
number (D-086, D-187).

Marked ``integration`` by the directory hook in ``tests/conftest.py``.

Reference: docs/DECISIONS.md D-086, D-093, D-184, D-185, D-187.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

psycopg = pytest.importorskip("psycopg")

from meridian.api import platform_clock  # noqa: E402
from meridian.api.app import create_app  # noqa: E402
from meridian.api.dependencies import get_connection  # noqa: E402
from meridian.registry.psycopg_registry import PsycopgRegistry  # noqa: E402
from meridian.reliability.accounting import classify_settled  # noqa: E402
from meridian.reliability.config import ClassificationConfig  # noqa: E402

pytestmark = pytest.mark.integration

AOS = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
NOW = AOS + timedelta(days=3)


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def client(rollback: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(platform_clock, "utc_now", lambda: NOW)
    monkeypatch.delenv("MERIDIAN_RELIABILITY_CONFIG", raising=False)
    app = create_app()
    app.dependency_overrides[get_connection] = lambda: rollback
    with TestClient(app, raise_server_exceptions=False) as started:
        yield started


@pytest.fixture
def classified(schedule_rows: Any) -> None:
    """A measured decode, a measured pass nobody heard, and a simulated decode."""
    rows = schedule_rows
    elements = rows.satellite("norad:99970")
    rows.station("st_real", simulated=False)
    rows.station("st_sim", simulated=True)
    for station, assignment, hours, outcome in (
        ("st_real", "as_r1", 0, "decoded"),
        ("st_real", "as_r2", 2, None),
        ("st_sim", "as_s1", 4, "decoded"),
    ):
        pass_id = rows.pass_(
            station, AOS + timedelta(hours=hours), element_set_id=elements
        )
        rows.assignment(assignment, pass_id, state="reported" if outcome else "held")
        if outcome:
            rows.observation(assignment, outcome=outcome)
    registry = PsycopgRegistry(
        rows.conn, pepper="test-pepper", recovery_window_s=3600, now_utc=NOW
    )
    classify_settled(rows.conn, registry, now=NOW, config=ClassificationConfig())


@pytest.mark.usefixtures("classified")
def test_the_body_is_computed_for_each_population_apart(client: TestClient) -> None:
    response = client.get("/api/v1/reliability")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "computed"
    measured, simulated = body["measured"], body["simulated"]
    assert (measured["simulated"], simulated["simulated"]) == (False, True)
    assert (measured["passes"], simulated["passes"]) == (2, 1)
    assert measured["capture_rate"]["numerator"] == 1
    assert measured["capture_rate"]["denominator"] == 2
    assert [one["station_id"] for one in measured["stations"]] == ["st_real"]


@pytest.mark.usefixtures("classified")
def test_the_budget_is_counted_by_reason_and_never_listed(client: TestClient) -> None:
    """A debit names an exact window, which D-093 never publishes (D-187)."""
    budget = client.get("/api/v1/reliability").json()["measured"]["loss_budget"]

    assert budget["spent"] == 1
    assert budget["by_reason"]["station_unavailable"] == 1
    assert budget["by_reason"]["confirmed_miss"] == 0
    assert "debits" not in budget


@pytest.mark.usefixtures("classified")
def test_what_cannot_be_measured_says_so_instead_of_a_number(
    client: TestClient,
) -> None:
    body = client.get("/api/v1/reliability").json()

    assert body["failure_detection"]["status"] == "not_measured"
    assert body["failure_detection"]["reason"]
    targets = {one["name"]: one for one in body["measured"]["targets"]}
    assert targets["pass capture rate"]["claim"] == "SC-4"
    assert targets["failure detection (s)"]["value"] is None


def test_nothing_classified_is_empty_populations_not_zero_rates(
    client: TestClient,
) -> None:
    body = client.get("/api/v1/reliability").json()

    capture = body["measured"]["capture_rate"]
    assert (capture["denominator"], capture["estimate"]) == (0, None)
    assert body["measured"]["loss_budget"]["remaining_ratio"] is None
