"""``/api/v1/passes`` against a real database and the real application.

What is pinned: which passes count as upcoming, the order, that paging walks the
whole queue once, and that nothing leaves at the precision D-093 forbids — in
the body *or* in the cursor.

Marked ``integration`` by the directory hook in ``tests/conftest.py``.

Reference: docs/DECISIONS.md D-085, D-093.
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
from meridian.api.public.pagination import decode_cursor  # noqa: E402

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 14, 6, 0, 0, tzinfo=UTC)


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


@pytest.fixture
def queue(schedule_rows: Any) -> dict[str, int]:
    """Two stations: a finished pass, one in progress, and two ahead."""
    rows = schedule_rows
    rows.station("st_q_a")
    rows.station("st_q_b")
    rows.station("st_q_gone", deleted=True)
    elements = rows.satellite()
    at = NOW + timedelta(seconds=17)
    return {
        "over": rows.pass_(
            "st_q_a", NOW - timedelta(minutes=30), element_set_id=elements
        ),
        "now": rows.pass_(
            "st_q_a", NOW - timedelta(minutes=3, seconds=41), element_set_id=elements
        ),
        "next_b": rows.pass_(
            "st_q_b", at + timedelta(minutes=20), element_set_id=elements
        ),
        "next_a": rows.pass_(
            "st_q_a", at + timedelta(hours=2), element_set_id=elements
        ),
        "deleted": rows.pass_("st_q_gone", at, element_set_id=elements),
    }


def _ids(body: dict[str, Any]) -> list[int]:
    return [item["pass_id"] for item in body["items"]]


def test_upcoming_passes_are_listed_soonest_first(
    client: TestClient, queue: dict[str, int]
) -> None:
    """Finished passes go; one in progress stays; a deleted station's are hidden."""
    body = client.get("/api/v1/passes").json()

    assert _ids(body) == [queue["now"], queue["next_b"], queue["next_a"]]
    assert body["next_cursor"] is None
    assert all(item["simulated"] is True for item in body["items"])


def test_the_list_filters_to_one_station(
    client: TestClient, queue: dict[str, int]
) -> None:
    body = client.get("/api/v1/passes", params={"station_id": "st_q_a"}).json()

    assert _ids(body) == [queue["now"], queue["next_a"]]


@pytest.mark.usefixtures("queue")
def test_an_unknown_station_filter_is_an_empty_page(client: TestClient) -> None:
    response = client.get("/api/v1/passes", params={"station_id": "st_nobody"})

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}


def test_paging_walks_the_queue_once_with_a_cursor_that_carries_no_time(
    client: TestClient, queue: dict[str, int]
) -> None:
    first = client.get("/api/v1/passes", params={"limit": 2}).json()
    second = client.get(
        "/api/v1/passes", params={"limit": 2, "cursor": first["next_cursor"]}
    ).json()

    assert _ids(first) + _ids(second) == [
        queue["now"],
        queue["next_b"],
        queue["next_a"],
    ]
    assert decode_cursor(first["next_cursor"]) == (str(queue["next_b"]),)


@pytest.mark.usefixtures("queue")
def test_the_window_and_angles_are_published_coarsened(client: TestClient) -> None:
    """D-093: whole minutes, widened, and whole degrees."""
    item = client.get("/api/v1/passes", params={"station_id": "st_q_b"}).json()[
        "items"
    ][0]

    assert item["aos"] == "2026-09-14T06:20:00Z"
    assert item["los"] == "2026-09-14T06:32:00Z"
    assert (
        item["max_elevation_deg"],
        item["aos_azimuth_deg"],
        item["los_azimuth_deg"],
    ) == (61, 13, 201)


@pytest.mark.usefixtures("queue")
@pytest.mark.parametrize("cursor", ["not-base64!", "MR9hYmM"])
def test_a_bad_cursor_is_invalid_query(client: TestClient, cursor: str) -> None:
    response = client.get("/api/v1/passes", params={"cursor": cursor})

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_query"
