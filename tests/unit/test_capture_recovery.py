"""What a restart finds in the capture folders, phase by phase (D-123).

Manifests are written directly, in the state a power cut could leave them, so
each row of the recovery table is checked on its own. The executor tests check
the same recovery end to end.

Marked as a unit test by living in ``tests/unit``.

Reference: docs/DECISIONS.md D-073, D-074, D-122, D-123.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from meridian_client.assignment_message import Assignment, ElementSet
from meridian_client.reception.capture_folder import MANIFEST_NAME, CaptureFolders
from meridian_client.reception.capture_recovery import (
    NOTHING_RECORDED,
    PRUNE_AFTER,
    discard_recording,
    facts_for,
    recover,
)
from meridian_client.reception.decode_report import DecodeFailure, DecodeReport
from meridian_client.reception.manifest import Manifest, RecordingStamp
from meridian_client.reception.protocols import Recording, Tuning
from meridian_client.reception.subprocess_decoder import decode_paths

START_AT = datetime(2026, 8, 14, 9, 41, 18, tzinfo=UTC)
NOW = START_AT + timedelta(minutes=20)
LINE1 = "1 57166U 23091A   26223.50000000  .00000100  00000-0  50000-4 0  9990"
LINE2 = "2 57166  98.7041 210.4322 0002726  80.4113 279.7297 14.22000000160126"


def assignment(assignment_id: str = "as_44b2") -> Assignment:
    return Assignment(
        assignment_id=assignment_id,
        satellite_id="norad:57166",
        start_at=START_AT,
        end_at=START_AT + timedelta(minutes=11),
        centre_freq_hz=137_900_000,
        mode="lrpt",
        expected_max_elevation_deg=61.4,
        predicted_yield=None,
        element_set=ElementSet(datetime(2026, 8, 14, 2, 11, tzinfo=UTC), LINE1, LINE2),
        timing_uncertainty_s=4.2,
        priority=1.0,
    )


@pytest.fixture
def folders(tmp_path: Path) -> CaptureFolders:
    return CaptureFolders(tmp_path / "captures")


def capturing(folders: CaptureFolders, recording_bytes: int | None) -> Manifest:
    """A capture a power cut interrupted, with ``recording_bytes`` on disk."""
    folder = folders.folder_for("as_44b2")
    folder.mkdir(parents=True)
    path = folder / "recording.u8"
    if recording_bytes is not None:
        path.write_bytes(b"\x7f" * recording_bytes)
    manifest = Manifest(
        assignment(),
        "capturing",
        START_AT,
        capture_started_at=START_AT,
        tuning=Tuning(137_900_000, 1_000, 32.8, path, "u8"),
    )
    folders.write(manifest)
    return manifest


def recording(path: Path) -> Recording:
    return Recording(
        path,
        1_000,
        "u8",
        137_900_000,
        660_000,
        START_AT,
        START_AT + timedelta(minutes=11),
        32.8,
        False,
    )


# --- capturing ----------------------------------------------------------------


def test_an_interrupted_capture_becomes_an_interrupted_recording_to_decode(
    folders: CaptureFolders,
) -> None:
    """Its last sample is the file's modification time; its first, counted back."""
    manifest = capturing(folders, recording_bytes=2 * 300_000)
    path = manifest.tuning.recording_path  # type: ignore[union-attr]
    last_write = START_AT + timedelta(minutes=6)
    os.utime(path, (last_write.timestamp(), last_write.timestamp()))

    recovery = recover(folders, NOW)

    assert [one.phase for one in recovery.to_decode] == ["captured"]
    recovered = recovery.to_decode[0].recording
    assert recovered is not None
    assert recovered.interrupted
    assert recovered.sample_count == 300_000
    assert recovered.stopped_at == last_write
    assert recovered.first_sample_at == last_write - timedelta(seconds=300)
    assert recovery.to_decode[0].recording_unchanged()
    assert folders.read("as_44b2") == recovery.to_decode[0]


def test_a_recording_is_never_placed_before_its_capture_began(
    folders: CaptureFolders,
) -> None:
    """A replayed file's modification time is older than the pass it stands in for."""
    manifest = capturing(folders, recording_bytes=2 * 300_000)
    path = manifest.tuning.recording_path  # type: ignore[union-attr]
    long_ago = START_AT - timedelta(days=90)
    os.utime(path, (long_ago.timestamp(), long_ago.timestamp()))

    recovered = recover(folders, NOW).to_decode[0].recording

    assert recovered is not None
    assert recovered.first_sample_at == START_AT


@pytest.mark.parametrize("recording_bytes", [None, 0, 1])
def test_an_interrupted_capture_that_recorded_nothing_is_reported_aborted(
    folders: CaptureFolders, recording_bytes: int | None
) -> None:
    capturing(folders, recording_bytes)

    recovery = recover(folders, NOW)

    assert recovery.to_decode == ()
    assert [one.phase for one in recovery.to_hand_over] == ["reported"]
    facts = facts_for(recovery.to_hand_over[0], folders.folder_for("as_44b2"))
    assert facts.decode == DecodeFailure(NOTHING_RECORDED)
    assert facts.recording is not None


# --- the other phases ---------------------------------------------------------


