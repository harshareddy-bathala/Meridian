"""The simulated and file-replay receivers, and the instants they report.

Real files in a temporary directory and a clock that moves only when a test says
so. Neither receiver hears the sky, and both must say so, because that flag is
what keeps their recordings off a station measuring the real one (D-125).

Marked as a unit test by living in ``tests/unit``.

Reference: docs/DECISIONS.md D-056, D-122, D-123, D-125.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from meridian.registry.doppler_tolerance import (
    doppler_tolerance_hz as platform_tolerance_hz,
)
from meridian_client.reception.doppler_tolerance import doppler_tolerance_hz
from meridian_client.reception.protocols import (
    CapturePlan,
    CaptureRefusedError,
    Receiver,
    StationClocks,
    first_sample_at,
)
from meridian_client.reception.synthetic_receivers import (
    SIMULATED_RECORDING_NAME,
    FileReplayReceiver,
    RecordingSource,
    SimulatedReceiver,
)

OPENS_AT = datetime(2026, 8, 14, 9, 41, 0, tzinfo=UTC)
METEOR_HZ = 137_900_000


class FakeWall:
    """A wall clock that moves only when told to."""

    def __init__(self) -> None:
        self.now = OPENS_AT

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def wall() -> FakeWall:
    return FakeWall()


def plan(tmp_path: Path, assignment_id: str = "as_44b2") -> CapturePlan:
    return CapturePlan(
        assignment_id=assignment_id,
        satellite_id="norad:57166",
        centre_freq_hz=METEOR_HZ,
        mode="lrpt",
        opens_at=OPENS_AT,
        closes_at=OPENS_AT + timedelta(minutes=12),
        folder=tmp_path / "captures" / assignment_id,
    )


def source(path: Path, **overrides: object) -> RecordingSource:
    fields: dict[str, object] = {
        "path": path,
        "sample_rate_hz": 1_000,
        "sample_format": "cf32",
        "centre_freq_hz": METEOR_HZ,
        "gain_db": 32.8,
    }
    fields.update(overrides)
    return RecordingSource(**fields)  # type: ignore[arg-type]


def recording_file(tmp_path: Path, samples: int, bytes_per_sample: int = 8) -> Path:
    path = tmp_path / "operator" / "pass.cf32"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * samples * bytes_per_sample)
    return path


# --- the first sample's instant -----------------------------------------------


def test_the_first_sample_is_counted_back_from_the_stop() -> None:
    """D-122: never stamped at launch, whose latency no uncertainty describes."""
    stopped_at = OPENS_AT + timedelta(minutes=10)

    assert first_sample_at(stopped_at, 600_000, 1_000) == OPENS_AT


# --- SimulatedReceiver --------------------------------------------------------


def test_the_simulated_receiver_does_not_hear_the_sky(wall: FakeWall) -> None:
    receiver: Receiver = SimulatedReceiver(StationClocks(wall=wall))

    assert receiver.hears_the_sky is False


def test_a_simulated_capture_records_as_long_as_the_clock_ran(
    tmp_path: Path, wall: FakeWall
) -> None:
    receiver = SimulatedReceiver(StationClocks(wall=wall), sample_rate_hz=100)

    tuning = receiver.start(plan(tmp_path))
    assert receiver.alive()
    wall.advance(90)
    recording = receiver.stop()

    assert tuning.centre_freq_hz == METEOR_HZ
    assert tuning.gain_db is None
    assert tuning.recording_path == tmp_path / "captures" / "as_44b2" / "recording.u8"
    assert tuning.sample_format == "u8"
    assert not receiver.alive()
    assert recording.sample_count == 9_000
    assert (
        recording.path == tmp_path / "captures" / "as_44b2" / SIMULATED_RECORDING_NAME
    )
    assert recording.path.stat().st_size == 9_000 * 2
    assert recording.first_sample_at == OPENS_AT
    assert recording.stopped_at == OPENS_AT + timedelta(seconds=90)
    assert recording.interrupted is False
    assert recording.notes == "simulated receiver, no antenna"


def test_two_simulated_captures_of_one_length_write_identical_files(
    tmp_path: Path, wall: FakeWall
) -> None:
    """Deterministic, so a simulated reception can be replayed byte for byte."""
    receiver = SimulatedReceiver(StationClocks(wall=wall), sample_rate_hz=100)

    receiver.start(plan(tmp_path / "one"))
    wall.advance(30)
    first = receiver.stop().path.read_bytes()
    receiver.start(plan(tmp_path / "two"))
    wall.advance(30)
    second = receiver.stop().path.read_bytes()

    assert first == second


def test_a_second_simulated_capture_is_refused_while_one_runs(
    tmp_path: Path, wall: FakeWall
) -> None:
    receiver = SimulatedReceiver(StationClocks(wall=wall))
    receiver.start(plan(tmp_path))

    with pytest.raises(CaptureRefusedError, match="one at a time"):
        receiver.start(plan(tmp_path, "as_other"))


def test_stopping_a_simulated_receiver_that_never_started_is_a_bug(
    wall: FakeWall,
) -> None:
    with pytest.raises(RuntimeError):
        SimulatedReceiver(StationClocks(wall=wall)).stop()


# --- FileReplayReceiver -------------------------------------------------------


def test_the_replay_receiver_does_not_hear_the_sky(wall: FakeWall) -> None:
    receiver: Receiver = FileReplayReceiver({}, StationClocks(wall=wall))

    assert receiver.hears_the_sky is False


def test_a_replay_references_the_file_in_place_on_the_capture_timeline(
    tmp_path: Path, wall: FakeWall
) -> None:
    """D-123: never copied. D-125: the result says it was a replay, and of what."""
    path = recording_file(tmp_path, samples=4_000)
    receiver = FileReplayReceiver({"as_44b2": source(path)}, StationClocks(wall=wall))

    tuning = receiver.start(plan(tmp_path))
    wall.advance(600)
    recording = receiver.stop()

    assert tuning.gain_db == 32.8
    assert tuning.recording_path == path
    assert recording.path == path
    assert not (tmp_path / "captures" / "as_44b2").exists()
    assert recording.sample_count == 4_000
    assert recording.first_sample_at == OPENS_AT
    assert recording.stopped_at == OPENS_AT + timedelta(seconds=4)
    assert (
        recording.notes == "replay of pass.cf32, first sample placed at capture start"
    )
    assert str(tmp_path) not in (recording.notes or "")


def test_a_replay_within_the_doppler_tolerance_is_accepted(
    tmp_path: Path, wall: FakeWall
) -> None:
    """A recording tuned a little off nominal is still the same transmitter."""
    path = recording_file(tmp_path, samples=10)
    offset = doppler_tolerance_hz(METEOR_HZ)
    receiver = FileReplayReceiver(
        {"as_44b2": source(path, centre_freq_hz=METEOR_HZ + offset)},
        StationClocks(wall=wall),
    )

    assert receiver.start(plan(tmp_path)).centre_freq_hz == METEOR_HZ + offset


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        ("no_source", "no recording to replay"),
        ("missing_file", "does not exist"),
        ("empty_file", "not a whole number"),
        ("partial_sample", "not a whole number"),
        ("other_satellite", "outside"),
    ],
)
def test_a_replay_that_cannot_stand_for_the_pass_is_refused(
    tmp_path: Path, wall: FakeWall, setup: str, message: str
) -> None:
    """Refused at start, so the pass is `not_attempted` rather than a false report.

    ``other_satellite`` is NOAA at 137.9125 MHz, 12.5 kHz from Meteor — the
    closest pair D-056 had to keep apart.
    """
    path = tmp_path / "operator" / "pass.cf32"
    path.parent.mkdir(parents=True)
    sources = {"as_44b2": source(path)}
    if setup == "no_source":
        sources = {}
    elif setup == "empty_file":
        path.write_bytes(b"")
    elif setup == "partial_sample":
        path.write_bytes(b"\0" * 12)
    elif setup == "other_satellite":
        path.write_bytes(b"\0" * 16)
        sources = {"as_44b2": source(path, centre_freq_hz=137_912_500)}
    receiver = FileReplayReceiver(sources, StationClocks(wall=wall))

    with pytest.raises(CaptureRefusedError, match=message):
        receiver.start(plan(tmp_path))
    assert not receiver.alive()


def test_a_recording_source_names_a_format_this_layer_reads(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="sample_format"):
        source(tmp_path / "pass.wav", sample_format="wav")


# --- agreement with the platform ----------------------------------------------


@pytest.mark.parametrize(
    "centre_freq_hz",
    [137_100_000, 137_900_000, 145_800_000, 437_500_000, 2_245_000_000],
)
def test_the_client_and_platform_agree_on_the_doppler_tolerance(
    centre_freq_hz: int,
) -> None:
    """Stated twice because the client cannot import the platform; kept equal here."""
    assert doppler_tolerance_hz(centre_freq_hz) == platform_tolerance_hz(centre_freq_hz)
