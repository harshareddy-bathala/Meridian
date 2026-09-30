"""``/api/v1/stations/{id}/uptime``, end to end.

Heartbeats are written into the last complete hour only, which is always above
the aggregate's refresh watermark (its policy stops an hour short of now), so
they are read from raw rows whatever the refresh job has done. Earlier hours
come back at zero rather than missing.

Marked ``integration`` by the directory hook in ``tests/conftest.py``.

Reference: docs/DECISIONS.md D-178.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

psycopg = pytest.importorskip("psycopg")

from meridian.api.app import create_app  # noqa: E402
from meridian.api.dependencies import get_connection  # noqa: E402

pytestmark = pytest.mark.integration

STATION = "st_uptime"


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


@pytest.fixture
def station(rollback: Any, schedule_rows: Any) -> str:
    """Sixty heartbeats in the last complete hour, a third of them listening."""
    schedule_rows.station(STATION, simulated=True)
    rollback.execute(
        "insert into heartbeats (station_id, sent_at, received_at, state,"
        " listening_assignment_id, listening_satellite_id, listening_freq_hz,"
        " listening_mode, simulated)"
        " select %s, t, t, 'listening',"
        " case when n %% 3 = 0 then 'as_up' end,"
        " case when n %% 3 = 0 then 'norad:99970' end,"
        " case when n %% 3 = 0 then 137100000 end,"
        " case when n %% 3 = 0 then 'lrpt' end, true"
        " from generate_series(0, 59) as n,"
        " lateral (select date_trunc('hour', now()) - interval '1 hour'"
        "  + n * interval '1 minute' as t) as instant",
        (STATION,),
    )
    return STATION


def test_every_hour_is_there_and_the_last_one_is_counted(
    client: TestClient, station: str
) -> None:
    body = client.get(f"/api/v1/stations/{station}/uptime", params={"hours": 3}).json()

    assert body["simulated"] is True
    interval = body["heartbeat_interval_s"]
    hours = body["hours"]
    assert len(hours) == 3
    assert [one["heartbeats"] for one in hours] == [0, 0, 60]
    assert hours[-1]["listening"] == 20
    assert hours[-1]["coverage"] == round(min(1.0, 60 / (3600 // interval)), 3)
    assert hours[0]["hour"] < hours[1]["hour"] < hours[2]["hour"]


def test_hours_outside_a_week_are_refused(client: TestClient, station: str) -> None:
    for hours in (0, 169):
        response = client.get(
            f"/api/v1/stations/{station}/uptime", params={"hours": hours}
        )
        assert response.status_code == 400


def test_an_unknown_station_is_not_found(client: TestClient) -> None:
    assert client.get("/api/v1/stations/st_nobody/uptime").status_code == 404
