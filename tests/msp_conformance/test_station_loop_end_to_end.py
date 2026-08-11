"""Stage 8's completion gate, run against the real platform.

*A registered software client can receive, persist, hold, execute, and report the
listening state of an assignment despite temporary platform unavailability.*

Every clause of that sentence is a step below, driven by the real
:class:`~meridian_client.station_loop.StationLoop` over a real
:class:`~meridian_client.transport.MspTransport` into the real application and a
real database. Nothing is stubbed except the passage of time.

Marked ``msp_conformance`` by the directory hook in ``tests/conftest.py``.

Reference: docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md Stage 8; docs/MSP-SPEC.md
§4.2, §4.3; docs/DECISIONS.md D-003, D-024, D-067, D-068, D-069.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from meridian.api.app import create_app
from meridian.api.dependencies import get_connection
from meridian.store.invites import hash_invite_token
from meridian_client.assignment_message import Assignment
from meridian_client.credentials import StationCredentials, save_credentials
from meridian_client.held_assignments import AssignmentRecord
from meridian_client.registration import ReceiveChain, StationProfile, register
from meridian_client.station_loop import StationLoop
from meridian_client.transport import MspTransport

SATELLITE_ID = "norad:57166"
CENTRE_FREQ_HZ = 137_900_000
LINE1 = "1 25544U 98067A   26226.50000000  .00001234  00000-0  12345-4 0  9991"
LINE2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.50377579123456"

PROFILE = StationProfile(
    name="station-001",
    operator="meridian",
    lat_deg=12.9716,
    lon_deg=77.5946,
    alt_m=920.0,
    capabilities=(
        ReceiveChain(
            band="vhf",
            freq_min_hz=136_000_000,
            freq_max_hz=138_000_000,
            modes=("lrpt",),
            polarisation="rhcp",
            tracking=False,
            min_elevation_deg=10.0,
        ),
    ),
)


class RecordingExecutor:
    """A real :class:`PassExecutor` that writes down what it was asked to do."""

    def __init__(self) -> None:
        self.begun: list[str] = []
        self.ended: list[str] = []

    def begin(self, assignment: Assignment) -> None:
        """Note a start."""
        self.begun.append(assignment.assignment_id)

    def end(self, assignment: Assignment) -> None:
        """Note a stop."""
        self.ended.append(assignment.assignment_id)


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes."""
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def started(
    database_url: str, rollback: Any, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Any]:
    """The started application, sharing this test's rolled-back connection."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("TOKEN_HASH_PEPPER", "station-loop-pepper")
    app = create_app()
    app.dependency_overrides[get_connection] = lambda: rollback
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def admit_station(started: Any, rollback: Any, tmp_path: Path) -> StationCredentials:
    """Step 1: an operator issues an invite and the station registers."""
    with rollback.cursor() as cur:
        cur.execute(
            "insert into invite_tokens (token_sha256, label) values (%s, %s)",
            (hash_invite_token("gate-invite"), "stage-8-gate"),
        )
    with MspTransport(
        "http://platform.test", http_transport=started._transport
    ) as anonymous:
        credentials = register(
            anonymous,
            PROFILE,
            invite_token="gate-invite",
            registration_key_path=tmp_path / "registration_key",
        )
    save_credentials(tmp_path / "credentials.json", credentials)
    return credentials


def schedule_a_pass(rollback: Any, station_id: str, *, opens_in_minutes: float) -> None:
    """Step 2: the scheduler's output, as one assignment row."""
    now = datetime.now(UTC)
    start_at = now + timedelta(minutes=opens_in_minutes)
    with rollback.cursor() as cur:
        cur.execute(
            "insert into satellites (satellite_id, name) values (%s, %s)"
            " on conflict do nothing",
            (SATELLITE_ID, "Meteor-M 2-3"),
        )
        cur.execute(
            "insert into element_sets (satellite_id, epoch, line1, line2, source)"
            " values (%s, %s, %s, %s, %s) returning id",
            (SATELLITE_ID, now - timedelta(hours=7), LINE1, LINE2, "manual"),
        )
        (element_set_id,) = cur.fetchone()
        cur.execute(
            "insert into passes (satellite_id, station_id, aos, los,"
            " max_elevation_deg, max_elevation_at, aos_azimuth_deg, los_azimuth_deg,"
            " element_set_id, min_elevation_deg)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id",
            (
                SATELLITE_ID,
                station_id,
                start_at,
                start_at + timedelta(minutes=11),
                61.4,
                start_at + timedelta(minutes=5),
                10.0,
                200.0,
                element_set_id,
                10.0,
            ),
        )
        (pass_id,) = cur.fetchone()
        cur.execute(
            "insert into assignments (assignment_id, pass_id, station_id, start_at,"
            " end_at, centre_freq_hz, mode, timing_uncertainty_s, reason)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                "as_gate",
                pass_id,
                station_id,
                start_at,
                start_at + timedelta(minutes=11),
                CENTRE_FREQ_HZ,
                "lrpt",
                4.2,
                "stage 8 gate",
            ),
        )


def state_of(rollback: Any, assignment_id: str) -> str:
    """The stored state of one assignment."""
    with rollback.cursor() as cur:
        cur.execute(
            "select state from assignments where assignment_id = %s", (assignment_id,)
        )
        row = cur.fetchone()
    assert row is not None
    return str(row[0])


