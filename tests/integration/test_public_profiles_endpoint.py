"""``/api/v1/stations/{id}/profiles``, end to end.

Declared and learned are two fields, never one; the newest of each is served;
a mask since cleared is not shown; and every body states its provenance.

Marked ``integration`` by the directory hook in ``tests/conftest.py``.

Reference: docs/DECISIONS.md D-031, D-174, D-175.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

psycopg = pytest.importorskip("psycopg")

from meridian.api.app import create_app  # noqa: E402
from meridian.api.dependencies import get_connection  # noqa: E402
from meridian.profile_build import build_profiles  # noqa: E402
from meridian.store.profiles import (  # noqa: E402
    HorizonBin,
    InterferenceRow,
    LearnedBuild,
    insert_learned_horizon,
    insert_learned_interference,
)

pytestmark = pytest.mark.integration

STATION = "st_prof_api"
TRAINED_FROM = datetime(2026, 8, 1, tzinfo=UTC)
TRAINED_UNTIL = datetime(2026, 9, 1, tzinfo=UTC)
MASK = '[{"az_deg": 90, "min_el_deg": 8}, {"az_deg": 315, "min_el_deg": 30}]'


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
    schedule_rows.station(STATION, simulated=True)
    rollback.execute(
        "insert into station_capabilities (station_id, band, freq_min_hz,"
        " freq_max_hz, modes, polarisation, min_elevation_deg, horizon_mask_json)"
        " values (%s, 'vhf', 136000000, 138000000, '{lrpt}', 'rhcp', 10, %s)",
        (STATION, MASK),
    )
    return STATION


def _learn(
    conn: Any, sha: bytes, floor: float, until: datetime = TRAINED_UNTIL
) -> None:
    build = LearnedBuild(
        station_id=STATION,
        method="d159-v1",
        dataset_sha256=sha,
        trained_from=TRAINED_FROM,
        trained_until=until,
    )
    insert_learned_horizon(conn, build, [HorizonBin(40.0, 10.0, floor, 5)])
    insert_learned_interference(
        conn,
        build,
        [InterferenceRow(0.0, 45.0, 4, 4, 1.5, -97.0, 3, 20.0, 32.8)],
    )


def test_declared_and_learned_are_served_apart_with_provenance(
    client: TestClient, rollback: Any, station: str, tmp_path: Path
) -> None:
    build_profiles(rollback, tmp_path)
    _learn(rollback, bytes([1]) * 32, 10.0)

    body = client.get(f"/api/v1/stations/{station}/profiles").json()

    assert set(body) == {
        "station_id",
        "simulated",
        "declared",
        "learned",
        "interference",
    }
    assert body["simulated"] is True
    (declared,) = body["declared"]
    assert [
        (one["azimuth_deg"], one["azimuth_width_deg"], one["min_elevation_deg"])
        for one in declared["bins"]
    ] == [(90.0, 225.0, 8.0), (315.0, 135.0, 30.0)]
    assert all(one["sample_count"] is None for one in declared["bins"])
    learned = body["learned"]
    assert learned["dataset_sha256"] == "01" * 32
    assert learned["bins"] == [
        {
            "azimuth_deg": 40.0,
            "azimuth_width_deg": 10.0,
            "min_elevation_deg": 10.0,
            "sample_count": 5,
        }
    ]
    assert body["interference"]["station_median_dbfs"] == -97.0
    assert body["interference"]["cells"][0]["gain_max_db"] == 32.8


def test_the_profile_of_the_latest_dataset_is_the_one_served(
    client: TestClient, rollback: Any, station: str
) -> None:
    """By the dataset's reach, not by build order: the later one is built first."""
    _learn(rollback, bytes([2]) * 32, 14.0, until=datetime(2026, 9, 20, tzinfo=UTC))
    _learn(rollback, bytes([1]) * 32, 10.0)

    learned = client.get(f"/api/v1/stations/{station}/profiles").json()["learned"]

    assert learned["dataset_sha256"] == "02" * 32
    assert learned["bins"][0]["min_elevation_deg"] == 14.0


def test_a_station_with_nothing_yet_has_null_and_empty(
    client: TestClient, station: str
) -> None:
    body = client.get(f"/api/v1/stations/{station}/profiles").json()

    assert body["declared"] == []
    assert body["learned"] is None
    assert body["interference"] is None


def test_a_mask_since_cleared_is_not_shown(
    client: TestClient, rollback: Any, station: str, tmp_path: Path
) -> None:
    build_profiles(rollback, tmp_path)
    rollback.execute(
        "update station_capabilities set horizon_mask_json = '[]'::jsonb"
        " where station_id = %s",
        (station,),
    )

    body = client.get(f"/api/v1/stations/{station}/profiles").json()

    assert body["declared"] == []


def test_an_unknown_station_is_not_found(client: TestClient) -> None:
    response = client.get("/api/v1/stations/st_nobody/profiles")

    assert response.status_code == 404
