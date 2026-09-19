"""Capture folders and their manifests, written, read back and scanned.

Real files in a temporary directory: every property here is about what a restart
finds on disk (D-123), which a mocked filesystem could not show.

Marked as a unit test by living in ``tests/unit``.

Reference: docs/DECISIONS.md D-068, D-073, D-123.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from meridian_client.assignment_message import Assignment, ElementSet
from meridian_client.reception.capture_folder import (
    MANIFEST_NAME,
    CaptureFolders,
    MalformedManifestError,
)
from meridian_client.reception.manifest import (
    NEXT_PHASES,
    PHASES,
    Manifest,
    RecordingStamp,
    advance,
)
from meridian_client.reception.protocols import Recording, Tuning

NOW = datetime(2026, 8, 14, 9, 53, 0, tzinfo=UTC)
START_AT = datetime(2026, 8, 14, 9, 41, 18, tzinfo=UTC)
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
        element_set=ElementSet(
            epoch=datetime(2026, 8, 14, 2, 11, 0, tzinfo=UTC),
            line1=LINE1,
            line2=LINE2,
        ),
        timing_uncertainty_s=4.2,
        priority=1.0,
    )


def recording(path: Path) -> Recording:
    return Recording(
        path=path,
        sample_rate_hz=1_000,
        sample_format="u8",
        centre_freq_hz=137_900_000,
        sample_count=660_000,
        first_sample_at=START_AT,
        stopped_at=START_AT + timedelta(minutes=11),
        gain_db=None,
        interrupted=False,
        notes="simulated receiver, no antenna",
    )


def full_manifest(tmp_path: Path) -> Manifest:
    """A manifest with every optional field set, as it stands once captured."""
    path = tmp_path / "captures" / "as_44b2" / "recording.u8"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\x7f" * 20)
    return Manifest(
        assignment=assignment(),
        phase="captured",
        updated_at=NOW,
        capture_started_at=START_AT,
        tuning=Tuning(137_900_000, 1_000, 32.8, path, "u8"),
        recording=recording(path),
        recording_stamp=RecordingStamp.of(path),
        reason=None,
    )


def test_every_field_of_a_manifest_survives_the_round_trip(tmp_path: Path) -> None:
    folders = CaptureFolders(tmp_path / "captures")
    manifest = full_manifest(tmp_path)

    folders.write(manifest)

    assert folders.read("as_44b2") == manifest


def test_a_bare_manifest_survives_the_round_trip(tmp_path: Path) -> None:
    """A refused reception has nothing but its assignment and its reason."""
    folders = CaptureFolders(tmp_path / "captures")
    manifest = Manifest(assignment(), "refused", NOW, reason="no decoder for lrpt")

    folders.write(manifest)

    assert folders.read("as_44b2") == manifest


def test_an_assignment_with_no_folder_has_no_manifest(tmp_path: Path) -> None:
    assert CaptureFolders(tmp_path / "captures").read("as_44b2") is None


def test_rewriting_a_manifest_leaves_no_partial_file(tmp_path: Path) -> None:
    """D-068's temp-then-rename: a power cut leaves one phase or the other."""
    folders = CaptureFolders(tmp_path / "captures")
    manifest = full_manifest(tmp_path)
    folders.write(manifest)
    folders.write(advance(manifest, "decoding", NOW + timedelta(seconds=1)))

    names = sorted(one.name for one in (tmp_path / "captures" / "as_44b2").iterdir())

    assert names == [MANIFEST_NAME, "recording.u8"]


@pytest.mark.parametrize(
    "corruption",
    [
        "not json {",
        "[]",
        json.dumps({"format": 99}),
        json.dumps({"format": 1, "phase": "captured"}),
    ],
    ids=["unparseable", "not-an-object", "unknown-format", "missing-fields"],
)
def test_an_unreadable_manifest_is_refused_not_treated_as_absent(
    tmp_path: Path, corruption: str
) -> None:
    """Treating it as absent would lose track of a reception, silently."""
    folder = tmp_path / "captures" / "as_44b2"
    folder.mkdir(parents=True)
    (folder / MANIFEST_NAME).write_text(corruption, encoding="utf-8")

    with pytest.raises(MalformedManifestError):
        CaptureFolders(tmp_path / "captures").read("as_44b2")


def test_a_manifest_in_another_assignments_folder_is_refused(tmp_path: Path) -> None:
    folders = CaptureFolders(tmp_path / "captures")
    folders.write(Manifest(assignment("as_other"), "refused", NOW, reason="test"))
    (tmp_path / "captures" / "as_other").rename(tmp_path / "captures" / "as_44b2")

    with pytest.raises(MalformedManifestError, match="describes as_other"):
        folders.read("as_44b2")


