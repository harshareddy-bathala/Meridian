"""The reception executor, driven the way the station loop drives it.

Real capture folders in a temporary directory, the real simulated and replay
receivers on a fake clock, and a scripted decoder that writes a real report file
— so manifests, hand-over and restart are exercised on disk, while no test waits
for a subprocess. The subprocess decoder has its own tests, and the gate tests
put the two together.

Every result is built into a body with the client's function and validated with
the platform's request model.

Marked as a unit test by living in ``tests/unit``.

Reference: docs/DECISIONS.md D-073, D-121 to D-126.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from meridian.api.models.observation import ObservationRequestBody
from meridian_client.assignment_message import Assignment, ElementSet
from meridian_client.execution import PassExecutor
from meridian_client.observation_message import (
    ObservationResult,
    build_observation_body,
)
from meridian_client.reception.capture_folder import CaptureFolders
from meridian_client.reception.decode_report import DecodeFailure, DecodeReport
from meridian_client.reception.disk_guard import DiskGuard
from meridian_client.reception.protocols import (
    CapturePlan,
    DecodeJob,
    StationClocks,
    Tuning,
)
from meridian_client.reception.reception_executor import (
    NEVER_BEGUN,
    RECORDING_CHANGED,
    ReceptionExecutor,
    ReceptionSetup,
)
from meridian_client.reception.subprocess_decoder import decode_paths
from meridian_client.reception.synthetic_receivers import (
    FileReplayReceiver,
    RecordingSource,
    SimulatedReceiver,
)

START_AT = datetime(2026, 8, 14, 9, 41, 18, tzinfo=UTC)
END_AT = START_AT + timedelta(minutes=11)
REPORT = {
    "format": 1,
    "decoder": "satdump",
    "decoder_version": "1.2.2",
    "frames_decoded": 412,
    "frames_failed": 37,
    "first_frame_offset_s": 35.0,
    "snr": [{"offset_s": 40.0, "snr_db": 11.4}],
    "noise_floor_dbfs": -52.3,
}

ASSIGNMENT = Assignment(
    assignment_id="as_44b2",
    satellite_id="norad:57166",
    start_at=START_AT,
    end_at=END_AT,
    centre_freq_hz=137_900_000,
    mode="lrpt",
    expected_max_elevation_deg=61.4,
    predicted_yield=None,
    element_set=ElementSet(
        epoch=datetime(2026, 8, 14, 2, 11, 0, tzinfo=UTC),
        line1="1 57166U 23091A   26223.50000000  .00000100  00000-0  50000-4 0  9990",
        line2="2 57166  98.7041 210.4322 0002726  80.4113 279.7297 14.22000000160126",
    ),
    timing_uncertainty_s=4.2,
    priority=1.0,
)


class FakeWall:
    def __init__(self) -> None:
        self.now = START_AT - timedelta(seconds=5)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class ScriptedRun:
    """A decode that finishes after a set number of polls, writing a real report."""

    def __init__(
        self, job: DecodeJob, report: object, failure: str | None, polls: int
    ) -> None:
        self._job, self._report, self._failure, self._polls = (
            job,
            report,
            failure,
            polls,
        )

    def poll(self) -> DecodeReport | DecodeFailure | None:
        if self._polls > 0:
            self._polls -= 1
            return None
        if self._failure is not None:
            return DecodeFailure(self._failure)
        decode_paths(self._job.folder).report.write_text(json.dumps(self._report))
        return DecodeReport("satdump", None, None, None, None, None, None)

    def cancel(self) -> None:
        self._polls = 0


class ScriptedDecoder:
    def __init__(
        self, report: object = REPORT, *, failure: str | None = None, polls: int = 1
    ) -> None:
        self.report, self.failure, self.polls = report, failure, polls
        self.jobs: list[DecodeJob] = []
        self.modes = {"lrpt"}

    def supports(self, mode: str) -> bool:
        return mode in self.modes

    def start(self, job: DecodeJob) -> ScriptedRun:
        self.jobs.append(job)
        return ScriptedRun(job, self.report, self.failure, self.polls)


class RecordingRotator:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def prepare(self, plan: CapturePlan) -> None:
        self.calls.append(f"prepare {plan.assignment_id}")

    def release(self) -> None:
        self.calls.append("release")


class MortalReceiver(SimulatedReceiver):
    """A simulated receiver that can be told it has died."""

    dead = False

    def alive(self) -> bool:
        return not self.dead and super().alive()


class SkyReceiver(SimulatedReceiver):
    hears_the_sky = True  # type: ignore[assignment]


@pytest.fixture
def wall() -> FakeWall:
    return FakeWall()


def setup_for(tmp_path: Path, wall: FakeWall, **changes: object) -> ReceptionSetup:
    base = ReceptionSetup(
        receiver=MortalReceiver(StationClocks(wall=wall)),
        decoder=ScriptedDecoder(),
        rotator=RecordingRotator(),
        folders=CaptureFolders(tmp_path / "captures"),
        disk=DiskGuard(bytes_per_second=2_000, reserve_bytes=0),
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def executor_for(setup: ReceptionSetup, wall: FakeWall) -> ReceptionExecutor:
    return ReceptionExecutor(setup, StationClocks(wall=wall), simulated_station=True)


def valid(results: tuple[ObservationResult, ...]) -> tuple[ObservationResult, ...]:
    for result in results:
        ObservationRequestBody.model_validate(
            build_observation_body(result, "st_7fa3c1")
        )
    return results


def work_a_pass(executor: ReceptionExecutor, wall: FakeWall) -> None:
    """Begin at the widened opening, end at the widened close."""
    executor.begin(ASSIGNMENT)
    wall.advance((END_AT - START_AT).total_seconds() + 10)
    executor.end(ASSIGNMENT)


def first_results(executor: ReceptionExecutor) -> tuple[ObservationResult, ...]:
    """Tick until something is handed over, and return just that."""
    for _ in range(10):
        results = executor.take_completed()
        if results:
            return valid(results)
    raise AssertionError("nothing was handed over")


def drain(executor: ReceptionExecutor, ticks: int = 5) -> tuple[ObservationResult, ...]:
    results: list[ObservationResult] = []
    for _ in range(ticks):
        results.extend(executor.take_completed())
    return valid(tuple(results))


# --- construction -------------------------------------------------------------


def test_a_synthetic_receiver_is_refused_for_a_station_that_is_not_simulated(
    tmp_path: Path, wall: FakeWall
) -> None:
    """D-125: a simulated result presented as measured, through the trusted path."""
    with pytest.raises(ValueError, match="does not hear the sky"):
        ReceptionExecutor(
            setup_for(tmp_path, wall), StationClocks(wall=wall), simulated_station=False
        )


def test_a_receiver_that_hears_the_sky_serves_a_real_station(
    tmp_path: Path, wall: FakeWall
) -> None:
    setup = setup_for(tmp_path, wall, receiver=SkyReceiver(StationClocks(wall=wall)))

    executor: PassExecutor = ReceptionExecutor(
        setup, StationClocks(wall=wall), simulated_station=False
    )

    assert executor.status(None).state == "idle"


def test_the_capture_window_is_widened_by_the_timing_uncertainty(
    tmp_path: Path, wall: FakeWall
) -> None:
    window = executor_for(setup_for(tmp_path, wall), wall).capture_window(ASSIGNMENT)

    assert window.opens_at == START_AT - timedelta(seconds=4.2)
    assert window.closes_at == END_AT + timedelta(seconds=4.2)


# --- a pass that works --------------------------------------------------------


def test_a_pass_is_captured_decoded_and_handed_over_once(
    tmp_path: Path, wall: FakeWall
) -> None:
    setup = setup_for(tmp_path, wall)
    executor = executor_for(setup, wall)

    executor.begin(ASSIGNMENT)
    listening = executor.status(ASSIGNMENT)
    wall.advance((END_AT - START_AT).total_seconds() + 10)
    executor.end(ASSIGNMENT)
    after_capture = executor.status(None)
    results = drain(executor)

    assert listening.state == "listening"
    assert listening.listening is not None
    assert listening.listening.centre_freq_hz == 137_900_000
    assert after_capture.state == "processing"
    assert after_capture.unfinished == ("as_44b2",)
    assert [one.outcome for one in results] == ["decoded"]
    assert results[0].signal is not None
    assert results[0].signal.first_detection_at == START_AT - timedelta(
        seconds=5
    ) + timedelta(seconds=35)
    assert (
        results[0].client_notes
        == "simulated receiver, no antenna; detected by first decoded frame"
    )
    assert executor.status(None).unfinished == ()
    manifest = setup.folders.read("as_44b2")
    assert manifest is not None
    assert manifest.phase == "handed_over"
    assert setup.rotator.calls == ["prepare as_44b2", "release"]  # type: ignore[attr-defined]


def test_work_stays_unfinished_until_the_tick_after_it_is_taken(
    tmp_path: Path, wall: FakeWall
) -> None:
    """D-121 and D-073: named in heartbeats until the queue write is behind it."""
    executor = executor_for(setup_for(tmp_path, wall), wall)
    work_a_pass(executor, wall)

    taken = first_results(executor)
    still_named = executor.status(None).unfinished
    executor.take_completed()

    assert len(taken) == 1
    assert still_named == ("as_44b2",)
    assert executor.status(None).unfinished == ()


def test_a_handed_over_recording_is_deleted_unless_kept(
    tmp_path: Path, wall: FakeWall
) -> None:
    executor = executor_for(setup_for(tmp_path, wall), wall)
    work_a_pass(executor, wall)
    drain(executor)

    assert not (tmp_path / "captures" / "as_44b2" / "recording.u8").exists()

    kept = executor_for(setup_for(tmp_path / "kept", wall, keep_recordings=True), wall)
    work_a_pass(kept, wall)
    drain(kept)

    assert (tmp_path / "kept" / "captures" / "as_44b2" / "recording.u8").exists()


def test_a_replayed_recording_is_never_deleted(tmp_path: Path, wall: FakeWall) -> None:
    """The operator's file is the operator's (D-123)."""
    source = tmp_path / "operator" / "pass.cf32"
    source.parent.mkdir()
    source.write_bytes(b"\0" * 8 * 700_000)
    receiver = FileReplayReceiver(
        {"as_44b2": RecordingSource(source, 1_000, "cf32", 137_900_000, 32.8)},
        StationClocks(wall=wall),
    )
    executor = executor_for(setup_for(tmp_path, wall, receiver=receiver), wall)

    work_a_pass(executor, wall)
    results = drain(executor)

    assert results[0].outcome == "decoded"
    assert "replay of pass.cf32" in (results[0].client_notes or "")
    assert source.exists()


# --- passes that do not work --------------------------------------------------


def test_a_mode_with_no_decoder_is_not_attempted_and_the_station_is_degraded(
    tmp_path: Path, wall: FakeWall
) -> None:
    decoder = ScriptedDecoder()
    decoder.modes = set()
    setup = setup_for(tmp_path, wall, decoder=decoder)
    executor = executor_for(setup, wall)

    executor.begin(ASSIGNMENT)
    status = executor.status(ASSIGNMENT)
    first = drain(executor, ticks=1)
    executor.end(ASSIGNMENT)
    later = drain(executor)

    assert status.state == "degraded"
    assert status.listening is None
    assert [one.outcome for one in first] == ["not_attempted"]
    assert first[0].client_notes == "no decoder is configured for lrpt"
    assert later == ()
    assert setup.rotator.calls == []  # type: ignore[attr-defined]


def test_a_disk_without_room_for_the_capture_is_not_attempted(
    tmp_path: Path, wall: FakeWall
) -> None:
    """D-123: a full disk mid-capture is worse than a pass never started."""
    guard = DiskGuard(
        bytes_per_second=2_000, reserve_bytes=0, free_bytes=lambda _: 1_000
    )
    executor = executor_for(setup_for(tmp_path, wall, disk=guard), wall)

    executor.begin(ASSIGNMENT)
    results = drain(executor, ticks=1)

    assert results[0].outcome == "not_attempted"
    assert "not enough disk" in (results[0].client_notes or "")


def test_a_receiver_that_refuses_releases_the_antenna(
    tmp_path: Path, wall: FakeWall
) -> None:
    receiver = FileReplayReceiver({}, StationClocks(wall=wall))
    setup = setup_for(tmp_path, wall, receiver=receiver)
    executor = executor_for(setup, wall)

    executor.begin(ASSIGNMENT)
    results = drain(executor, ticks=1)

    assert results[0].outcome == "not_attempted"
    assert setup.rotator.calls == ["prepare as_44b2", "release"]  # type: ignore[attr-defined]


class BrokenReceiver(SimulatedReceiver):
    def start(self, plan: CapturePlan) -> Tuning:
        raise PermissionError(
            f"cannot open the capture folder for {plan.assignment_id}"
        )


def test_a_receiver_that_fails_with_an_os_error_is_refused_not_raised(
    tmp_path: Path, wall: FakeWall
) -> None:
    """One pass the station cannot start must not stop the station loop."""
    setup = setup_for(tmp_path, wall, receiver=BrokenReceiver(StationClocks(wall=wall)))
    executor = executor_for(setup, wall)

    executor.begin(ASSIGNMENT)
    results = drain(executor, ticks=1)

    assert results[0].outcome == "not_attempted"
    assert results[0].client_notes == "cannot open the capture folder for as_44b2"
    assert setup.rotator.calls == ["prepare as_44b2", "release"]  # type: ignore[attr-defined]


def test_a_held_pass_never_begun_is_not_attempted_when_it_ends(
    tmp_path: Path, wall: FakeWall
) -> None:
    """D-121: the loop calls ``end`` for work whose window closed without ``begin``."""
    executor = executor_for(setup_for(tmp_path, wall), wall)

    executor.end(ASSIGNMENT)
    results = drain(executor)

    assert [one.outcome for one in results] == ["not_attempted"]
    assert results[0].client_notes == NEVER_BEGUN


def test_ending_work_already_reported_produces_nothing_more(
    tmp_path: Path, wall: FakeWall
) -> None:
    """After a restart the loop may end a pass its record still holds."""
    setup = setup_for(tmp_path, wall)
    first = executor_for(setup, wall)
    work_a_pass(first, wall)
    drain(first)

    second = executor_for(setup, wall)
    second.end(ASSIGNMENT)
    second.begin(ASSIGNMENT)

    assert drain(second) == ()


def test_a_receiver_that_dies_mid_pass_stops_claiming_to_listen(
    tmp_path: Path, wall: FakeWall
) -> None:
    """Hard rule 7 must be able to trust ``listening``; a dead receiver cannot."""
    setup = setup_for(tmp_path, wall)
    executor = executor_for(setup, wall)

    executor.begin(ASSIGNMENT)
    setup.receiver.dead = True  # type: ignore[attr-defined]
    status = executor.status(ASSIGNMENT)
    wall.advance((END_AT - START_AT).total_seconds() + 10)
    executor.end(ASSIGNMENT)
    results = drain(executor)

    assert status.state == "degraded"
    assert status.listening is None
    assert results[0].outcome == "aborted"
    assert "capture was interrupted" in (results[0].client_notes or "")


def test_a_failed_decode_is_aborted_with_its_reason(
    tmp_path: Path, wall: FakeWall
) -> None:
    decoder = ScriptedDecoder(failure="the decoder exited with status 3")
    executor = executor_for(setup_for(tmp_path, wall, decoder=decoder), wall)

    work_a_pass(executor, wall)
    results = drain(executor)

    assert results[0].outcome == "aborted"
    assert (
        results[0].client_notes
        == "simulated receiver, no antenna; the decoder exited with status 3"
    )


def test_a_recording_that_changed_before_its_decode_is_not_decoded(
    tmp_path: Path, wall: FakeWall
) -> None:
    decoder = ScriptedDecoder()
    executor = executor_for(setup_for(tmp_path, wall, decoder=decoder), wall)
    work_a_pass(executor, wall)

    (tmp_path / "captures" / "as_44b2" / "recording.u8").write_bytes(b"tampered")
    results = drain(executor)

    assert decoder.jobs == []
    assert results[0].outcome == "aborted"
    assert RECORDING_CHANGED in (results[0].client_notes or "")


def test_decodes_run_one_at_a_time_oldest_first(tmp_path: Path, wall: FakeWall) -> None:
    decoder = ScriptedDecoder(polls=2)
    executor = executor_for(setup_for(tmp_path, wall, decoder=decoder), wall)
    later = replace(ASSIGNMENT, assignment_id="as_later")

    work_a_pass(executor, wall)
    executor.begin(later)
    wall.advance(60)
    executor.end(later)
    executor.take_completed()

    assert [job.folder.name for job in decoder.jobs] == ["as_44b2"]
    drain(executor, ticks=10)
    assert [job.folder.name for job in decoder.jobs] == ["as_44b2", "as_later"]


# --- restart ------------------------------------------------------------------


def test_a_restart_mid_decode_decodes_again(tmp_path: Path, wall: FakeWall) -> None:
    setup = setup_for(tmp_path, wall)
    first = executor_for(setup, wall)
    work_a_pass(first, wall)
    first.take_completed()  # the decode starts, and the station loses power

    decoder = ScriptedDecoder()
    restarted = executor_for(replace(setup, decoder=decoder), wall)
    results = drain(restarted)

    assert len(decoder.jobs) == 1
    assert [one.outcome for one in results] == ["decoded"]


def test_a_result_handed_over_again_after_a_restart_is_byte_identical(
    tmp_path: Path, wall: FakeWall
) -> None:
    """D-123: rebuilt from disk, so the platform writes nothing the second time."""
    setup = setup_for(tmp_path, wall, keep_recordings=True)
    first = executor_for(setup, wall)
    work_a_pass(first, wall)
    handed = first_results(first)  # taken; the station dies before the next tick

    again = executor_for(setup, wall).take_completed()

    body = build_observation_body(handed[0], "st_7fa3c1")
    assert [build_observation_body(one, "st_7fa3c1") for one in again] == [body]


def test_a_capture_interrupted_by_a_restart_is_decoded_and_aborted(
    tmp_path: Path, wall: FakeWall
) -> None:
    setup = setup_for(tmp_path, wall)
    first = executor_for(setup, wall)
    first.begin(ASSIGNMENT)
    wall.advance(300)
    (tmp_path / "captures" / "as_44b2" / "recording.u8").write_bytes(
        b"\x7f" * 2 * 300_000
    )

    restarted = executor_for(setup, wall)
    results = drain(restarted)

    assert [one.outcome for one in results] == ["aborted"]
    assert "capture was interrupted" in (results[0].client_notes or "")


def test_status_while_idle_and_while_processing(tmp_path: Path, wall: FakeWall) -> None:
    executor = executor_for(setup_for(tmp_path, wall), wall)

    assert executor.status(None).state == "idle"
    work_a_pass(executor, wall)
    assert executor.status(None).state == "processing"
    drain(executor)
    assert executor.status(None).state == "idle"


def test_tuning_is_what_the_manifest_records_while_capturing(
    tmp_path: Path, wall: FakeWall
) -> None:
    setup = setup_for(tmp_path, wall)
    executor = executor_for(setup, wall)

    executor.begin(ASSIGNMENT)
    manifest = setup.folders.read("as_44b2")

    assert manifest is not None
    assert manifest.phase == "capturing"
    assert manifest.tuning == Tuning(
        137_900_000,
        1_000,
        None,
        tmp_path / "captures" / "as_44b2" / "recording.u8",
        "u8",
    )
    assert manifest.recording is None
