"""The Phase 1 exit criterion, as far as a test can reach it.

*A virtual station is visible on the public site from outside the college
network.* "From outside" is an operator's check against the deployed hostname
(D-088's ``verify_public_surface.py``). Everything before that is here: a station
brought up by the real simulator, through the real reference client, over real
MSP, appears in the public read API the dashboard draws — labelled simulated,
live, located no more precisely than it asked, and carrying nothing that is not
meant to be public.

Stage 10's gate stops at the observation store. This file continues from the
same registration to the surface a reader sees, which is the half of the chain
that did not exist when that gate was written.

Marked ``e2e`` by the directory hook in ``tests/conftest.py``.

Reference: CLAUDE.md "Current phase"; docs/DECISIONS.md D-082, D-083, D-088.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from meridian.api.app import create_app
from meridian.api.dependencies import get_connection
from meridian.store.invites import hash_invite_token
from meridian_sim import supervisor as supervisor_module
from meridian_sim.config import RunConfig
from meridian_sim.supervisor import Supervisor

MASTER_SEED = 4471

FORBIDDEN_KEY_PARTS = ("token", "invite", "seed", "registration_key", "health")
"""The same list ``deploy/tools/verify_public_surface.py`` checks the deployed
hostname against, so a leak fails here before it can fail there."""


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fleet is ticked by hand, so nothing should sleep."""
    monkeypatch.setattr(supervisor_module, "_sleep", lambda _seconds: None)


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def started(
    database_url: str, rollback: Any, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """The application, reading and writing through this test's transaction."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("TOKEN_HASH_PEPPER", "published-pepper")
    app = create_app()
    app.dependency_overrides[get_connection] = lambda: rollback
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def station_id(started: TestClient, rollback: Any, tmp_path: Path) -> str:
    """One virtual station, registered and one heartbeat in."""
    token = "published-invite"
    with rollback.cursor() as cur:
        cur.execute(
            "insert into invite_tokens (token_sha256, label) values (%s, %s)",
            (hash_invite_token(token), "exit-criterion"),
        )
    config = RunConfig(
        master_seed=MASTER_SEED,
        run_id="published-run",
        station_count=1,
        base_url="http://platform.test",
        state_dir=tmp_path / "state",
    )
    fleet = Supervisor(config, [token], started._transport)
    with fleet:
        (registered,) = fleet.bring_up()
        fleet.tick_round(0, datetime.now(UTC))
    return registered


def _published(client: TestClient, station_id: str) -> dict[str, Any]:
    """The station's entry in the directory, walking every page."""
    cursor: str | None = None
    while True:
        params = {"limit": 200} | ({"cursor": cursor} if cursor else {})
        response = client.get("/api/v1/stations", params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        for item in body["items"]:
            if item["station_id"] == station_id:
                return item
        cursor = body.get("next_cursor")
        assert cursor, f"{station_id} is not in the public station directory"


def _keys(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, inner in value.items():
            yield key
            yield from _keys(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _keys(inner)


def test_the_virtual_station_is_in_the_public_directory_as_simulated(
    started: TestClient, station_id: str
) -> None:
    """CLAUDE.md rule 5, on the response the dashboard's map is drawn from."""
    item = _published(started, station_id)

    assert item["simulated"] is True
    assert item["liveness"] == "online"
    assert item["last_heartbeat_at"] is not None


def test_the_detail_endpoint_agrees_with_the_directory(
    started: TestClient, station_id: str
) -> None:
    """A reader who clicks a station sees the fields they were looking at."""
    detail = started.get(f"/api/v1/stations/{station_id}")

    assert detail.status_code == 200
    assert detail.json() == _published(started, station_id)


def test_the_published_location_is_no_finer_than_the_station_declared(
    started: TestClient, rollback: Any, station_id: str
) -> None:
    """D-082: the stored position is exact; the published one is not."""
    with rollback.cursor() as cur:
        cur.execute(
            "select lat_deg, lon_deg, location_precision_decimals"
            " from stations where station_id = %s",
            (station_id,),
        )
        lat, lon, decimals = cur.fetchone()

    location = _published(started, station_id)["location"]

    assert location["lat_deg"] == round(lat, decimals)
    assert location["lon_deg"] == round(lon, decimals)


def test_nothing_private_about_the_station_is_published(
    started: TestClient, station_id: str
) -> None:
    """The simulator's seed and the station's credentials stay in the database."""
    body = started.get(f"/api/v1/stations/{station_id}").json()

    leaked = [
        key
        for key in _keys(body)
        if any(part in key.lower() for part in FORBIDDEN_KEY_PARTS)
    ]
    assert leaked == []
