"""``/api/v1/schedule`` and ``/api/v1/schedule-runs/latest``, end to end.

A station's schedule holds its decisions over the next hours, scheduled and
skipped, each with its pass's peak to the degree and its element set's epoch,
and nothing past the span; an unknown station is ``not_found``. The latest run
is the newest record, with its decisions counted by population and its gain
over a baseline stated as not measured.

Marked ``integration`` by the directory hook in ``tests/conftest.py``.

Reference: docs/DECISIONS.md D-093, D-170, D-172, D-331.
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
from meridian.api.public.models.schedule import BASELINE_GAIN_REASON  # noqa: E402

pytestmark = pytest.mark.integration

STATION = "st_schedule_api"


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


def run(conn: Any, run_id: str, decided_at: datetime) -> str:
    conn.execute(
        "insert into schedule_runs (run_id, decided_at, horizon_start, horizon_end,"
        " model_config, config_sha256, parameters, yield_source, solver,"
        " solver_version, status, objective, bound, time_limit_s, runtime_s,"
        " stations, candidates, scheduled, skipped)"
        " values (%s, %s, %s, %s, 'A', %s, '{}', 'elevation_proxy', 'highs',"
        " '1.11.0', 'optimal', 4.25, 4.25, 10, 0.4, 2, 3, 2, 1)",
        (
            run_id,
            decided_at,
            decided_at,
            decided_at + timedelta(hours=6),
            bytes([3]) * 32,
        ),
    )
    return run_id


def at(rows: Any, now: datetime, hours: int, element_set: int) -> int:
    """A pass of the test station rising ``hours`` from ``now``."""
    return int(
        rows.pass_(STATION, now + timedelta(hours=hours), element_set_id=element_set)
    )


def test_a_schedule_holds_the_spans_decisions_with_their_orbit(
    client: TestClient, rollback: Any, schedule_rows: Any
) -> None:
    schedule_rows.station(STATION, simulated=True)
    element_set = schedule_rows.satellite()
    now = datetime.now(UTC)
    soon = schedule_rows.pass_(
        STATION, now + timedelta(hours=1), element_set_id=element_set
    )
    later = schedule_rows.pass_(
        STATION, now + timedelta(hours=2), element_set_id=element_set
    )
    beyond = schedule_rows.pass_(
        STATION, now + timedelta(hours=9), element_set_id=element_set
    )
    schedule_rows.assignment("as_sched_1", soon)
    schedule_rows.assignment(
        "as_sched_2",
        later,
        decision="skipped",
        reason="overlaps a better pass",
        conflicts_with_assignment_id="as_sched_1",
    )
    schedule_rows.assignment("as_sched_3", beyond)
    epoch = rollback.execute(
        "select epoch from element_sets where id = %s", (element_set,)
    ).fetchone()[0]

    body = client.get("/api/v1/schedule", params={"station_id": STATION}).json()

    assert body["station_id"] == STATION
    assert body["simulated"] is True
    assert body["start"].endswith(":00Z")
    assert [one["assignment"]["assignment_id"] for one in body["entries"]] == [
        "as_sched_1",
        "as_sched_2",
    ]
    first, second = body["entries"]
    assert first["max_elevation_deg"] == 61
    assert datetime.fromisoformat(first["element_set_epoch"]) == epoch
    assert first["assignment"]["decision"] == "scheduled"
    assert first["assignment"]["start_at"].endswith(":00Z")
    assert second["assignment"]["decision"] == "skipped"
    assert second["assignment"]["state"] is None
    assert second["assignment"]["conflicts_with_assignment_id"] == "as_sched_1"


def test_a_longer_span_reaches_further(client: TestClient, schedule_rows: Any) -> None:
    schedule_rows.station(STATION, simulated=False)
    element_set = schedule_rows.satellite()
    beyond = schedule_rows.pass_(
        STATION, datetime.now(UTC) + timedelta(hours=9), element_set_id=element_set
    )
    schedule_rows.assignment("as_sched_far", beyond)

    short = client.get("/api/v1/schedule", params={"station_id": STATION}).json()
    long = client.get(
        "/api/v1/schedule", params={"station_id": STATION, "hours": 12}
    ).json()
    too_long = client.get(
        "/api/v1/schedule", params={"station_id": STATION, "hours": 25}
    )

    assert short["entries"] == []
    assert [one["assignment"]["assignment_id"] for one in long["entries"]] == [
        "as_sched_far"
    ]
    assert too_long.status_code == 400


def test_an_unknown_station_or_none_named_is_refused(client: TestClient) -> None:
    missing = client.get("/api/v1/schedule", params={"station_id": "st_nobody"})
    unnamed = client.get("/api/v1/schedule")

    assert missing.status_code == 404
    assert missing.json()["error"] == "not_found"
    assert unnamed.status_code == 400


def test_the_latest_run_is_the_newest_counted_by_population(
    client: TestClient, rollback: Any, schedule_rows: Any
) -> None:
    schedule_rows.station(STATION, simulated=True)
    schedule_rows.station("st_schedule_real", simulated=False)
    element_set = schedule_rows.satellite()
    now = datetime.now(UTC)
    run(rollback, "run_old", now - timedelta(minutes=10))
    newest = run(rollback, "run_new", now - timedelta(minutes=5))
    for index, (name, station) in enumerate(
        (("as_run_1", STATION), ("as_run_2", STATION), ("as_run_3", "st_schedule_real"))
    ):
        pass_id = schedule_rows.pass_(
            station, now + timedelta(hours=1 + index), element_set_id=element_set
        )
        schedule_rows.assignment(name, pass_id)
        rollback.execute(
            "update assignments set schedule_run_id = %s where assignment_id = %s",
            (newest, name),
        )

    body = client.get("/api/v1/schedule-runs/latest").json()

    assert body["status"] == "computed"
    assert body["run_id"] == "run_new"
    assert body["configuration"] == "A"
    assert body["yield_source"] == "elevation_proxy"
    assert body["solver_status"] == "optimal"
    assert body["objective"] == 4.25
    assert body["simulated_decisions"] == 2
    assert body["measured_decisions"] == 1
    assert body["baseline_gain"] == {
        "status": "not_measured",
        "reason": BASELINE_GAIN_REASON,
    }


def test_no_run_yet_is_not_found(client: TestClient, rollback: Any) -> None:
    if rollback.execute("select count(*) from schedule_runs").fetchone()[0]:
        pytest.skip("this database already holds schedule runs")

    response = client.get("/api/v1/schedule-runs/latest")

    assert response.status_code == 404
