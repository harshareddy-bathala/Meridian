"""``/api/v1/stations`` against a real database and the real application.

The models and the store reads are tested apart from each other elsewhere. What
this file covers is the wiring between them: that a station written to the
database comes back through the endpoint coarsened, classified and labelled, and
that paging a real table hands out cursors that actually advance.

Marked ``integration`` by the directory hook in ``tests/conftest.py``.

Reference: docs/DECISIONS.md D-082, D-084, D-085.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

psycopg = pytest.importorskip("psycopg")

from meridian.api.app import create_app  # noqa: E402
from meridian.api.dependencies import get_connection  # noqa: E402
from meridian.store.stations import (  # noqa: E402
    Capability,
    NewStation,
    insert_station,
)

pytestmark = pytest.mark.integration

STORED_LAT_DEG = 12.971598
STORED_LON_DEG = 77.594562

SAMPLE_CAPABILITY = Capability(
    band="vhf",
    freq_min_hz=136_000_000,
    freq_max_hz=138_000_000,
    modes=("lrpt",),
    polarisation="rhcp",
    tracking=False,
    min_elevation_deg=10.0,
    horizon_mask_json='[{"az_deg": 90.0, "min_el_deg": 15.0}]',
)


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes."""
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def client(rollback: Any) -> Iterator[TestClient]:
    """The real application, reading through the rolled-back transaction.

    ``get_connection`` is overridden so the endpoints see the same uncommitted
    rows the test just wrote. Everything else — routing, error handlers, response
    models — is the application as it ships.
    """
    app = create_app()
    app.dependency_overrides[get_connection] = lambda: rollback
    with TestClient(app, raise_server_exceptions=False) as started:
        yield started


def add(rollback: Any, station_id: str, **overrides: Any) -> None:
    """Insert one station, with a token hash unique to its id."""
    base = NewStation(
        station_id=station_id,
        name="station-001",
        operator="meridian",
        lat_deg=STORED_LAT_DEG,
        lon_deg=STORED_LON_DEG,
        alt_m=920.4,
        token_sha256=station_id.encode().ljust(32, b"\0")[:32],
        registration_key_sha256=bytes(32),
        simulated=False,
        location_precision_decimals=2,
        simulator_run_id=None,
        seed=None,
        client_implementation="meridian-reference",
        client_version="0.1.0",
    )
    insert_station(rollback, replace(base, **overrides), [SAMPLE_CAPABILITY])


def test_a_listed_station_is_coarsened_and_labelled(
    client: TestClient, rollback: Any
) -> None:
    """The whole public contract for a station, on one response.

    The stored latitude must be absent from the body, not merely accompanied by
    a rounded one — this is the assertion that would fail if the rounding were
    removed from the model and nothing else changed.
    """
    add(rollback, "st_pub1", location_precision_decimals=1)

    body = client.get("/api/v1/stations").json()

    station = next(s for s in body["items"] if s["station_id"] == "st_pub1")
    assert station["location"] == {"lat_deg": 13.0, "lon_deg": 77.6, "alt_m": 920}
    assert station["location_precision_decimals"] == 1
    assert station["simulated"] is False
    assert station["liveness"] == "never_seen"
    assert str(STORED_LAT_DEG) not in client.get("/api/v1/stations").text


def test_no_listed_station_carries_a_credential(
    client: TestClient, rollback: Any
) -> None:
    """The projection is the boundary, asserted at the surface it protects."""
    add(rollback, "st_pub2", simulated=True, simulator_run_id="run-1", seed=4471)

    body = client.get("/api/v1/stations").text.lower()

    for forbidden in ("token", "registration_key", "seed", "invite"):
        assert forbidden not in body


def test_the_cursor_advances_through_the_directory(
    client: TestClient, rollback: Any
) -> None:
    """Two pages of one, and the second must not repeat the first.

    A cursor that was inclusive, or that encoded the wrong row, shows a reader
    the same station twice — which looks like duplicate data rather than a
    paging bug.
    """
    add(rollback, "st_page_a")
    add(rollback, "st_page_b")

    first = client.get("/api/v1/stations?limit=1").json()
    assert len(first["items"]) == 1
    assert first["next_cursor"] is not None

    second = client.get(
        f"/api/v1/stations?limit=1&cursor={first['next_cursor']}"
    ).json()

    assert second["items"][0]["station_id"] != first["items"][0]["station_id"]


def test_the_last_page_offers_no_cursor(client: TestClient, rollback: Any) -> None:
    """Absent means done. A cursor onto nothing would loop a client forever."""
    add(rollback, "st_only")

    body = client.get("/api/v1/stations?limit=50").json()

    assert body["next_cursor"] is None


def test_a_limit_outside_the_range_is_invalid_query(client: TestClient) -> None:
    """D-084's vocabulary, reached through FastAPI's validation and the guard."""
    response = client.get("/api/v1/stations?limit=500")

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_query"


def test_a_forged_cursor_is_refused_rather_than_reset(client: TestClient) -> None:
    """Serving page one would look to a reader like the list had restarted."""
    response = client.get("/api/v1/stations?cursor=not-a-real-cursor")

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_query"


def test_an_unknown_station_is_not_found(client: TestClient) -> None:
    """The public vocabulary — a code MSP §6 does not have (D-084)."""
    response = client.get("/api/v1/stations/st_nope")

    assert response.status_code == 404
    assert response.json() == {
        "error": "not_found",
        "message": "No station with that id.",
    }


def test_capabilities_come_back_with_the_declared_mask(
    client: TestClient, rollback: Any
) -> None:
    """The five columns the scheduler ignores are the ones a reader wants.

    The mask's keys are the assertion that matters. They are stored as `az_deg`
    and `min_el_deg` (D-031) and published spelled out, so a body carrying the
    stored spellings would mean the rename was skipped — and the endpoint would
    still look like it worked.
    """
    add(rollback, "st_caps")

    body = client.get("/api/v1/stations/st_caps/capabilities").json()

    assert len(body) == 1
    assert body[0]["polarisation"] == "rhcp"
    assert body[0]["tracking"] is False
    assert body[0]["horizon_mask"] == [{"azimuth_deg": 90.0, "min_elevation_deg": 15.0}]


def test_liveness_is_served_without_the_rest_of_the_station(
    client: TestClient, rollback: Any
) -> None:
    """A status light should not re-fetch a location that never changes."""
    add(rollback, "st_live", simulated=True, simulator_run_id="run-1", seed=1)

    body = client.get("/api/v1/stations/st_live/liveness").json()

    assert body == {
        "station_id": "st_live",
        "liveness": "never_seen",
        "last_heartbeat_at": None,
        "simulated": True,
    }
