"""``/api/v1/satellites`` against a real database and the real application.

The models and the store reads are tested apart from each other elsewhere. What
this file covers is the wiring between them: that a satellite written to the
database comes back through the endpoint aged and labelled, that an id
containing a colon routes at all, and that paging a real table hands out cursors
that advance.

Marked ``integration`` by the directory hook in ``tests/conftest.py``.

Reference: docs/DECISIONS.md D-021, D-066, D-083, D-084, D-085.
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
from meridian.store.element_sets import NewElementSet, insert_element_set  # noqa: E402
from meridian.store.satellites import (  # noqa: E402
    NewSatellite,
    NewTransmitter,
    insert_satellite,
    insert_transmitter,
)

pytestmark = pytest.mark.integration

SAMPLE_LINE_1 = "1 25544U 98067A   26225.50000000  .00016717  00000-0  10270-3 0  9004"
SAMPLE_LINE_2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.72125391563537"


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes."""
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def client(rollback: Any) -> Iterator[TestClient]:
    """The real application, reading through the rolled-back transaction."""
    app = create_app()
    app.dependency_overrides[get_connection] = lambda: rollback
    with TestClient(app, raise_server_exceptions=False) as started:
        yield started


def add(rollback: Any, satellite_id: str, **columns: Any) -> None:
    """Insert one satellite, then set columns the insertable form withholds."""
    insert_satellite(
        rollback, NewSatellite(satellite_id=satellite_id, name="Test object")
    )
    for column, value in columns.items():
        with rollback.cursor() as cur:
            cur.execute(
                f"update satellites set {column} = %s where satellite_id = %s",
                (value, satellite_id),
            )


def add_transmitter(
    rollback: Any, satellite_id: str, freq_hz: int, **fields: Any
) -> None:
    """Record one downlink for a satellite."""
    base = {
        "satellite_id": satellite_id,
        "centre_freq_hz": freq_hz,
        "mode": "lrpt",
        "polarisation": "rhcp",
        "bandwidth_hz": 120_000,
    }
    base.update(fields)
    insert_transmitter(rollback, NewTransmitter(**base))  # type: ignore[arg-type]


def add_element_set(rollback: Any, satellite_id: str, epoch: datetime) -> None:
    """Archive one element set whose first line carries its own epoch."""
    epoch_field = f"{epoch.year % 100:02d}{epoch.timetuple().tm_yday:03d}.50000000"
    insert_element_set(
        rollback,
        NewElementSet(
            satellite_id=satellite_id,
            epoch=epoch,
            line1=SAMPLE_LINE_1[:18] + epoch_field + SAMPLE_LINE_1[32:],
            line2=SAMPLE_LINE_2,
            source="manual",
        ),
    )


def test_an_id_containing_a_colon_routes(client: TestClient, rollback: Any) -> None:
    """`norad:25544` is the id everywhere, and a URL has to carry it unescaped.

    A colon is a legal path character (RFC 3986 §3.3), but it is the kind of
    thing that works in a router and breaks in a proxy — so it is asserted here
    rather than assumed, on the shape every satellite URL takes.
    """
    add(rollback, "norad:25544")

    response = client.get("/api/v1/satellites/norad:25544")

    assert response.status_code == 200
    assert response.json()["satellite_id"] == "norad:25544"


def test_a_listed_satellite_carries_its_weighting_and_age(
    client: TestClient, rollback: Any
) -> None:
    """The whole public contract for a satellite, on one response."""
    add(rollback, "norad:40069", priority=2.5)
    add_element_set(rollback, "norad:40069", datetime.now(UTC) - timedelta(hours=6))

    body = client.get("/api/v1/satellites").json()

    satellite = next(s for s in body["items"] if s["satellite_id"] == "norad:40069")
    assert satellite["priority"] == 2.5
    assert satellite["is_active"] is True
    assert satellite["latest_element_set_epoch"] is not None
    assert 6 * 3600 - 60 < satellite["element_set_age_s"] < 6 * 3600 + 60


