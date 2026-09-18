"""Stage 13's completion gate, through the real platform and a real database.

*The client executes an assignment through a simulated or recorded-data pipeline
and produces a valid observation, without knowing anything about PostgreSQL.*

A station registers as simulated, the scheduler's output is one assignment row,
and the real :class:`~meridian_client.station_loop.StationLoop` drives the real
:class:`~meridian_client.reception.reception_executor.ReceptionExecutor` — a
recording replayed from disk, the subprocess decoder running a decoder program —
over a real :class:`~meridian_client.transport.MspTransport` into the real
application. The platform stores a ``decoded`` MSP 0.3 observation with its
decoder statistics, labelled simulated.

The client side of the same gate, with no database at all, is
``tests/unit/test_reception_gate.py``.

Marked ``msp_conformance`` by the directory hook in ``tests/conftest.py``.

Reference: docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md Stage 13; docs/MSP-SPEC.md
§4.4, §5; docs/DECISIONS.md D-117, D-119, D-120 to D-126.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from meridian.api.app import create_app
from meridian.api.dependencies import get_connection
from meridian.store.invites import hash_invite_token
from meridian_client.credentials import StationCredentials
from meridian_client.held_assignments import AssignmentRecord
from meridian_client.observation_queue import ObservationQueue
from meridian_client.reception.capture_folder import CaptureFolders
from meridian_client.reception.disk_guard import DiskGuard
from meridian_client.reception.null_rotator import NullRotator
from meridian_client.reception.protocols import StationClocks
from meridian_client.reception.reception_executor import (
    ReceptionExecutor,
    ReceptionSetup,
)
from meridian_client.reception.subprocess_decoder import (
    DecoderCommand,
    SubprocessDecoder,
)
from meridian_client.reception.synthetic_receivers import (
    FileReplayReceiver,
    RecordingSource,
)
from meridian_client.registration import ReceiveChain, StationProfile, register
from meridian_client.station_loop import StationLoop
from meridian_client.transport import MspTransport

SATELLITE_ID = "norad:57166"
CENTRE_FREQ_HZ = 137_900_000
LINE1 = "1 25544U 98067A   26226.50000000  .00001234  00000-0  12345-4 0  9991"
LINE2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.50377579123456"
FAKE_DECODER = (
    Path(__file__).parents[1] / "unit" / "reception_fakes" / "fake_decoder.py"
)

PROFILE = StationProfile(
    name="station-sim-013",
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
    simulated=True,
    simulator_run_id="stage-13-gate",
    seed=1313,
)


class FollowTheTick:
    """The station's wall clock, set to each tick's instant.

    The test moves time faster than it passes; the recording's timestamps have to
    move with the loop's, or the capture would span a few real milliseconds.
    """

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def started(
    database_url: str, rollback: Any, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Any]:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("TOKEN_HASH_PEPPER", "reception-gate-pepper")
    app = create_app()
    app.dependency_overrides[get_connection] = lambda: rollback
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def admit_simulated_station(
    started: Any, rollback: Any, tmp_path: Path
) -> StationCredentials:
    with rollback.cursor() as cur:
        cur.execute(
            "insert into invite_tokens (token_sha256, label) values (%s, %s)",
            (hash_invite_token("reception-gate-invite"), "stage-13-gate"),
        )
    with MspTransport(
        "http://platform.test", http_transport=started._transport
    ) as anonymous:
        return register(
            anonymous,
            PROFILE,
            invite_token="reception-gate-invite",
            registration_key_path=tmp_path / "registration_key",
        )


def schedule_a_pass(rollback: Any, station_id: str, start_at: datetime) -> None:
    """The scheduler's output for one pass, as its assignment row."""
    with rollback.cursor() as cur:
        cur.execute(
            "insert into satellites (satellite_id, name) values (%s, %s)"
            " on conflict do nothing",
            (SATELLITE_ID, "Meteor-M 2-3"),
        )
        cur.execute(
            "insert into element_sets (satellite_id, epoch, line1, line2, source)"
            " values (%s, %s, %s, %s, %s) returning id",
            (SATELLITE_ID, start_at - timedelta(hours=7), LINE1, LINE2, "manual"),
        )
        (element_set_id,) = cur.fetchone()
        cur.execute(
            "insert into passes (satellite_id, station_id, aos, los,"
            " max_elevation_deg, max_elevation_at, aos_azimuth_deg, los_azimuth_deg,"
            " element_set_id, min_elevation_deg)"
            " values (%s, %s, %s, %s, 61.4, %s, 10.0, 200.0, %s, 10.0) returning id",
            (
                SATELLITE_ID,
                station_id,
                start_at,
                start_at + timedelta(minutes=11),
                start_at + timedelta(minutes=5),
                element_set_id,
            ),
        )
        (pass_id,) = cur.fetchone()
        cur.execute(
            "insert into assignments (assignment_id, pass_id, station_id, start_at,"
            " end_at, centre_freq_hz, mode, timing_uncertainty_s, reason)"
            " values ('as_reception', %s, %s, %s, %s, %s, 'lrpt', 4.2, 'stage 13')",
            (
                pass_id,
                station_id,
                start_at,
                start_at + timedelta(minutes=11),
                CENTRE_FREQ_HZ,
            ),
        )


