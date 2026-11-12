"""``/api/v1/reports/weekly`` against a real database and the real application.

Pins the network's weekly report (D-367): the latest settled week by default,
counted from the classifications under the deployment's configuration, the
measured and simulated stations in sections of their own, and a refusal for a
day that is not a Monday or a week still settling.

Marked ``integration`` by the directory hook in ``tests/conftest.py``.

Reference: docs/DECISIONS.md D-182, D-306, D-367.
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
from meridian.notifications.network import SIMULATED_HEADING  # noqa: E402
from meridian.registry.psycopg_registry import PsycopgRegistry  # noqa: E402
from meridian.reliability.accounting import classify_settled  # noqa: E402
from meridian.reliability.config import (  # noqa: E402
    ClassificationConfig,
    ReliabilityConfig,
)
from meridian.store.loss_diagnoses import NewDiagnosis, insert_diagnosis  # noqa: E402

pytestmark = pytest.mark.integration

MONDAY = datetime(2026, 8, 31, tzinfo=UTC)
AOS = MONDAY + timedelta(days=1, hours=10, seconds=20)
NOW = MONDAY + timedelta(days=9, hours=12)
"""Wednesday of the next week: the week from MONDAY is the latest settled."""

CONFIG = ReliabilityConfig()


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


def diagnose(conn: Any, assignment: str, cause: str) -> None:
    classification, station, simulated = conn.execute(
        "select classification_id, station_id, simulated from pass_classifications"
        " where assignment_id = %s",
        (assignment,),
    ).fetchone()
    insert_diagnosis(
        conn,
        NewDiagnosis(
            assignment_id=assignment,
            revision=None,
            observation_started_at=None,
            station_id=station,
            classification_id=classification,
            cause=cause,
            candidates=[],
            evidence={},
            method="diagnosis-1",
            config_sha256=CONFIG.diagnosis.sha256(),
            verdict_method=None,
            simulated=simulated,
        ),
    )


@pytest.fixture
def network(schedule_rows: Any) -> None:
    """Two measured passes, one decoded and one unheard, and a simulated decode."""
    rows = schedule_rows
    elements = rows.satellite("norad:99970")
    rows.station("st_net_real", simulated=False)
    rows.station("st_net_sim", simulated=True)
    rows.conn.execute(
        "update stations set registered_at = %s where station_id like 'st_net_%%'",
        (MONDAY - timedelta(days=30),),
    )
    for station, assignment, hours, outcome in (
        ("st_net_real", "as_net_1", 0, "decoded"),
        ("st_net_real", "as_net_2", 2, None),
        ("st_net_sim", "as_net_3", 4, "decoded"),
    ):
        pass_id = rows.pass_(
            station, AOS + timedelta(hours=hours), element_set_id=elements
        )
        rows.assignment(assignment, pass_id, state="reported" if outcome else "held")
        if outcome:
            rows.observation(assignment, outcome=outcome)
    rows.conn.execute(
        "insert into heartbeats (station_id, sent_at, received_at, state,"
        " clock_offset_s, clock_uncertainty_s)"
        " values ('st_net_real', %s, %s, 'idle', 0, 0.1)",
        (AOS, AOS),
    )
    registry = PsycopgRegistry(
        rows.conn, pepper="test-pepper", recovery_window_s=3600, now_utc=NOW
    )
    classify_settled(rows.conn, registry, now=NOW, config=ClassificationConfig())
    diagnose(rows.conn, "as_net_2", "station_not_listening")


@pytest.mark.usefixtures("network")
def test_the_latest_settled_week_in_plain_words(client: TestClient) -> None:
    response = client.get("/api/v1/reports/weekly")

    assert response.status_code == 200
    body = response.json()
    assert body["template"] == "network-weekly-1"
    assert body["period_start"] == "2026-08-31T00:00:00Z"
    assert body["period_end"] == "2026-09-07T00:00:00Z"
    assert body["includes_simulated"] is True
    measured, simulated = body["body"].split(SIMULATED_HEADING)
    assert "Stations: 1 registered, 1 heard from during the week." in measured
    assert "Passes settled: 2; decoded 1," in measured
    assert "Losses by cause: station not listening 1." in measured
    assert "Stations: 1 registered, 0 heard from during the week." in simulated
    assert "Passes settled: 1; decoded 1." in simulated


@pytest.mark.usefixtures("network")
def test_two_reads_of_a_settled_week_give_the_same_hashes(client: TestClient) -> None:
    first = client.get("/api/v1/reports/weekly").json()
    again = client.get("/api/v1/reports/weekly?week=2026-08-31").json()
    assert first["inputs_sha256"] == again["inputs_sha256"]
    assert first["content_sha256"] == again["content_sha256"]


@pytest.mark.usefixtures("network")
def test_the_earlier_weeks_stop_at_the_first_registration(client: TestClient) -> None:
    latest = client.get("/api/v1/reports/weekly").json()
    assert latest["previous_week"] == "2026-08-24"
    first = client.get("/api/v1/reports/weekly?week=2026-07-27").json()
    assert first["previous_week"] is None
    assert "Passes: none closed in the week and was settled." in first["body"]


def test_a_day_that_is_not_a_monday_is_refused(client: TestClient) -> None:
    response = client.get("/api/v1/reports/weekly?week=2026-09-01")
    assert response.status_code == 400
    assert response.json()["message"] == "week is not a Monday."


def test_a_week_still_settling_is_refused(client: TestClient) -> None:
    response = client.get("/api/v1/reports/weekly?week=2026-09-07")
    assert response.status_code == 400
    assert "latest settled week is 2026-08-31" in response.json()["message"]


def test_a_network_with_no_station_says_so(client: TestClient) -> None:
    body = client.get("/api/v1/reports/weekly").json()
    assert "No measured station is registered." in body["body"]
    assert body["includes_simulated"] is False
    assert body["previous_week"] is None
