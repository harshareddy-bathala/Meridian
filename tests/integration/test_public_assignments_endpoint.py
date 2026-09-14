"""``/api/v1/assignments`` against a real database and the real application.

Pins that a skipped pass is published with the reason it was skipped and the pass
it lost to, that the filters and the decision vocabulary hold at the HTTP layer,
and that the window leaves widened (D-093).

Marked ``integration`` by the directory hook in ``tests/conftest.py``.

Reference: docs/DECISIONS.md D-008, D-093.
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

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 14, 6, 0, tzinfo=UTC)


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def client(rollback: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(platform_clock, "utc_now", lambda: NOW)
    app = create_app()
    app.dependency_overrides[get_connection] = lambda: rollback
    with TestClient(app, raise_server_exceptions=False) as started:
        yield started


@pytest.fixture(autouse=True)
def decisions(schedule_rows: Any) -> None:
    rows = schedule_rows
    rows.station("st_e_a")
    rows.station("st_e_b")
    elements = rows.satellite()
    soon = NOW + timedelta(minutes=10, seconds=33)
    rows.assignment("as_e_2", rows.pass_("st_e_a", soon, element_set_id=elements))
    rows.assignment(
        "as_e_1",
        rows.pass_("st_e_b", soon, element_set_id=elements),
        decision="skipped",
        reason="overlaps a higher-scoring pass",
        conflicts_with_assignment_id="as_e_2",
        score=0.2,
        state="expired",
    )


def test_a_skipped_pass_is_published_with_its_reason(client: TestClient) -> None:
    body = client.get("/api/v1/assignments", params={"decision": "skipped"}).json()

    (item,) = body["items"]
    assert item["assignment_id"] == "as_e_1"
    assert item["decision"] == "skipped"
    assert item["reason"] == "overlaps a higher-scoring pass"
    assert item["conflicts_with_assignment_id"] == "as_e_2"
    assert item["state"] == "expired"
    assert item["simulated"] is True
    assert item["predicted_yield"] is None
    # DATA-MODEL.md's column name, not the attribute Pydantic forced on the model.
    assert item["model_config"] == "A"
    assert "prediction_config" not in item


def test_the_window_is_widened_to_whole_minutes(client: TestClient) -> None:
    item = client.get("/api/v1/assignments/as_e_2").json()

    assert (item["start_at"], item["end_at"]) == (
        "2026-09-14T06:10:00Z",
        "2026-09-14T06:22:00Z",
    )


def test_paging_and_the_station_filter(client: TestClient) -> None:
    first = client.get("/api/v1/assignments", params={"limit": 1}).json()
    second = client.get(
        "/api/v1/assignments", params={"limit": 1, "cursor": first["next_cursor"]}
    ).json()
    only_a = client.get("/api/v1/assignments", params={"station_id": "st_e_a"}).json()

    assert [i["assignment_id"] for i in first["items"] + second["items"]] == [
        "as_e_1",
        "as_e_2",
    ]
    assert second["next_cursor"] is None
    assert [i["assignment_id"] for i in only_a["items"]] == ["as_e_2"]


def test_a_decision_outside_the_vocabulary_is_invalid_query(client: TestClient) -> None:
    response = client.get("/api/v1/assignments", params={"decision": "maybe"})

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_query"


def test_an_unknown_assignment_is_not_found(client: TestClient) -> None:
    response = client.get("/api/v1/assignments/as_nothing")

    assert response.status_code == 404
    assert response.json() == {
        "error": "not_found",
        "message": "No assignment with that id.",
    }
