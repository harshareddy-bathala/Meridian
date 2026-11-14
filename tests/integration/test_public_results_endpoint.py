"""``/api/v1/results`` against a real database and the real application.

Pins the network-wide results Stage 24 owed (D-365): every pass classified
under the deployment's classification, newest first, each with its window
widened to whole minutes, its effect on SC-4 from the reliability module, and
the cause of its loss where the deployment's own diagnosis found one.

Marked ``integration`` by the directory hook in ``tests/conftest.py``.

Reference: docs/DECISIONS.md D-093, D-182, D-272, D-365.
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
from meridian.reliability.config import (  # noqa: E402
    ClassificationConfig,
    ReliabilityConfig,
)
from meridian.store.loss_diagnoses import NewDiagnosis, insert_diagnosis  # noqa: E402

pytestmark = pytest.mark.integration

AOS = datetime(2026, 9, 1, 10, 0, 20, tzinfo=UTC)
NOW = AOS + timedelta(days=3)
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


def diagnose(conn: Any, assignment: str, cause: str, sha256: bytes) -> int:
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
            config_sha256=sha256,
            verdict_method=None,
            simulated=simulated,
        ),
    )
    return int(classification)


@pytest.fixture
def network(schedule_rows: Any) -> None:
    """A measured decode, a measured pass nobody heard, and a simulated decode."""
    rows = schedule_rows
    elements = rows.satellite("norad:99970")
    rows.station("st_res_real", simulated=False)
    rows.station("st_res_sim", simulated=True)
    for station, assignment, hours, outcome in (
        ("st_res_real", "as_res_1", 0, "decoded"),
        ("st_res_real", "as_res_2", 2, None),
        ("st_res_sim", "as_res_3", 4, "decoded"),
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
    diagnose(rows.conn, "as_res_2", "undetermined", bytes(32))
    diagnose(rows.conn, "as_res_2", "station_not_listening", CONFIG.diagnosis.sha256())


def mine(client: TestClient, query: str = "") -> list[dict[str, Any]]:
    body = client.get(f"/api/v1/results?limit=200{query}").json()
    return [one for one in body["items"] if one["station_id"].startswith("st_res")]


@pytest.mark.usefixtures("network")
def test_every_settled_pass_with_its_effect_newest_first(client: TestClient) -> None:
    results = mine(client)

    assert [one["assignment_id"] for one in results] == [
        "as_res_3",
        "as_res_2",
        "as_res_1",
    ]
    by_id = {one["assignment_id"]: one for one in results}
    assert (by_id["as_res_1"]["effect"], by_id["as_res_1"]["outcome"]) == (
        "captured",
        "decoded",
    )
    assert by_id["as_res_2"]["effect"] == "lost"
    assert by_id["as_res_2"]["outcome"] is None
    assert by_id["as_res_3"]["simulated"] is True
    assert by_id["as_res_1"]["simulated"] is False


@pytest.mark.usefixtures("network")
def test_a_window_is_published_to_whole_minutes(client: TestClient) -> None:
    first = next(one for one in mine(client) if one["assignment_id"] == "as_res_1")

    assert first["window_start"].endswith(":00Z")
    assert first["window_end"].endswith(":00Z")
    assert first["window_start"] <= AOS.isoformat().replace("+00:00", "Z")


@pytest.mark.usefixtures("network")
def test_a_lost_pass_names_the_deployments_own_diagnosis(client: TestClient) -> None:
    lost = next(one for one in mine(client) if one["assignment_id"] == "as_res_2")

    assert lost["cause"] == "station_not_listening"
    assert lost["diagnosis_id"] is not None
    captured = next(one for one in mine(client) if one["assignment_id"] == "as_res_1")
    assert (captured["cause"], captured["diagnosis_id"]) == (None, None)


@pytest.mark.usefixtures("network")
def test_one_stations_results(client: TestClient) -> None:
    results = mine(client, "&station_id=st_res_sim")

    assert [one["assignment_id"] for one in results] == ["as_res_3"]


@pytest.mark.usefixtures("network")
def test_a_cursor_walks_every_page_and_a_foreign_one_is_refused(
    client: TestClient,
) -> None:
    everything = client.get("/api/v1/results?limit=200").json()["items"]
    walked: list[dict[str, Any]] = []
    url = "/api/v1/results?limit=1"
    while True:
        page = client.get(url).json()
        walked += page["items"]
        if page["next_cursor"] is None:
            break
        url = f"/api/v1/results?limit=1&cursor={page['next_cursor']}"

    assert walked == everything
    refused = client.get("/api/v1/results?cursor=bm90LWEtY3Vyc29y")
    assert (refused.status_code, refused.json()["error"]) == (400, "invalid_query")