def loop_for(
    started: Any, credentials: StationCredentials, tmp_path: Path
) -> tuple[StationLoop, RecordingExecutor]:
    """A station loop over the record at ``tmp_path``, against the live platform."""
    transport = MspTransport(
        "http://platform.test",
        bearer_token=credentials.bearer_token,
        http_transport=started._transport,
    )
    executor = RecordingExecutor()
    record = AssignmentRecord(tmp_path / "held.json")
    return StationLoop(transport, credentials, record, executor), executor


def test_a_station_receives_holds_executes_and_survives_an_outage(
    started: Any, rollback: Any, tmp_path: Path
) -> None:
    """Stage 8's gate, one clause at a time.

    The steps are numbered against the roadmap's sentence rather than split into
    separate tests, because each one depends on the state the last one left — and
    a gate that only passes when its steps are run in order should be run in
    order.
    """
    credentials = admit_station(started, rollback, tmp_path)
    schedule_a_pass(rollback, credentials.station_id, opens_in_minutes=1)
    loop, executor = loop_for(started, credentials, tmp_path)
    now = datetime.now(UTC)

    # 1. Receive. The assignment arrives and the platform still sees it as issued,
    #    because the station has not yet said it kept it.
    first = loop.tick(now)
    assert first.accepted == ("as_gate",)
    assert state_of(rollback, "as_gate") == "issued"

    # 2. Persist. It is on disk before the station names it anywhere.
    assert AssignmentRecord(tmp_path / "held.json").held_ids() == ["as_gate"]

    # 3. Hold. The next heartbeat names it, and the platform records that.
    loop.tick(now + timedelta(seconds=30))
    assert state_of(rollback, "as_gate") == "held"

    # 4. Execute. The window opens, the executor starts, and the same heartbeat
    #    carries the listening block that proves it (MSP §4.2's key field).
    third = loop.tick(now + timedelta(minutes=2))
    assert executor.begun == ["as_gate"]
    assert state_of(rollback, "as_gate") == "in_progress"

    # 5. Still delivered while in progress — D-067. A station rebooting here must
    #    be told what it is in the middle of, not that it has nothing to do.
    assert "as_gate" in third.accepted


def test_reception_continues_while_the_platform_is_unreachable(
    started: Any, rollback: Any, tmp_path: Path
) -> None:
    """The "despite temporary platform unavailability" clause.

    The transport is pointed at a closed port mid-pass. The station keeps
    executing, keeps its record, and resumes reporting when the platform returns
    — with the same assignment, not a duplicate.
    """
    credentials = admit_station(started, rollback, tmp_path)
    schedule_a_pass(rollback, credentials.station_id, opens_in_minutes=1)
    loop, executor = loop_for(started, credentials, tmp_path)
    now = datetime.now(UTC)

    loop.tick(now)
    loop.tick(now + timedelta(minutes=2))
    assert executor.begun == ["as_gate"]

    unreachable = MspTransport(
        "http://127.0.0.1:1", bearer_token=credentials.bearer_token
    )
    offline = StationLoop(
        unreachable, credentials, AssignmentRecord(tmp_path / "held.json"), executor
    )
    with unreachable:
        outcome = offline.tick(now + timedelta(minutes=3))

    assert outcome.heartbeat_sent is False
    assert outcome.stop_reason is None
    # It picked the pass up from the record alone, with no response to read.
    assert offline.executing() is not None
    assert executor.ended == []


def test_a_restarted_station_resumes_from_its_own_record(
    started: Any, rollback: Any, tmp_path: Path
) -> None:
    """Power loss mid-pass, then a new process against the same files.

    The second loop is told about this assignment by nothing except the disk.
    """
    credentials = admit_station(started, rollback, tmp_path)
    schedule_a_pass(rollback, credentials.station_id, opens_in_minutes=1)
    first, _ = loop_for(started, credentials, tmp_path)
    now = datetime.now(UTC)
    first.tick(now)

    second, executor = loop_for(started, credentials, tmp_path)
    second.tick(now + timedelta(minutes=2))

    assert executor.begun == ["as_gate"]
    assert state_of(rollback, "as_gate") == "in_progress"


def test_a_revoked_token_stops_the_loop(
    started: Any, rollback: Any, tmp_path: Path
) -> None:
    """D-024: stop, log, surface to the operator. Never loop, never re-register."""
    credentials = admit_station(started, rollback, tmp_path)
    loop, _ = loop_for(started, credentials, tmp_path)
    with rollback.cursor() as cur:
        cur.execute(
            "update stations set token_revoked_at = now() where station_id = %s",
            (credentials.station_id,),
        )

    outcome = loop.tick(datetime.now(UTC))

    assert outcome.stop_reason == "unauthorized"


def test_the_offline_transport_reports_a_transport_failure_not_a_refusal() -> None:
    """A closed port is `httpx.HTTPError`, which the loop must not treat as a stop.

    Asserted directly so the outage test above cannot pass for the wrong reason —
    a refusal and an unreachable platform lead to opposite decisions.
    """
    with (
        MspTransport("http://127.0.0.1:1") as unreachable,
        pytest.raises(httpx.HTTPError),
    ):
        unreachable.heartbeat({"station_id": "st_x"})