def test_a_satellite_we_cannot_propagate_reports_no_age(
    client: TestClient, rollback: Any
) -> None:
    """Null, not zero — a gap in the archive is not a fresh element set."""
    add(rollback, "norad:40070")

    body = client.get("/api/v1/satellites/norad:40070").json()

    assert body["latest_element_set_epoch"] is None
    assert body["element_set_age_s"] is None


def test_a_silent_satellite_is_listed_and_labelled(
    client: TestClient, rollback: Any
) -> None:
    """Hiding it would make its empty week read as a prediction failure."""
    add(rollback, "norad:40071", active=False)

    body = client.get("/api/v1/satellites/norad:40071").json()

    assert body["is_active"] is False


def test_one_page_shares_one_clock(client: TestClient, rollback: Any) -> None:
    """Two satellites at one epoch must report one age, to the microsecond.

    A route that read the clock per row would differ by whatever the query took,
    which is small enough to look like data rather than like a bug.
    """
    epoch = datetime.now(UTC) - timedelta(days=2)
    add(rollback, "norad:40072")
    add(rollback, "norad:40073")
    add_element_set(rollback, "norad:40072", epoch)
    add_element_set(rollback, "norad:40073", epoch)

    items = client.get("/api/v1/satellites").json()["items"]

    ages = {
        s["element_set_age_s"]
        for s in items
        if s["satellite_id"] in {"norad:40072", "norad:40073"}
    }
    assert len(ages) == 1


def test_the_cursor_advances_through_the_catalogue(
    client: TestClient, rollback: Any
) -> None:
    """Two pages of one, and the second must not repeat the first."""
    add(rollback, "norad:40074")
    add(rollback, "norad:40075")

    first = client.get("/api/v1/satellites?limit=1").json()
    assert first["next_cursor"] is not None

    second = client.get(
        f"/api/v1/satellites?limit=1&cursor={first['next_cursor']}"
    ).json()

    assert second["items"][0]["satellite_id"] != first["items"][0]["satellite_id"]


def test_an_unknown_satellite_is_not_found(client: TestClient) -> None:
    """The public vocabulary — a code MSP §6 does not have (D-084)."""
    response = client.get("/api/v1/satellites/norad:99999")

    assert response.status_code == 404
    assert response.json() == {
        "error": "not_found",
        "message": "No satellite with that id.",
    }


def test_a_forged_cursor_is_refused_rather_than_reset(client: TestClient) -> None:
    """Serving page one would look to a reader like the catalogue restarted."""
    response = client.get("/api/v1/satellites?cursor=not-a-real-cursor")

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_query"


def test_transmitters_come_back_in_frequency_order(
    client: TestClient, rollback: Any
) -> None:
    """Ascending frequency, so a reader scans a band rather than a table."""
    add(rollback, "norad:40076")
    add_transmitter(rollback, "norad:40076", 137_900_000)
    add_transmitter(rollback, "norad:40076", 137_100_000)

    body = client.get("/api/v1/satellites/norad:40076/transmitters").json()

    assert [t["centre_freq_hz"] for t in body] == [137_100_000, 137_900_000]
    assert body[0]["polarisation"] == "rhcp"
    assert body[0]["source"] == "manual"


def test_a_satellite_with_no_recorded_downlink_gets_an_empty_list(
    client: TestClient, rollback: Any
) -> None:
    """Empty is a real answer — a catalogue gap, not a bad URL."""
    add(rollback, "norad:40077")

    response = client.get("/api/v1/satellites/norad:40077/transmitters")

    assert response.status_code == 200
    assert response.json() == []


def test_transmitters_for_an_unknown_satellite_are_not_found(
    client: TestClient,
) -> None:
    """Told apart from a real satellite whose downlinks nobody has entered."""
    response = client.get("/api/v1/satellites/norad:99999/transmitters")

    assert response.status_code == 404
