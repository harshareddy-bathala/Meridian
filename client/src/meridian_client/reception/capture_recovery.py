"""Rebuilding a reception from its capture folder, and resuming one after a restart.

Two jobs that are one rule. **A result is always built from what is on disk** —
the manifest and the report file beside it — never from memory, whether the
executor is handing it over the first time or a restart is handing it over again.
The body is therefore the same both times, its digest is the same, and the
platform writes nothing the second time (D-015, D-123).

At start-up every manifest is read and each reception resumes from its phase:

==================  =====================================================
Phase found         Action
==================  =====================================================
``capturing``       interrupted: decode what was recorded, report ``aborted``
``captured``        decode
``decoding``        clear the decoder's output and decode again
``refused``         hand the ``not_attempted`` result over again
``reported``        hand the result over again
``handed_over``     delete its recording; prune the folder after 30 days
==================  =====================================================

Reference: docs/DECISIONS.md D-015, D-073, D-074, D-122, D-123.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from meridian_client.reception.capture_folder import CaptureFolders, ScannedFolder
from meridian_client.reception.decode_report import (
    DecodeFailure,
    DecodeReport,
    DecodeReportError,
    read_decode_report,
)
from meridian_client.reception.manifest import Manifest, RecordingStamp, advance
from meridian_client.reception.outcome_rules import ReceptionFacts
from meridian_client.reception.protocols import BYTES_PER_SAMPLE, Recording
from meridian_client.reception.subprocess_decoder import decode_paths

__all__ = [
    "PRUNE_AFTER",
    "Recovery",
    "discard_recording",
    "facts_for",
    "recover",
]

_log = logging.getLogger(__name__)

PRUNE_AFTER = timedelta(days=30)
"""How long a finished reception's folder is kept: the platform's acceptance
window, which the upload queue already mirrors (D-074)."""

NOTHING_RECORDED = "the station restarted before anything was recorded"


@dataclass(frozen=True, slots=True)
class Recovery:
    """What start-up found in the capture folders."""

    to_decode: tuple[Manifest, ...] = ()
    to_hand_over: tuple[Manifest, ...] = ()
    unreadable: tuple[ScannedFolder, ...] = ()


def facts_for(manifest: Manifest, folder: Path) -> ReceptionFacts:
    """The facts of a finished reception, read back from its folder.

    Args:
        manifest: A manifest in ``refused`` or ``reported``.
        folder: Its capture folder, where the decode report is.

    Note:
        A decode that failed stored its reason in the manifest. One that
        succeeded left its report on disk, and the report is read again here
        rather than carried in memory — so the result built at hand-over and the
        one rebuilt after a restart come from the same bytes.
    """
    recording = manifest.recording
    if manifest.phase == "refused" or recording is None:
        return ReceptionFacts(manifest.assignment, reason=manifest.reason)
    decode: DecodeReport | DecodeFailure
    if manifest.reason is not None:
        decode = DecodeFailure(manifest.reason)
    else:
        try:
            decode = read_decode_report(
                decode_paths(folder).report,
                recording_duration_s=recording.sample_count / recording.sample_rate_hz,
            )
        except DecodeReportError as exc:
            decode = DecodeFailure(str(exc))
    return ReceptionFacts(manifest.assignment, recording, decode)


def recover(
    folders: CaptureFolders, now: datetime, *, keep_recordings: bool = False
) -> Recovery:
    """Read every capture folder and decide what each reception does next.

    Args:
        folders: The station's capture folders.
        now: Timezone-aware UTC.
        keep_recordings: Leave recordings of handed-over receptions in place.

    Returns:
        The receptions to decode and to hand over, oldest assignment id first,
        and the folders that could not be read. An unreadable folder is logged
        and left alone for an operator; it never stops the rest recovering.
    """
    to_decode: list[Manifest] = []
    to_hand_over: list[Manifest] = []
    unreadable: list[ScannedFolder] = []
    for scanned in folders.scan():
        manifest = scanned.manifest
        if manifest is None:
            _log.error("capture folder %s: %s", scanned.assignment_id, scanned.problem)
            unreadable.append(scanned)
            continue
        folder = folders.folder_for(scanned.assignment_id)
        if manifest.phase == "capturing":
            manifest = _close_interrupted(manifest, now)
            folders.write(manifest)
        if manifest.phase in {"captured", "decoding"}:
            to_decode.append(manifest)
        elif manifest.phase in {"refused", "reported"}:
            to_hand_over.append(manifest)
        else:
            if not keep_recordings:
                discard_recording(manifest, folder)
            _prune(manifest, folder, now)
    return Recovery(tuple(to_decode), tuple(to_hand_over), tuple(unreadable))


def discard_recording(manifest: Manifest, folder: Path) -> None:
    """Delete a handed-over reception's recording, if the station made it.

    A recording outside the capture folder — a file an operator supplied for
    replay — is never this station's to delete (D-123, D-125).
    """
    recording = manifest.recording
    if recording is None or not recording.path.resolve().is_relative_to(
        folder.resolve()
    ):
        return
    recording.path.unlink(missing_ok=True)


def _prune(manifest: Manifest, folder: Path, now: datetime) -> None:
    """Remove a handed-over reception's folder once it is past the window."""
    if manifest.phase == "handed_over" and now - manifest.updated_at > PRUNE_AFTER:
        shutil.rmtree(folder, ignore_errors=True)


def _close_interrupted(manifest: Manifest, now: datetime) -> Manifest:
    """A capture a restart cut short, turned into the recording it left behind.

    Its last sample is taken from the file's modification time, and its first is
    counted back from there by its length (D-122), but never before the capture
    started — a replayed file's modification time is older than the pass it
    stands in for.
    """
    tuning, started_at = manifest.tuning, manifest.capture_started_at
    if tuning is None or started_at is None:
        nothing = advance(replace(manifest, reason=NOTHING_RECORDED), "captured", now)
        return advance(nothing, "reported", now)
    path = tuning.recording_path
    try:
        stat = path.stat()
    except OSError:
        size, modified_at = 0, started_at
    else:
        size, modified_at = stat.st_size, datetime.fromtimestamp(stat.st_mtime, UTC)
    samples = size // BYTES_PER_SAMPLE[tuning.sample_format]
    duration = timedelta(seconds=samples / tuning.sample_rate_hz)
    first_sample_at = max(started_at, modified_at - duration)
    recording = Recording(
        path=path,
        sample_rate_hz=tuning.sample_rate_hz,
        sample_format=tuning.sample_format,
        centre_freq_hz=tuning.centre_freq_hz,
        sample_count=samples,
        first_sample_at=first_sample_at,
        stopped_at=first_sample_at + duration,
        gain_db=tuning.gain_db,
        interrupted=True,
    )
    captured = replace(
        manifest,
        recording=recording,
        recording_stamp=RecordingStamp.of(path) if samples else None,
        reason=None if samples else NOTHING_RECORDED,
    )
    captured = advance(captured, "captured", now)
    return advance(captured, "reported", now) if not samples else captured