def test_a_boolean_where_a_count_belongs_is_refused(tmp_path: Path) -> None:
    folders = CaptureFolders(tmp_path / "captures")
    folders.write(full_manifest(tmp_path))
    path = tmp_path / "captures" / "as_44b2" / MANIFEST_NAME
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["recording"]["sample_count"] = True
    path.write_text(json.dumps(stored), encoding="utf-8")

    with pytest.raises(MalformedManifestError, match="sample_count"):
        folders.read("as_44b2")


def test_a_scan_reports_every_folder_and_survives_a_corrupt_one(
    tmp_path: Path,
) -> None:
    """One corrupt reception must not stop a station recovering the rest."""
    folders = CaptureFolders(tmp_path / "captures")
    folders.write(Manifest(assignment("as_a"), "refused", NOW, reason="test"))
    (tmp_path / "captures" / "as_b").mkdir()
    (tmp_path / "captures" / "as_c").mkdir()
    (tmp_path / "captures" / "as_c" / MANIFEST_NAME).write_text("{", encoding="utf-8")
    (tmp_path / "captures" / "stray.txt").write_text("not a folder", encoding="utf-8")

    scanned = folders.scan()

    assert [one.assignment_id for one in scanned] == ["as_a", "as_b", "as_c"]
    assert scanned[0].manifest is not None
    assert scanned[1].problem == "no manifest"
    assert scanned[2].manifest is None
    assert "not a readable manifest" in (scanned[2].problem or "")


def test_a_station_with_no_captures_yet_scans_nothing(tmp_path: Path) -> None:
    assert CaptureFolders(tmp_path / "captures").scan() == ()


@pytest.mark.parametrize("assignment_id", ["../held", "as/../x", "", "as a", "as_1\n"])
def test_an_id_that_could_escape_the_folder_is_refused(
    tmp_path: Path, assignment_id: str
) -> None:
    """The id comes from the platform; a ``..`` in it must never become a path."""
    with pytest.raises(ValueError, match="not usable as a folder"):
        CaptureFolders(tmp_path / "captures").folder_for(assignment_id)


# --- phases -------------------------------------------------------------------


def test_a_reception_that_works_passes_through_every_phase_but_refused() -> None:
    manifest = Manifest(assignment(), "capturing", NOW)
    for phase in ("captured", "decoding", "reported", "handed_over"):
        manifest = advance(manifest, phase, NOW)

    assert manifest.phase == "handed_over"


@pytest.mark.parametrize(
    ("current", "following"),
    [
        (current, following)
        for current in PHASES
        for following in PHASES
        if following not in NEXT_PHASES[current]
    ],
)
def test_a_transition_d_123_does_not_allow_is_refused(
    current: str, following: str
) -> None:
    """Caught before it is written down, rather than found at the next restart."""
    with pytest.raises(ValueError, match="cannot go from"):
        advance(Manifest(assignment(), current, NOW), following, NOW)


def test_an_unknown_phase_is_refused() -> None:
    with pytest.raises(ValueError, match="phase must be one of"):
        Manifest(assignment(), "listening", NOW)


def test_advancing_stamps_the_new_instant() -> None:
    later = NOW + timedelta(minutes=2)

    assert (
        advance(Manifest(assignment(), "capturing", NOW), "captured", later).updated_at
        == later
    )


# --- the recording stamp ------------------------------------------------------


def test_an_untouched_recording_matches_its_stamp(tmp_path: Path) -> None:
    assert full_manifest(tmp_path).recording_unchanged()


def test_a_recording_that_changed_before_its_decode_does_not_match(
    tmp_path: Path,
) -> None:
    """D-123: size and modification time, never a gigabyte hashed on the loop."""
    manifest = full_manifest(tmp_path)
    assert manifest.recording is not None
    path = manifest.recording.path
    path.write_bytes(b"\x7f" * 22)
    os.utime(path, ns=(manifest.recording_stamp.modified_ns,) * 2)  # type: ignore[union-attr]

    assert not manifest.recording_unchanged()


def test_a_missing_recording_or_stamp_does_not_match(tmp_path: Path) -> None:
    manifest = full_manifest(tmp_path)
    assert manifest.recording is not None
    manifest.recording.path.unlink()

    assert not manifest.recording_unchanged()
    assert not replace(manifest, recording_stamp=None).recording_unchanged()
