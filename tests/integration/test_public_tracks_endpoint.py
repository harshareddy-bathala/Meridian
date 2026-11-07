"""``/api/v1/satellites/tracks`` against a real database and the real propagator.

``tests/unit/test_satellite_tracks.py`` checks the body's rules with a fake
propagator. This covers what that cannot: the route is matched before
``/satellites/{satellite_id}``, the element set chosen is the one pass
generation would choose, and a real set propagates to a track on the globe.

Marked ``integration`` by the directory hook in ``tests/conftest.py``.

Reference: docs/DECISIONS.md D-321, D-325, D-361.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

psycopg = pytest.importorskip("psycopg")

from meridian.api.app import create_app  # noqa: E402
from meridian.api.dependencies import get_connection  # noqa: E402
from meridian.api.public import tracks  # noqa: E402
from meridian.store.element_sets import (  # noqa: E402
    NewElementSet,
    find_current_element_sets,
    insert_element_set,
)
from meridian.store.satellites import NewSatellite, insert_satellite  # noqa: E402

pytestmark = pytest.mark.integration

CATALOGUE = Path(__file__).resolve().parents[2] / "deploy" / "catalogue"
LINES = json.loads((CATALOGUE / "development.json").read_text(encoding="utf-8"))[
    "satellites"
][0]["element_sets"][0]
"""A real element set, so the propagator has something it accepts."""


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes."""
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def client(rollback: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The real application, with a cache of its own for each test."""
    monkeypatch.setattr(tracks, "_CACHE", tracks._MinuteCache())
    app = create_app()
    app.dependency_overrides[get_connection] = lambda: rollback
    with TestClient(app, raise_server_exceptions=False) as started:
        yield started


def add_satellite(rollback: Any, satellite_id: str) -> None:
    insert_satellite(
        rollback, NewSatellite(satellite_id=satellite_id, name="Test object")
    )


def add_set(rollback: Any, satellite_id: str, epoch: datetime) -> None:
    """Archive one set whose first line carries ``epoch``.

    A set is identified by its contents (D-057), so each epoch has to be written
    into the line for two sets to be two rows.
    """
    epoch_field = f"{epoch.year % 100:02d}{epoch.timetuple().tm_yday:03d}.50000000"
    line1 = LINES["line1"]
    insert_element_set(
        rollback,
        NewElementSet(
            satellite_id=satellite_id,
            epoch=epoch,
            line1=line1[:18] + epoch_field + line1[32:],
            line2=LINES["line2"],
            source="manual",
        ),
    )


def test_the_tracks_route_is_not_read_as_a_satellite_id(client: TestClient) -> None:
    body = client.get("/api/v1/satellites/tracks").json()

    assert body["status"] == "computed"
    assert (body["step_s"], body["horizon_s"]) == (60, 5400)


def test_a_satellite_with_an_element_set_is_tracked_over_the_globe(
    client: TestClient, rollback: Any
) -> None:
    add_satellite(rollback, "norad:990001")
    add_set(rollback, "norad:990001", datetime.now(UTC) - timedelta(hours=6))

    body = client.get("/api/v1/satellites/tracks").json()

    track = next(s for s in body["satellites"] if s["satellite_id"] == "norad:990001")
    assert len(track["track"]) == 91
    assert track["track"][0]["t"] == body["at"]
    assert all(-90 <= p["lat_deg"] <= 90 for p in track["track"])
    assert all(-180 <= p["lon_deg"] <= 180 for p in track["track"])
    assert 6 * 3600 - 120 < track["element_set_age_s"] < 6 * 3600 + 120
    assert track["element_set_source"] == "manual"


def test_a_satellite_without_an_element_set_is_named_not_dropped(
    client: TestClient, rollback: Any
) -> None:
    add_satellite(rollback, "norad:990002")

    body = client.get("/api/v1/satellites/tracks").json()

    assert {"satellite_id": "norad:990002", "name": "Test object"} | {
        "reason": "no_element_set"
    } in body["omitted"]


def test_the_set_chosen_is_the_one_pass_generation_would_choose(rollback: Any) -> None:
    now = datetime.now(UTC)
    add_satellite(rollback, "norad:990003")
    add_set(rollback, "norad:990003", now - timedelta(days=2))
    add_set(rollback, "norad:990003", now - timedelta(hours=3))
    add_set(rollback, "norad:990003", now + timedelta(hours=1))
    add_satellite(rollback, "norad:990004")
    add_set(rollback, "norad:990004", now - timedelta(hours=3))
    with rollback.cursor() as cur:
        cur.execute(
            "update satellites set deleted_at = now() where satellite_id = %s",
            ("norad:990004",),
        )

    current = find_current_element_sets(rollback, now)

    # Not the future set, and not a deleted satellite's. The archive's carried
    # sets are refused too (D-325): test_satnogs_archive_load.py loads real ones.
    assert current["norad:990003"].epoch == now - timedelta(hours=3)
    assert "norad:990004" not in current
