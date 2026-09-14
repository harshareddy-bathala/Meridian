"""A reception's manifest: the phase it reached, and what it had by then.

The values :mod:`meridian_client.reception.capture_folder` writes to and reads
from ``manifest.json``, and the rules about them — which phases exist, which may
follow which, and the stored JSON form. Split from the folder module so each
stays a length someone reads in one sitting; nothing here touches the disk
except :meth:`RecordingStamp.of`.

Reference: docs/DECISIONS.md D-068, D-073, D-122, D-123.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from meridian_client.assignment_message import (
    Assignment,
    parse_assignment,
    render_assignment,
)
from meridian_client.clock import parse_wire_time, require_utc
from meridian_client.reception.protocols import BYTES_PER_SAMPLE, Recording, Tuning

__all__ = [
    "MANIFEST_FORMAT",
    "NEXT_PHASES",
    "PHASES",
    "Manifest",
    "RecordingStamp",
    "advance",
    "manifest_from_json",
    "manifest_to_json",
]

MANIFEST_FORMAT = 1

PHASES = (
    "refused",
    "capturing",
    "captured",
    "decoding",
    "reported",
    "handed_over",
)
"""D-123's phases, in the order a reception that works passes through them."""

NEXT_PHASES: Mapping[str, frozenset[str]] = {
    "refused": frozenset({"handed_over"}),
    "capturing": frozenset({"captured"}),
    "captured": frozenset({"decoding", "reported"}),
    "decoding": frozenset({"decoding", "reported"}),
    "reported": frozenset({"handed_over"}),
    "handed_over": frozenset(),
}
"""Where each phase may go next.

``decoding`` may repeat, because a restart mid-decode clears the output and
decodes again. ``captured`` may skip to ``reported``, for a pass whose result
needs no decode — one with no decoder to run. ``refused`` still ends
``handed_over``, because its ``not_attempted`` result has to reach the queue too.
"""


@dataclass(frozen=True, slots=True)
class RecordingStamp:
    """A recording's size and modification time, taken when capture stopped.

    Compared before a decode instead of hashing the file: a pass is about a
    gigabyte, and a hash on a Pi costs seconds the loop may not spend (D-123).
    """

    size_bytes: int
    modified_ns: int

    @classmethod
    def of(cls, path: Path) -> RecordingStamp:
        """The stamp of the file at ``path`` as it is now."""
        stat = path.stat()
        return cls(stat.st_size, stat.st_mtime_ns)


@dataclass(frozen=True, slots=True)
class Manifest:
    """Everything a restart needs to resume one reception."""

    assignment: Assignment
    phase: str
    updated_at: datetime
    capture_started_at: datetime | None = None
    tuning: Tuning | None = None
    recording: Recording | None = None
    recording_stamp: RecordingStamp | None = None
    reason: str | None = None
    """Why the reception was refused or failed, for ``client_notes``."""

    def __post_init__(self) -> None:
        """Refuse a phase D-123 does not name, or a naive instant."""
        if self.phase not in PHASES:
            raise ValueError(f"phase must be one of {PHASES}, not {self.phase!r}")
        require_utc(self.updated_at, "updated_at")

    def recording_unchanged(self) -> bool:
        """Whether the recording is still the file capture stopped with.

        ``False`` when there is no recording or stamp, when the file is gone,
        and when its size or modification time differ — a recording that
        changed before its decode is refused rather than decoded (D-123).
        """
        if self.recording is None or self.recording_stamp is None:
            return False
        try:
            return RecordingStamp.of(self.recording.path) == self.recording_stamp
        except OSError:
            return False


def advance(manifest: Manifest, phase: str, now: datetime) -> Manifest:
    """``manifest`` moved to ``phase``, if D-123's order allows it.

    Args:
        manifest: The manifest as last written. Change other fields with
            :func:`dataclasses.replace` first.
        phase: Where it goes next.
        now: Timezone-aware UTC.

    Raises:
        ValueError: The transition is not in :data:`NEXT_PHASES` — a bug in the
            caller, caught here before it is written down.
    """
    if phase not in NEXT_PHASES[manifest.phase]:
        raise ValueError(f"a reception cannot go from {manifest.phase} to {phase}")
    return replace(manifest, phase=phase, updated_at=now)


def manifest_to_json(manifest: Manifest) -> dict[str, object]:
    """A manifest as JSON-ready values, in the stored format."""
    return {
        "format": MANIFEST_FORMAT,
        "assignment": render_assignment(manifest.assignment),
        "phase": manifest.phase,
        "updated_at": manifest.updated_at.isoformat(),
        "capture_started_at": _instant_or_none(manifest.capture_started_at),
        "tuning": None
        if manifest.tuning is None
        else _tuning_to_stored(manifest.tuning),
        "recording": (
            None
            if manifest.recording is None
            else _recording_to_stored(manifest.recording)
        ),
        "recording_stamp": (
            None
            if manifest.recording_stamp is None
            else {
                "size_bytes": manifest.recording_stamp.size_bytes,
                "modified_ns": manifest.recording_stamp.modified_ns,
            }
        ),
        "reason": manifest.reason,
    }


