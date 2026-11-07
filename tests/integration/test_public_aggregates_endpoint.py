"""``/api/v1/aggregates`` against a real database and the real application.

Pins what the live page reads: today's passes as counts, with those closed and
not yet classified counted as waiting rather than read as quiet, and seven days
of capture rate and loss budget, each marked settled or not, for measured and
simulated stations apart (D-362).

Marked ``integration`` by the directory hook in ``tests/conftest.py``.

Reference: docs/DECISIONS.md D-182, D-184, D-185, D-362.
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

YESTERDAY = datetime(2026, 9, 1, tzinfo=UTC)
TODAY = YESTERDAY + timedelta(days=1)
NOW = TODAY + timedelta(hours=15)
"""Under the default day's settle margin, yesterday's passes before 15:00 are
classified by now, and today's are not."""


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
def network(schedule_rows: Any) -> None:
    """Yesterday: a measured decode, a measured pass nobody heard, a simulated
    decode, all classified. Today: a measured pass closed and waiting, and one
    still to come."""
    rows = schedule_rows
    elements = rows.satellite("norad:99970")
    rows.station("st_real", simulated=False)
    rows.station("st_sim", simulated=True)
    for station, assignment, aos, outcome in (
        ("st_real", "as_r1", YESTERDAY + timedelta(hours=10), "decoded"),
        ("st_real", "as_r2", YESTERDAY + timedelta(hours=12), None),
        ("st_sim", "as_s1", YESTERDAY + timedelta(hours=13), "decoded"),
        ("st_real", "as_r3", TODAY + timedelta(hours=9), "decoded"),
        ("st_real", "as_r4", TODAY + timedelta(hours=20), None),
    ):
        pass_id = rows.pass_(station, aos, element_set_id=elements)
        rows.assignment(assignment, pass_id, state="reported" if outcome else "held")
        if outcome:
            rows.observation(assignment, outcome=outcome)
    registry = PsycopgRegistry(
        rows.conn, pepper="test-pepper", recovery_window_s=3600, now_utc=NOW
    )
    classify_settled(rows.conn, registry, now=NOW, config=ClassificationConfig())


@pytest.mark.usefixtures("network")
def test_today_counts_what_is_scheduled_closed_and_waiting(client: TestClient) -> None:
    response = client.get("/api/v1/aggregates")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "computed"
    today = body["measured"]["today"]
    assert today["day"] == TODAY.date().isoformat()
    assert (today["scheduled"], today["ended"], today["awaiting"]) == (2, 1, 1)
    assert today["classified"] == 0
    assert set(today["by_class"].values()) == {0}
    assert body["simulated"]["today"]["scheduled"] == 0


@pytest.mark.usefixtures("network")
def test_a_week_of_days_each_with_its_rate_and_budget(client: TestClient) -> None:
    body = client.get("/api/v1/aggregates").json()

    days = body["measured"]["days"]
    assert len(days) == 7
    assert days[-1]["day"] == TODAY.date().isoformat()
    yesterday = days[-2]
    assert yesterday["passes"] == 2
    capture = yesterday["capture_rate"]
    assert (capture["numerator"], capture["denominator"]) == (1, 2)
    budget = yesterday["loss_budget"]
    assert (budget["spent"], budget["by_reason"]["station_unavailable"]) == (1, 1)
    assert "debits" not in budget


@pytest.mark.usefixtures("network")
def test_a_day_is_settled_only_once_its_margin_has_passed(client: TestClient) -> None:
    days = client.get("/api/v1/aggregates").json()["measured"]["days"]

    # The day before yesterday ended 39 hours ago, yesterday 15 hours ago.
    assert [one["settled"] for one in days[-3:]] == [True, False, False]


@pytest.mark.usefixtures("network")
def test_measured_and_simulated_are_never_pooled(client: TestClient) -> None:
    body = client.get("/api/v1/aggregates").json()

    assert (body["measured"]["simulated"], body["simulated"]["simulated"]) == (
        False,
        True,
    )
    simulated = body["simulated"]["days"][-2]
    assert simulated["passes"] == 1
    assert simulated["capture_rate"]["numerator"] == 1


def test_nothing_scheduled_is_counts_of_zero_and_rates_with_no_estimate(
    client: TestClient,
) -> None:
    body = client.get("/api/v1/aggregates").json()

    today = body["measured"]["today"]
    assert (today["scheduled"], today["awaiting"]) == (0, 0)
    for day in body["measured"]["days"]:
        assert day["capture_rate"]["estimate"] is None
        assert day["loss_budget"]["remaining_ratio"] is None
