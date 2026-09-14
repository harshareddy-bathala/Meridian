"""``/api/v1/observations`` and ``/api/v1/simulator-runs``, end to end.

Pins the exact set of keys an observation is published with — so a column added
to the store read cannot appear on the public surface unnoticed — the widened
window, paging, and that a simulator run is labelled and carries no seed.

Marked ``integration`` by the directory hook in ``tests/conftest.py``.

Reference: docs/DECISIONS.md D-088, D-093.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

psycopg = pytest.importorskip("psycopg")

from meridian.api.app import create_app  # noqa: E402
from meridian.api.dependencies import get_connection  # noqa: E402

pytestmark = pytest.mark.integration

STARTED = datetime(2026, 9, 14, 3, 7, 41, tzinfo=UTC)

PUBLISHED_KEYS = {
    "observation_id",
    "assignment_id",
    "revision",
    "station_id",
    "satellite_id",
    "started_at",
    "ended_at",
    "outcome",
    "signal_detected",
    "peak_snr_db",
    "provenance",
    "submitted_at",
    "simulated",
}


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def client(rollback: Any) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_connection] = lambda: rollback
    with TestClient(app, raise_server_exceptions=False) as started:
        yield started


@pytest.fixture(autouse=True)
def reported(schedule_rows: Any) -> None:
    rows = schedule_rows
    rows.station("st_o_a")
    elements = rows.satellite()
    for index in range(2):
        assignment_id = f"as_o_{index}"
        at = STARTED + timedelta(hours=index)
        rows.assignment(
            assignment_id, rows.pass_("st_o_a", at, element_set_id=elements)
        )
        rows.observation(assignment_id)


def test_an_observation_is_published_with_exactly_its_public_fields(
    client: TestClient,
) -> None:
    body = client.get("/api/v1/observations", params={"station_id": "st_o_a"}).json()

    assert [item["assignment_id"] for item in body["items"]] == ["as_o_1", "as_o_0"]
    oldest = body["items"][1]
    assert set(oldest) == PUBLISHED_KEYS
    assert (oldest["started_at"], oldest["ended_at"]) == (
        "2026-09-14T03:07:00Z",
        "2026-09-14T03:19:00Z",
    )
    assert oldest["simulated"] is True


def test_paging_walks_every_observation_once(client: TestClient) -> None:
    first = client.get("/api/v1/observations", params={"limit": 1}).json()
    second = client.get(
        "/api/v1/observations", params={"limit": 1, "cursor": first["next_cursor"]}
    ).json()

    ids = [i["assignment_id"] for i in first["items"] + second["items"]]
    assert ids[:2] == ["as_o_1", "as_o_0"]


def test_a_simulator_run_is_labelled_and_carries_no_seed(client: TestClient) -> None:
    runs = client.get("/api/v1/simulator-runs").json()["items"]

    (run,) = [run for run in runs if run["run_id"] == "run-a"]
    assert run["station_count"] == 1
    assert run["simulated"] is True
    assert not any("seed" in key for key in run)
