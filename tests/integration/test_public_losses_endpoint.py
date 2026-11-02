"""``/api/v1/losses``, end to end.

A diagnosed loss is listed newest first with its window widened, its cause, and
every cause tested with only the listed findings, rounded; only the
deployment's own diagnoses are read, and only an assignment's latest revision;
pages follow by cursor, and a foreign cursor is refused.

Marked ``integration`` by the directory hook in ``tests/conftest.py``.

Reference: docs/DECISIONS.md D-093, D-272, D-273, D-331.
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
from meridian.reliability.config import ReliabilityConfig  # noqa: E402
from meridian.store.loss_diagnoses import NewDiagnosis, insert_diagnosis  # noqa: E402
from meridian.store.pass_classifications import (  # noqa: E402
    NewClassification,
    insert_classification,
)

pytestmark = pytest.mark.integration

STATION = "st_losses_api"
AOS = datetime(2026, 10, 30, 6, 4, 17, tzinfo=UTC)
DEPLOYED = ReliabilityConfig().diagnosis.sha256()

CANDIDATES = [
    {
        "cause": "obstruction",
        "fired": True,
        "support": 0.734512,
        "found": {
            "samples": 40,
            "lost_inside": 9,
            "share": 0.41237,
            "marked": [{"azimuth_deg": 40.0, "width_deg": 30.0, "ceiling_deg": 22.5}],
        },
    },
    {
        "cause": "satellite_silent",
        "fired": False,
        "support": 0.0,
        "found": {"signals": 2, "silences": 0, "peak_elevation_deg": 61.37},
    },
    {
        "cause": "interference",
        "fired": False,
        "support": 0.0,
        "found": {"floor_dbfs": -61.234, "baseline_dbfs": -64.1, "baseline_count": 12},
    },
    {
        "cause": "timing_fault",
        "fired": False,
        "support": 0.0,
        "found": {"allowed_s": 6.2345, "recording_s": 0.04},
    },
]


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


def lost(rows: Any, name: str, aos: datetime, **changes: Any) -> str:
    """A scheduled pass, classified as lost and diagnosed."""
    held = rows.conn.execute(
        "select id from element_sets where satellite_id = 'norad:99970' limit 1"
    ).fetchone()
    element_set = held[0] if held else rows.satellite()
    pass_id = rows.pass_(STATION, aos, element_set_id=element_set)
    rows.assignment(name, pass_id, state="held")
    insert_classification(
        rows.conn,
        NewClassification(
            assignment_ids=[name],
            pass_id=pass_id,
            station_id=STATION,
            satellite_id="norad:99970",
            window_start=aos,
            window_end=aos + timedelta(minutes=11),
            classification="signal_no_decode",
            evidence={},
            method="classification-1",
            config_sha256=bytes([5]) * 32,
            simulated=True,
        ),
    )
    classification_id = rows.conn.execute(
        "select classification_id from pass_classifications where assignment_id = %s",
        (name,),
    ).fetchone()[0]
    values: dict[str, Any] = {
        "assignment_id": name,
        "revision": None,
        "observation_started_at": None,
        "station_id": STATION,
        "classification_id": classification_id,
        "cause": "obstruction",
        "candidates": CANDIDATES,
        "evidence": {},
        "method": "diagnosis-1",
        "config_sha256": DEPLOYED,
        "verdict_method": None,
        "simulated": True,
    } | changes
    assert insert_diagnosis(rows.conn, NewDiagnosis(**values))
    return name


def test_a_loss_is_published_with_every_cause_tested_and_only_listed_findings(
    client: TestClient, schedule_rows: Any
) -> None:
    schedule_rows.station(STATION, simulated=True)
    lost(schedule_rows, "as_loss_1", AOS)

    body = client.get("/api/v1/losses").json()

    (loss,) = body["items"]
    assert body["next_cursor"] is None
    assert loss["station_id"] == STATION
    assert loss["satellite_id"] == "norad:99970"
    assert loss["window_start"] == "2026-10-30T06:04:00Z"
    assert loss["window_end"] == "2026-10-30T06:16:00Z"
    assert loss["classification"] == "signal_no_decode"
    assert loss["cause"] == "obstruction"
    assert loss["method"] == "diagnosis-1"
    assert loss["simulated"] is True
    assert loss["tested"] == [
        {
            "cause": "obstruction",
            "fired": True,
            "support": 0.73,
            "findings": {"samples": 40, "lost_inside": 9, "share": 0.41},
        },
        {
            "cause": "satellite_silent",
            "fired": False,
            "support": 0.0,
            "findings": {"signals": 2, "silences": 0, "peak_elevation_deg": 61},
        },
        {
            "cause": "interference",
            "fired": False,
            "support": 0.0,
            "findings": {"baseline_count": 12},
        },
        {
            "cause": "timing_fault",
            "fired": False,
            "support": 0.0,
            "findings": {"allowed_s": 6.2, "recording_s": 0.0},
        },
    ]


def test_another_configurations_diagnosis_is_not_listed(
    client: TestClient, schedule_rows: Any
) -> None:
    schedule_rows.station(STATION, simulated=True)
    lost(schedule_rows, "as_loss_other", AOS, config_sha256=bytes([9]) * 32)

    assert client.get("/api/v1/losses").json()["items"] == []


def test_losses_page_newest_first_and_filter_by_station(
    client: TestClient, schedule_rows: Any
) -> None:
    schedule_rows.station(STATION, simulated=True)
    schedule_rows.station("st_losses_other", simulated=True)
    for index in range(3):
        lost(schedule_rows, f"as_loss_p{index}", AOS + timedelta(hours=index))

    first = client.get("/api/v1/losses", params={"limit": 2}).json()
    second = client.get(
        "/api/v1/losses", params={"limit": 2, "cursor": first["next_cursor"]}
    ).json()
    other = client.get("/api/v1/losses", params={"station_id": "st_losses_other"})

    names = [one["assignment_id"] for one in first["items"] + second["items"]]
    assert names == ["as_loss_p2", "as_loss_p1", "as_loss_p0"]
    assert second["next_cursor"] is None
    assert other.json()["items"] == []


def test_a_cursor_from_another_endpoint_is_refused(client: TestClient) -> None:
    from meridian.api.public.pagination import encode_cursor

    response = client.get("/api/v1/losses", params={"cursor": encode_cursor(("as_x",))})

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_query"