def reception_setup(tmp_path: Path, clocks: StationClocks) -> ReceptionSetup:
    """A replayed recording and a decoder program that reports 412 frames."""
    recording = tmp_path / "operator" / "pass.cf32"
    recording.parent.mkdir()
    recording.write_bytes(b"\0" * 8 * 100 * 700)  # 700 s at 100 Hz, cf32
    report = tmp_path / "operator" / "report.json"
    report.write_text(
        json.dumps(
            {
                "format": 1,
                "decoder": "fake-satdump",
                "decoder_version": "0.0.1",
                "frames_decoded": 412,
                "frames_failed": 37,
                "first_frame_offset_s": 35.0,
                "snr": [{"offset_s": 330.0, "snr_db": 11.4}],
                "noise_floor_dbfs": -52.3,
            }
        )
    )
    command = DecoderCommand(
        (sys.executable, str(FAKE_DECODER), "report", "{report_path}", str(report)),
        timeout_s=60.0,
    )
    source = RecordingSource(recording, 100, "cf32", CENTRE_FREQ_HZ, 32.8)
    return ReceptionSetup(
        receiver=FileReplayReceiver({"as_reception": source}, clocks),
        decoder=SubprocessDecoder({"lrpt": command}, clocks),
        rotator=NullRotator(),
        folders=CaptureFolders(tmp_path / "state" / "captures"),
        disk=DiskGuard(bytes_per_second=800, reserve_bytes=0),
    )


def stored_observation(rollback: Any) -> tuple[Any, ...] | None:
    with rollback.cursor() as cur:
        cur.execute(
            "select outcome, simulated, decoder, frames_decoded, frames_failed,"
            " receiver_gain_db, noise_floor_dbfs, first_detection_at"
            " from observations_current where assignment_id = 'as_reception'"
        )
        return cur.fetchone()


def test_a_simulated_station_reports_a_decoded_pass_through_the_platform(
    started: Any, rollback: Any, tmp_path: Path
) -> None:
    credentials = admit_simulated_station(started, rollback, tmp_path)
    now = datetime.now(UTC)
    start_at = now + timedelta(minutes=1)
    schedule_a_pass(rollback, credentials.station_id, start_at)
    wall = FollowTheTick(now)
    clocks = StationClocks(wall=wall)
    executor = ReceptionExecutor(
        reception_setup(tmp_path, clocks), clocks, simulated_station=True
    )
    loop = StationLoop(
        MspTransport(
            "http://platform.test",
            bearer_token=credentials.bearer_token,
            http_transport=started._transport,
        ),
        credentials,
        AssignmentRecord(tmp_path / "state" / "held.json"),
        executor,
        ObservationQueue(tmp_path / "state" / "outbox"),
    )

    def tick_at(instant: datetime) -> Any:
        wall.now = instant
        return loop.tick(instant)

    assert tick_at(now).accepted == ("as_reception",)
    assert tick_at(start_at - timedelta(seconds=3)).began == "as_reception"
    assert tick_at(start_at + timedelta(minutes=11, seconds=5)).ended == "as_reception"

    instant = start_at + timedelta(minutes=11, seconds=5)
    deadline = time.monotonic() + 30
    submitted: tuple[str, ...] = ()
    while not submitted:
        assert time.monotonic() < deadline, "the observation was never submitted"
        instant += timedelta(seconds=30)
        submitted = tick_at(instant).submitted
        time.sleep(0.02)

    assert submitted == ("as_reception",)
    row = stored_observation(rollback)
    assert row is not None
    outcome, simulated, decoder, decoded, failed, gain, floor, detected_at = row
    assert (outcome, simulated) == ("decoded", True)
    assert (decoder, decoded, failed) == ("fake-satdump", 412, 37)
    assert (gain, floor) == (32.8, -52.3)
    assert detected_at == start_at - timedelta(seconds=3) + timedelta(seconds=35)
