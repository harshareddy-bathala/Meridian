"""``/api/v1/events`` against a real database and the real application.

Pins the feed the live page reads: each kind read from the record that says so,
newest first, a pass's decode once however often it is resubmitted, only the
deployment's own diagnoses and warnings, nothing past the week or from a
deleted station, and a cursor that walks every page with no event twice (D-363).

Marked ``integration`` by the directory hook in ``tests/conftest.py``.

Reference: docs/DECISIONS.md D-085, D-272, D-292, D-363.
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
from meridian.reliability.watch_run import watched_under  # noqa: E402
from meridian.store.loss_diagnoses import NewDiagnosis, insert_diagnosis  # noqa: E402
from meridian.store.receive_chain_warnings import (  # noqa: E402
    NewWarning,
    clear_warning,
    raise_warning,
)
from meridian.store.signal_baselines import NewBaseline, insert_baseline  # noqa: E402

pytestmark = pytest.mark.integration

STATION = "st_events"
CONFIG = ReliabilityConfig()
UNDER = watched_under(CONFIG)
NOW = datetime.now(UTC)


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def client(rollback: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.delenv("MERIDIAN_RELIABILITY_CONFIG", raising=False)
    app = create_app()
    app.dependency_overrides[get_connection] = lambda: rollback
    with TestClient(app, raise_server_exceptions=False) as started:
        yield started


def diagnose(rows: Any, world: Any, assignment: str, sha256: bytes) -> None:
    classification = world.classified(assignment, "station_unavailable")
    station, simulated = rows.conn.execute(
        "select station_id, simulated from assignments where assignment_id = %s",
        (assignment,),
    ).fetchone()
    insert_diagnosis(
        rows.conn,
        NewDiagnosis(
            assignment_id=assignment,
            revision=None,
            observation_started_at=None,
            station_id=station,
            classification_id=classification,
            cause="undetermined",
            candidates=[],
            evidence={},
            method="diagnosis-1",
            config_sha256=sha256,
            verdict_method=None,
            simulated=simulated,
        ),
    )


def chain_warning(conn: Any, raised: datetime, cleared: datetime) -> int:
    chain = int(
        conn.execute(
            "insert into station_capabilities (station_id, band, freq_min_hz,"
            " freq_max_hz, modes, polarisation, min_elevation_deg)"
            " values (%s, 'vhf', 137000000, 138000000, '{lrpt}', 'rhcp', 5)"
            " returning id",
            (STATION,),
        ).fetchone()[0]
    )
    base = insert_baseline(
        conn,
        NewBaseline(
            station_id=STATION,
            capability_id=chain,
            evidence="samples",
            bin_width_deg=10.0,
            bins=[
                {
                    "elevation_deg": 10.0,
                    "median_db": 4.0,
                    "p10_db": 2.0,
                    "receptions": 9,
                    "samples": 120,
                }
            ],
            receptions=12,
            trained_from=raised - timedelta(days=14),
            trained_until=raised - timedelta(hours=1),
            method=UNDER.baseline_method,
            config_sha256=UNDER.baseline_sha256,
        ),
    )
    warning = raise_warning(
        conn,
        NewWarning(
            baseline_id=base,
            raised_at=raised,
            shortfall_db=4.2,
            affected_bins=[
                {"elevation_deg": 10.0, "shortfall_db": 4.4, "receptions": 3}
            ],
            evidence={},
            method=UNDER.watch_method,
            config_sha256=UNDER.watch_sha256,
        ),
    )
    assert warning is not None
    clear_warning(conn, warning, at=cleared, shortfall_db=0.5)
    return warning


@pytest.fixture
def network(schedule_rows: Any, world: Any) -> dict[str, Any]:
    """One station with one of every kind, a deleted station, and old history."""
    rows = schedule_rows
    elements = world.element_set
    rows.station(STATION, simulated=False)
    decoded = rows.pass_(STATION, NOW - timedelta(hours=3), element_set_id=elements)
    rows.assignment("as_ev_decoded", decoded, state="reported")
    rows.observation("as_ev_decoded", revision=1, outcome="decoded")
    rows.observation("as_ev_decoded", revision=2, outcome="decoded")
    lost = rows.pass_(STATION, NOW - timedelta(hours=5), element_set_id=elements)
    rows.assignment("as_ev_lost", lost, state="held")
    diagnose(rows, world, "as_ev_lost", CONFIG.diagnosis.sha256())
    other = rows.pass_(STATION, NOW - timedelta(hours=6), element_set_id=elements)
    rows.assignment("as_ev_other_config", other, state="held")
    diagnose(rows, world, "as_ev_other_config", bytes(32))
    warning = chain_warning(
        rows.conn, NOW - timedelta(hours=2), NOW - timedelta(hours=1)
    )

    rows.station("st_events_gone", simulated=True, deleted=True)
    rows.station("st_events_old", simulated=True)
    rows.conn.execute(
        "update stations set registered_at = %s where station_id = 'st_events_old'",
        (NOW - timedelta(days=10),),
    )
    return {"warning": str(warning)}


def mine(body: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        (one["kind"], one["ref"])
        for one in body["items"]
        if one["station_id"].startswith("st_events")
    ]


def test_each_kind_is_read_from_its_record_newest_first(
    client: TestClient, network: dict[str, Any]
) -> None:
    response = client.get("/api/v1/events?limit=200")

    assert response.status_code == 200
    body = response.json()
    events = mine(body)
    # The registration, the report and the diagnosis were written in this
    # transaction, so they share its `now()`; one instant sorts by kind.
    assert [kind for kind, _ in events] == [
        "station_joined",
        "pass_decoded",
        "loss_diagnosed",
        "warning_cleared",
        "warning_raised",
    ]
    assert events[0] == ("station_joined", STATION)
    assert events[3:] == [
        ("warning_cleared", network["warning"]),
        ("warning_raised", network["warning"]),
    ]
    times = [one["at"] for one in body["items"]]
    assert times == sorted(times, reverse=True)


@pytest.mark.usefixtures("network")
def test_a_decode_is_one_event_however_often_it_is_resubmitted(
    client: TestClient,
) -> None:
    body = client.get("/api/v1/events?limit=200").json()

    decodes = [
        one
        for one in body["items"]
        if one["kind"] == "pass_decoded" and one["assignment_id"] == "as_ev_decoded"
    ]
    assert len(decodes) == 1
    assert decodes[0]["satellite_id"] == "norad:99970"
    assert decodes[0]["simulated"] is False


@pytest.mark.usefixtures("network")
def test_only_the_deployments_own_diagnoses_are_events(client: TestClient) -> None:
    body = client.get("/api/v1/events?limit=200").json()

    losses = [one for one in body["items"] if one["kind"] == "loss_diagnosed"]
    assert [one["assignment_id"] for one in losses if one["station_id"] == STATION] == [
        "as_ev_lost"
    ]
    assert losses[0]["cause"] == "undetermined"


@pytest.mark.usefixtures("network")
def test_a_deleted_station_and_last_months_history_are_left_out(
    client: TestClient,
) -> None:
    stations = {
        one["station_id"]
        for one in client.get("/api/v1/events?limit=200").json()["items"]
    }

    assert STATION in stations
    assert "st_events_gone" not in stations
    assert "st_events_old" not in stations


@pytest.mark.usefixtures("network")
def test_a_cursor_walks_every_page_with_no_event_twice(client: TestClient) -> None:
    everything = client.get("/api/v1/events?limit=200").json()["items"]
    walked: list[dict[str, Any]] = []
    url = "/api/v1/events?limit=2"
    while True:
        page = client.get(url).json()
        walked += page["items"]
        if page["next_cursor"] is None:
            break
        url = f"/api/v1/events?limit=2&cursor={page['next_cursor']}"

    assert walked == everything


def test_a_cursor_it_did_not_issue_is_refused(client: TestClient) -> None:
    response = client.get("/api/v1/events?cursor=bm90LWEtY3Vyc29y")

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_query"