def manifest_from_json(decoded: object) -> Manifest:
    """The inverse of :func:`manifest_to_json`, strict about every type.

    Raises:
        ValueError: A value is out of range, or the format is unknown.
        TypeError: A value has the wrong JSON type.
        KeyError: A field is missing.
    """
    stored = _mapping(decoded, "manifest")
    if stored.get("format") != MANIFEST_FORMAT:
        raise ValueError(f"unknown manifest format {stored.get('format')!r}")
    tuning = stored["tuning"]
    recording = stored["recording"]
    stamp = stored["recording_stamp"]
    return Manifest(
        assignment=parse_assignment(_mapping(stored["assignment"], "assignment")),
        phase=_text(stored["phase"], "phase"),
        updated_at=parse_wire_time(
            _text(stored["updated_at"], "updated_at"), "updated_at"
        ),
        capture_started_at=_optional_instant(
            stored["capture_started_at"], "capture_started_at"
        ),
        tuning=None
        if tuning is None
        else _tuning_from_stored(_mapping(tuning, "tuning")),
        recording=(
            None
            if recording is None
            else _recording_from_stored(_mapping(recording, "recording"))
        ),
        recording_stamp=(
            None
            if stamp is None
            else RecordingStamp(
                _whole(_mapping(stamp, "recording_stamp")["size_bytes"], "size_bytes"),
                _whole(
                    _mapping(stamp, "recording_stamp")["modified_ns"], "modified_ns"
                ),
            )
        ),
        reason=_optional_text(stored["reason"], "reason"),
    )


def _tuning_to_stored(tuning: Tuning) -> dict[str, object]:
    return {
        "centre_freq_hz": tuning.centre_freq_hz,
        "sample_rate_hz": tuning.sample_rate_hz,
        "gain_db": tuning.gain_db,
        "recording_path": str(tuning.recording_path),
        "sample_format": tuning.sample_format,
    }


def _tuning_from_stored(stored: Mapping[str, object]) -> Tuning:
    return Tuning(
        centre_freq_hz=_whole(stored["centre_freq_hz"], "centre_freq_hz"),
        sample_rate_hz=_whole(stored["sample_rate_hz"], "sample_rate_hz"),
        gain_db=_optional_number(stored["gain_db"], "gain_db"),
        recording_path=Path(_text(stored["recording_path"], "recording_path")),
        sample_format=_sample_format(stored["sample_format"]),
    )


def _recording_to_stored(recording: Recording) -> dict[str, object]:
    return {
        "path": str(recording.path),
        "sample_rate_hz": recording.sample_rate_hz,
        "sample_format": recording.sample_format,
        "centre_freq_hz": recording.centre_freq_hz,
        "sample_count": recording.sample_count,
        "first_sample_at": recording.first_sample_at.isoformat(),
        "stopped_at": recording.stopped_at.isoformat(),
        "gain_db": recording.gain_db,
        "interrupted": recording.interrupted,
        "notes": recording.notes,
    }


def _recording_from_stored(stored: Mapping[str, object]) -> Recording:
    sample_format = _sample_format(stored["sample_format"])
    interrupted = stored["interrupted"]
    if not isinstance(interrupted, bool):
        raise TypeError(f"interrupted must be true or false, not {interrupted!r}")
    return Recording(
        path=Path(_text(stored["path"], "path")),
        sample_rate_hz=_whole(stored["sample_rate_hz"], "sample_rate_hz"),
        sample_format=sample_format,
        centre_freq_hz=_whole(stored["centre_freq_hz"], "centre_freq_hz"),
        sample_count=_whole(stored["sample_count"], "sample_count"),
        first_sample_at=parse_wire_time(
            _text(stored["first_sample_at"], "first_sample_at"), "first_sample_at"
        ),
        stopped_at=parse_wire_time(
            _text(stored["stopped_at"], "stopped_at"), "stopped_at"
        ),
        gain_db=_optional_number(stored["gain_db"], "gain_db"),
        interrupted=interrupted,
        notes=_optional_text(stored["notes"], "notes"),
    )


def _sample_format(value: object) -> str:
    sample_format = _text(value, "sample_format")
    if sample_format not in BYTES_PER_SAMPLE:
        raise ValueError(f"unknown sample_format {sample_format!r}")
    return sample_format


def _instant_or_none(instant: datetime | None) -> str | None:
    return None if instant is None else instant.isoformat()


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be an object, not {value!r}")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string, not {value!r}")
    return value


def _optional_text(value: object, name: str) -> str | None:
    return None if value is None else _text(value, name)


def _optional_instant(value: object, name: str) -> datetime | None:
    return None if value is None else parse_wire_time(_text(value, name), name)


def _whole(value: object, name: str) -> int:
    # bool is a subclass of int, and `true` is not a sample count.
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TypeError(f"{name} must be a whole number, not {value!r}")
    return value


def _optional_number(value: object, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{name} must be a number, not {value!r}")
    return float(value)
