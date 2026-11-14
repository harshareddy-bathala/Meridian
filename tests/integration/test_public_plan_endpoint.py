"""``/api/v1/plan`` against a real database and the real propagator.

``tests/unit/test_station_plan.py`` checks the planner's rules with a stub
orbit. This covers what that cannot: the satellites searched are the
catalogue's with a live downlink, a real element set gives real passes over a
real place, a published model scores them, and nothing is written.

Marked ``integration`` by the directory hook in ``tests/conftest.py``.

Reference: docs/DECISIONS.md D-086, D-093, D-203, D-370.
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
from meridian.api.public import plan  # noqa: E402
from meridian.prediction.features import FEATURE_VERSION  # noqa: E402
from meridian.store.element_sets import NewElementSet, insert_element_set  # noqa: E402
from meridian.store.satellites import (  # noqa: E402
    NewSatellite,
    NewTransmitter,
    insert_satellite,
    insert_transmitter,
)

pytestmark = pytest.mark.integration

CATALOGUE = Path(__file__).resolve().parents[2] / "deploy" / "catalogue"
LINES = json.loads((CATALOGUE / "development.json").read_text(encoding="utf-8"))[
    "satellites"
][0]["element_sets"][0]
"""A real element set, so the propagator has something it accepts."""

SATELLITE = "norad:990101"


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes."""
    with conn.transaction(force_rollback=True):
        yield conn


def started(rollback: Any) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_connection] = lambda: rollback
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client(rollback: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The real application with no planner model, and a cache of its own."""
    monkeypatch.setattr(plan, "_CACHE", plan._WindowCache())
    monkeypatch.delenv("PLAN_MODEL", raising=False)
    with started(rollback) as one:
        yield one


def a_satellite(rollback: Any, *, downlink: bool = True) -> None:
    """A satellite with a set from six hours ago and, unless told not, a downlink."""
    insert_satellite(rollback, NewSatellite(satellite_id=SATELLITE, name="Test LRPT"))
    if downlink:
        insert_transmitter(
            rollback,
            NewTransmitter(
                satellite_id=SATELLITE, centre_freq_hz=137_900_000, mode="lrpt"
            ),
        )
    epoch = datetime.now(UTC) - timedelta(hours=6)
    epoch_field = f"{epoch.year % 100:02d}{epoch.timetuple().tm_yday:03d}.50000000"
    line1 = LINES["line1"]
    insert_element_set(
        rollback,
        NewElementSet(
            satellite_id=SATELLITE,
            epoch=epoch,
            line1=line1[:18] + epoch_field + line1[32:],
            line2=LINES["line2"],
            source="manual",
        ),
    )


def a_plan_model(path: Path) -> Path:
    body = {
        "schema": "plan-model-1",
        "model_sha256": "ab" * 32,
        "configuration": "A",
        "population": "own",
        "as_of": "2026-11-01T00:00:00+00:00",
        "dataset_sha256": "cd" * 32,
        "fitted": {
            "model_format": 1,
            "feature_version": FEATURE_VERSION,
            "configuration": "A",
            "population": "own",
            "reads_history": False,
            "min_station_history": 5,
            "configured": {
                "features": ["max_elevation_deg"],
                "mean": [35.0],
                "scale": [18.0],
                "coefficients": [1.2],
                "intercept": -0.3,
                "calibration": {"method": "platt", "a": 0.9, "b": 0.1},
            },
            "fallback": None,
        },
    }
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def mine(body: dict[str, Any]) -> list[dict[str, Any]]:
    return [one for one in body["passes"] if one["satellite_id"] == SATELLITE]


def test_a_place_gets_two_days_of_real_passes_with_their_peaks(
    client: TestClient, rollback: Any
) -> None:
    a_satellite(rollback)

    answer = client.get("/api/v1/plan", params={"lat": 12.3456, "lon": 77.5678})

    assert answer.status_code == 200
    body = answer.json()
    assert body["status"] == "computed"
    assert body["place"] == {"lat_deg": 12.3, "lon_deg": 77.6, "alt_m": 0}
    assert body["min_elevation_deg"] == 10
    passes = mine(body)
    # A polar orbit passes over any place a few times a day above 10°.
    assert len(passes) >= 4
    for one in passes:
        assert one["los"] > body["at"] and one["aos"] < body["until"]
        assert one["aos"].endswith(":00Z") and one["los"].endswith(":00Z")
        assert 10 <= one["max_elevation_deg"] <= 90
        assert one["predicted_yield"]["status"] == "not_measured"
    (searched,) = [s for s in body["satellites"] if s["satellite_id"] == SATELLITE]
    assert searched["downlinks"] == [{"centre_freq_hz": 137_900_000, "mode": "lrpt"}]
    assert body["yield_model"]["status"] == "not_yet_computed"


def test_a_satellite_without_a_live_downlink_is_not_searched(
    client: TestClient, rollback: Any
) -> None:
    a_satellite(rollback, downlink=False)

    body = client.get("/api/v1/plan", params={"lat": 51.5, "lon": -0.1}).json()

    assert SATELLITE not in {s["satellite_id"] for s in body["satellites"]}
    assert SATELLITE not in {s["satellite_id"] for s in body["omitted"]}


def test_nothing_is_written_for_a_place(client: TestClient, rollback: Any) -> None:
    a_satellite(rollback)
    with rollback.cursor() as cur:
        cur.execute("select count(*) from passes")
        before = cur.fetchone()

    client.get("/api/v1/plan", params={"lat": -33.9, "lon": 151.2})

    with rollback.cursor() as cur:
        cur.execute("select count(*) from passes")
        assert cur.fetchone() == before


def test_a_published_model_scores_every_pass(
    rollback: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    a_satellite(rollback)
    monkeypatch.setattr(plan, "_CACHE", plan._WindowCache())
    monkeypatch.setenv("PLAN_MODEL", str(a_plan_model(tmp_path / "plan-model.json")))

    with started(rollback) as client:
        body = client.get("/api/v1/plan", params={"lat": 12.3, "lon": 77.6}).json()

    assert body["yield_model"]["model_sha256"] == "ab" * 32
    yields = [one["predicted_yield"] for one in mine(body)]
    assert yields
    assert all(one["status"] == "computed" for one in yields)
    assert all(0 <= one["probability"] <= 1 for one in yields)


def test_a_refused_model_is_a_server_error_naming_the_setting(
    rollback: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    damaged = tmp_path / "plan-model.json"
    damaged.write_text('{"schema": "plan-model-1"}', encoding="utf-8")
    monkeypatch.setenv("PLAN_MODEL", str(damaged))

    with started(rollback) as client:
        answer = client.get("/api/v1/plan", params={"lat": 12.3, "lon": 77.6})

    assert answer.status_code == 500
    assert answer.json()["error"] == "server_error"
    assert "PLAN_MODEL" in answer.json()["message"]
    assert str(damaged) not in answer.text


@pytest.mark.parametrize(
    "params",
    [
        {"lat": 91, "lon": 0},
        {"lat": 0, "lon": -181},
        {"lat": "nan", "lon": 0},
        {"lat": "inf", "lon": 0},
        {"lat": 12.3},
        {},
    ],
)
def test_a_place_off_the_globe_is_an_invalid_query(
    client: TestClient, params: dict[str, object]
) -> None:
    answer = client.get("/api/v1/plan", params=params)

    assert answer.status_code == 400
    assert answer.json()["error"] == "invalid_query"
