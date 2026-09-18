"""Stage 13's completion gate, with no platform and no database.

*The client executes an assignment through a simulated or recorded-data pipeline
and produces a valid observation, without knowing anything about PostgreSQL.*

The real :class:`~meridian_client.station_loop.StationLoop` drives the real
:class:`~meridian_client.reception.reception_executor.ReceptionExecutor`: a
recording replayed from disk, the real subprocess decoder running a decoder
program, real capture folders, the real upload queue. Only the platform is
scripted, and only time is faked. Any attempt to open a database connection fails
the test, and the observation that comes out is validated against the platform's
own request model.

The same gate against the real platform and database is
``tests/msp_conformance/test_reception_end_to_end.py``.

Marked as a unit test by living in ``tests/unit``: child processes and temporary
files are not infrastructure.

Reference: docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md Stage 13; docs/DECISIONS.md
D-120 to D-126.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest

from meridian.api.models.observation import ObservationRequestBody
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
from meridian_client.station_loop import StationLoop

NOW = datetime(2026, 8, 14, 9, 30, 0, tzinfo=UTC)
START_AT = NOW + timedelta(minutes=1)
END_AT = START_AT + timedelta(minutes=11)
FAKE_DECODER = Path(__file__).parent / "reception_fakes" / "fake_decoder.py"
CREDENTIALS = StationCredentials("st_7fa3c1", "a-token", "a-key", 30)
LINE1 = "1 57166U 23091A   26223.50000000  .00000100  00000-0  50000-4 0  9990"
LINE2 = "2 57166  98.7041 210.4322 0002726  80.4113 279.7297 14.22000000160126"

ASSIGNMENT = {
    "assignment_id": "as_gate",
    "satellite_id": "norad:57166",
    "start_at": START_AT.isoformat(),
    "end_at": END_AT.isoformat(),
    "centre_freq_hz": 137_900_000,
    "mode": "lrpt",
    "expected_max_elevation_deg": 61.4,
    "predicted_yield": None,
    "element_set": {"epoch": NOW.isoformat(), "line1": LINE1, "line2": LINE2},
    "timing_uncertainty_s": 4.2,
    "priority": 1.0,
}


class ScriptedPlatform:
    """Delivers one assignment, then acknowledges whatever it is sent."""

    def __init__(self) -> None:
        self.heartbeats: list[dict[str, Any]] = []
        self.observations: list[dict[str, Any]] = []

    def heartbeat(self, body: dict[str, Any]) -> dict[str, Any]:
        self.heartbeats.append(body)
        assignments = [ASSIGNMENT] if len(self.heartbeats) == 1 else []
        return {"assignments": assignments, "server_time": NOW.isoformat()}

    def observations_(self, body: dict[str, Any]) -> dict[str, Any]:
        self.observations.append(body)
        return {
            "observation_id": "ob_05601bd09768",
            "assignment_id": body["assignment_id"],
            "superseded": False,
        }


class FakeWall:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def no_database(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("the station client opened a database connection")


def build_station(
    tmp_path: Path, wall: FakeWall
) -> tuple[StationLoop, ScriptedPlatform]:
    """One simulated station: replayed recording, decoder program, real folders."""
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
                "snr": [
                    {"offset_s": 30.0, "snr_db": 1.2},
                    {"offset_s": 330.0, "snr_db": 11.4},
                ],
                "noise_floor_dbfs": -52.3,
            }
        )
    )
    clocks = StationClocks(wall=wall)
    command = DecoderCommand(
        (sys.executable, str(FAKE_DECODER), "report", "{report_path}", str(report)),
        timeout_s=60.0,
    )
    setup = ReceptionSetup(
        receiver=FileReplayReceiver(
            {"as_gate": RecordingSource(recording, 100, "cf32", 137_900_000, 32.8)},
            clocks,
        ),
        decoder=SubprocessDecoder({"lrpt": command}, clocks),
        rotator=NullRotator(),
        folders=CaptureFolders(tmp_path / "state" / "captures"),
        disk=DiskGuard(bytes_per_second=800, reserve_bytes=0),
    )
    executor = ReceptionExecutor(setup, clocks, simulated_station=True)
    platform = ScriptedPlatform()
    transport = type(
        "Transport",
        (),
        {"heartbeat": platform.heartbeat, "observations": platform.observations_},
    )()
    loop = StationLoop(
        transport,  # type: ignore[arg-type]
        CREDENTIALS,
        AssignmentRecord(tmp_path / "state" / "held.json"),
        executor,
        ObservationQueue(tmp_path / "state" / "outbox"),
    )
    return loop, platform


def assert_a_decoded_replay(body: dict[str, Any]) -> None:
    """The submitted body: valid to the platform, and saying what happened."""
    ObservationRequestBody.model_validate(body)
    capture_started_at = START_AT - timedelta(seconds=3)
    assert body["outcome"] == "decoded"
    assert body["started_at"] == capture_started_at.isoformat().replace("+00:00", "Z")
    assert body["signal"]["first_detection_at"] == (
        (capture_started_at + timedelta(seconds=35)).isoformat().replace("+00:00", "Z")
    )
    assert body["signal"]["receiver_gain_db"] == 32.8
    assert body["decode"] == {
        "decoder": "fake-satdump",
        "decoder_version": "0.0.1",
        "frames_decoded": 412,
        "frames_failed": 37,
    }
    assert "replay of pass.cf32" in body["client_notes"]


def test_a_station_turns_an_assignment_into_a_valid_observation_with_no_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(psycopg, "connect", no_database)
    wall = FakeWall()
    loop, platform = build_station(tmp_path, wall)

    def tick_at(instant: datetime) -> Any:
        wall.now = instant
        return loop.tick(instant)

    # Held, then begun at the widened opening: listening to what was tuned.
    assert tick_at(NOW).accepted == ("as_gate",)
    began = tick_at(START_AT - timedelta(seconds=3))
    assert began.began == "as_gate"
    assert platform.heartbeats[-1]["state"] == "listening"
    assert platform.heartbeats[-1]["listening"]["centre_freq_hz"] == 137_900_000

    # Ended at the widened close; decoding, and still named while it decodes.
    ended = tick_at(END_AT + timedelta(seconds=5))
    assert ended.ended == "as_gate"
    assert platform.heartbeats[-1]["state"] == "processing"
    assert platform.heartbeats[-1]["held_assignments"] == ["as_gate"]

    # Ticks go on until the decoder program has finished and the result is sent.
    instant = END_AT + timedelta(seconds=5)
    deadline = time.monotonic() + 30
    while not platform.observations:
        assert time.monotonic() < deadline, "the observation was never submitted"
        instant += timedelta(seconds=30)
        tick_at(instant)
        time.sleep(0.02)
    tick_at(instant + timedelta(seconds=30))

    (body,) = platform.observations
    assert_a_decoded_replay(body)
    assert platform.heartbeats[-1]["held_assignments"] == []
    assert platform.heartbeats[-1]["state"] == "idle"
    assert ObservationQueue(tmp_path / "state" / "outbox").pending() == ()
    assert (tmp_path / "operator" / "pass.cf32").exists()