@pytest.mark.parametrize(
    ("phase", "decode", "hand_over"),
    [
        ("captured", True, False),
        ("decoding", True, False),
        ("refused", False, True),
        ("reported", False, True),
    ],
)
def test_each_phase_resumes_where_it_stopped(
    folders: CaptureFolders, tmp_path: Path, phase: str, decode: bool, hand_over: bool
) -> None:
    folders.write(
        Manifest(assignment(), phase, START_AT, recording=recording(tmp_path / "r.u8"))
    )

    recovery = recover(folders, NOW)

    assert bool(recovery.to_decode) is decode
    assert bool(recovery.to_hand_over) is hand_over


def test_a_handed_over_recording_is_discarded_and_its_folder_kept_for_now(
    folders: CaptureFolders,
) -> None:
    folder = folders.folder_for("as_44b2")
    folder.mkdir(parents=True)
    (folder / "recording.u8").write_bytes(b"\x7f" * 20)
    folders.write(
        Manifest(
            assignment(),
            "handed_over",
            NOW,
            recording=recording(folder / "recording.u8"),
        )
    )

    recovery = recover(folders, NOW)

    assert recovery == type(recovery)()
    assert not (folder / "recording.u8").exists()
    assert (folder / MANIFEST_NAME).exists()


def test_a_kept_recording_survives_recovery(folders: CaptureFolders) -> None:
    folder = folders.folder_for("as_44b2")
    folder.mkdir(parents=True)
    (folder / "recording.u8").write_bytes(b"\x7f" * 20)
    folders.write(
        Manifest(
            assignment(),
            "handed_over",
            NOW,
            recording=recording(folder / "recording.u8"),
        )
    )

    recover(folders, NOW, keep_recordings=True)

    assert (folder / "recording.u8").exists()


def test_a_folder_past_the_acceptance_window_is_pruned(folders: CaptureFolders) -> None:
    """D-123: thirty days, the window the upload queue already mirrors (D-074)."""
    folders.write(Manifest(assignment("as_old"), "handed_over", START_AT))
    folders.write(
        Manifest(assignment("as_recent"), "handed_over", START_AT + PRUNE_AFTER)
    )

    recover(folders, START_AT + PRUNE_AFTER + timedelta(seconds=1))

    assert [one.assignment_id for one in folders.scan()] == ["as_recent"]


def test_a_replayed_recording_outside_the_folder_is_never_discarded(
    folders: CaptureFolders, tmp_path: Path
) -> None:
    operator_file = tmp_path / "operator" / "pass.cf32"
    operator_file.parent.mkdir()
    operator_file.write_bytes(b"\0" * 16)
    manifest = Manifest(
        assignment(), "handed_over", NOW, recording=recording(operator_file)
    )

    discard_recording(manifest, folders.folder_for("as_44b2"))

    assert operator_file.exists()


def test_an_unreadable_folder_is_reported_and_the_rest_still_recover(
    folders: CaptureFolders,
) -> None:
    folders.write(Manifest(assignment("as_good"), "refused", START_AT, reason="test"))
    broken = folders.folder_for("as_broken")
    broken.mkdir(parents=True)
    (broken / MANIFEST_NAME).write_text("{", encoding="utf-8")

    recovery = recover(folders, NOW)

    assert [one.assignment_id for one in recovery.unreadable] == ["as_broken"]
    assert [one.assignment.assignment_id for one in recovery.to_hand_over] == [
        "as_good"
    ]
    assert (broken / MANIFEST_NAME).read_text() == "{"


# --- facts_for ----------------------------------------------------------------


def test_the_facts_of_a_decoded_reception_are_read_from_its_report(
    folders: CaptureFolders, tmp_path: Path
) -> None:
    folder = folders.folder_for("as_44b2")
    folder.mkdir(parents=True)
    decode_paths(folder).report.write_text(
        json.dumps(
            {
                "format": 1,
                "decoder": "satdump",
                "frames_decoded": 3,
                "first_frame_offset_s": 2.0,
            }
        )
    )
    manifest = Manifest(
        assignment(), "reported", NOW, recording=recording(tmp_path / "r.u8")
    )

    facts = facts_for(manifest, folder)

    assert isinstance(facts.decode, DecodeReport)
    assert facts.decode.frames_decoded == 3


def test_a_report_that_went_bad_on_disk_is_a_failed_decode(
    folders: CaptureFolders, tmp_path: Path
) -> None:
    folder = folders.folder_for("as_44b2")
    folder.mkdir(parents=True)
    manifest = Manifest(
        assignment(), "reported", NOW, recording=recording(tmp_path / "r.u8")
    )

    facts = facts_for(manifest, folder)

    assert isinstance(facts.decode, DecodeFailure)
    assert "wrote no report" in facts.decode.reason


def test_the_facts_of_a_refused_reception_carry_only_the_reason(
    folders: CaptureFolders,
) -> None:
    manifest = Manifest(assignment(), "refused", NOW, reason="not enough disk")

    facts = facts_for(manifest, folders.folder_for("as_44b2"))

    assert facts.recording is None
    assert facts.reason == "not enough disk"


def test_a_stamp_taken_at_recovery_matches_the_file(folders: CaptureFolders) -> None:
    manifest = capturing(folders, recording_bytes=200)

    recovered = recover(folders, NOW).to_decode[0]

    path = manifest.tuning.recording_path  # type: ignore[union-attr]
    assert recovered.recording_stamp == RecordingStamp.of(path)
    assert replace(recovered, recording_stamp=None).recording_unchanged() is False
