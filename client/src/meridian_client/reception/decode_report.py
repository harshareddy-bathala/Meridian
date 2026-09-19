"""Meridian's decode report: what a decoder must write, and how strictly it is read.

A station runs whichever decoder it likes — SatDump, a GNU Radio flowgraph, a
script — wrapped so that it writes one JSON file at ``{report_path}`` (D-124).
This module is that file's contract. It reads nothing else a decoder produces:
no SatDump output reader ships until a real recording validates one.

**Strict, because a lenient reader counts wrongly with nothing to show it.** A
misspelled key is refused rather than ignored, an integer is never a boolean, a
number is always finite, and an offset outside the recording invalidates the
whole report rather than being clamped into it (D-122). A report that is refused
is a failed decode, and the pass is ``aborted`` — which is honest about the
station's own chain, where a guessed number would not be.

The report, with every optional key present::

    {
      "format": 1,
      "decoder": "satdump",
      "decoder_version": "1.2.2",
      "frames_decoded": 412,
      "frames_failed": 37,
      "first_frame_offset_s": 35.2,
      "snr": [{"offset_s": 35.0, "snr_db": 3.1}, {"offset_s": 326.0, "snr_db": 11.4}],
      "noise_floor_dbfs": -52.3
    }

``snr`` absent means not measured; ``[]`` means measured and nothing found.

Reference: docs/DECISIONS.md D-117, D-122, D-124.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

__all__ = [
    "MAX_REPORT_BYTES",
    "MAX_SNR_POINTS",
    "REPORT_FORMAT",
    "DecodeFailure",
    "DecodeReport",
    "DecodeReportError",
    "SnrPoint",
    "parse_decode_report",
    "read_decode_report",
]

REPORT_FORMAT = 1

MAX_REPORT_BYTES = 16 * 1024 * 1024
"""Refused unread above this. A report is kilobytes; sixteen megabytes is a
decoder writing something that is not a report, and parsing it would stall the
loop."""

MAX_SNR_POINTS = 100_000
"""A bound on the raw series, far above one point a second for any pass. The
executor reduces it to MSP's 512 (D-122); this only stops a runaway decoder."""

_KEYS = frozenset(
    {
        "format",
        "decoder",
        "decoder_version",
        "frames_decoded",
        "frames_failed",
        "first_frame_offset_s",
        "snr",
        "noise_floor_dbfs",
    }
)


class DecodeReportError(ValueError):
    """A report that is missing, unparseable, or breaks the contract."""


@dataclass(frozen=True, slots=True)
class SnrPoint:
    """One SNR measurement, placed as an offset into the recording."""

    offset_s: float
    snr_db: float


@dataclass(frozen=True, slots=True)
class DecodeReport:
    """A report that passed every check. Unknown values are ``None``, not zero."""

    decoder: str
    decoder_version: str | None
    frames_decoded: int | None
    frames_failed: int | None
    first_frame_offset_s: float | None
    snr: tuple[SnrPoint, ...] | None
    noise_floor_dbfs: float | None


@dataclass(frozen=True, slots=True)
class DecodeFailure:
    """A decode that produced no usable report, and why, for ``client_notes``."""

    reason: str


def read_decode_report(path: Path, *, recording_duration_s: float) -> DecodeReport:
    """Read and check the report a decoder wrote.

    Args:
        path: ``{report_path}``.
        recording_duration_s: The recording's length. Every offset must fall
            inside it.

    Raises:
        DecodeReportError: The file is missing, too large, not JSON, or not a
            report this contract accepts.
    """
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise DecodeReportError(f"the decoder wrote no report: {exc}") from exc
    if size > MAX_REPORT_BYTES:
        raise DecodeReportError(f"the report is {size} bytes, over {MAX_REPORT_BYTES}")
    try:
        decoded = json.loads(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise DecodeReportError(f"the report is not JSON: {exc}") from exc
    return parse_decode_report(decoded, recording_duration_s=recording_duration_s)


def parse_decode_report(
    decoded: object, *, recording_duration_s: float
) -> DecodeReport:
    """Check an already-decoded report against the contract.

    Args:
        decoded: The JSON value read from the report file.
        recording_duration_s: The recording's length, in seconds.

    Raises:
        DecodeReportError: With the first rule the report breaks.
    """
    stored = _object(decoded)
    frames_decoded = _count(stored, "frames_decoded")
    first_frame_offset_s = _offset(stored, "first_frame_offset_s", recording_duration_s)
    if first_frame_offset_s is not None and not frames_decoded:
        # An offset for a frame nobody counted fits no row of D-122's table.
        raise DecodeReportError(
            "first_frame_offset_s names a frame, so frames_decoded must be 1 or more"
        )
    return DecodeReport(
        decoder=_decoder(stored),
        decoder_version=_optional_text(stored, "decoder_version"),
        frames_decoded=frames_decoded,
        frames_failed=_count(stored, "frames_failed"),
        first_frame_offset_s=first_frame_offset_s,
        snr=_snr(stored, recording_duration_s),
        noise_floor_dbfs=_number(stored, "noise_floor_dbfs"),
    )


def _object(decoded: object) -> Mapping[str, object]:
    """The report's top level: an object, in this format, with no unknown keys."""
    if not isinstance(decoded, dict):
        raise DecodeReportError("the report must be a JSON object")
    if decoded.get("format") != REPORT_FORMAT:
        raise DecodeReportError(
            f"format must be {REPORT_FORMAT}, not {decoded.get('format')!r}"
        )
    unknown = sorted(set(decoded) - _KEYS)
    if unknown:
        # A misspelled key read as absent would report "not measured" for
        # something the decoder measured, and nobody would see why.
        raise DecodeReportError(f"the report has unknown keys: {unknown}")
    return decoded


def _decoder(stored: Mapping[str, object]) -> str:
    decoder = stored.get("decoder")
    if not isinstance(decoder, str) or not decoder:
        raise DecodeReportError("decoder must name the decoder")
    return decoder


def _optional_text(stored: Mapping[str, object], key: str) -> str | None:
    value = stored.get(key)
    if value is not None and not isinstance(value, str):
        raise DecodeReportError(f"{key} must be a string, not {value!r}")
    return value


def _count(stored: Mapping[str, object], key: str) -> int | None:
    value = stored.get(key)
    if value is None:
        return None
    # bool is a subclass of int, and `true` would otherwise count as one frame.
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DecodeReportError(
            f"{key} must be a whole number of frames, not {value!r}"
        )
    return value


def _number(stored: Mapping[str, object], key: str) -> float | None:
    return _finite(stored.get(key), key)


def _finite(value: object, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise DecodeReportError(f"{name} must be a number, not {value!r}")
    if not math.isfinite(value):
        raise DecodeReportError(f"{name} must be finite, not {value!r}")
    return float(value)


def _offset(
    stored: Mapping[str, object], key: str, recording_duration_s: float
) -> float | None:
    return _within(_finite(stored.get(key), key), key, recording_duration_s)


def _within(
    offset: float | None, name: str, recording_duration_s: float
) -> float | None:
    """An offset inside the recording, or the report is refused (D-122)."""
    if offset is not None and not 0.0 <= offset <= recording_duration_s:
        raise DecodeReportError(
            f"{name} {offset} s is outside the {recording_duration_s} s recording"
        )
    return offset


def _snr(
    stored: Mapping[str, object], recording_duration_s: float
) -> tuple[SnrPoint, ...] | None:
    """The SNR series, in time order, every point inside the recording."""
    series = stored.get("snr")
    if series is None:
        return None
    if not isinstance(series, list):
        raise DecodeReportError("snr must be an array")
    if len(series) > MAX_SNR_POINTS:
        raise DecodeReportError(f"snr has {len(series)} points, over {MAX_SNR_POINTS}")
    points = tuple(_snr_point(one, recording_duration_s) for one in series)
    if any(later.offset_s < earlier.offset_s for earlier, later in pairwise(points)):
        raise DecodeReportError("snr points must be in time order")
    return points


def _snr_point(value: object, recording_duration_s: float) -> SnrPoint:
    if not isinstance(value, dict) or set(value) != {"offset_s", "snr_db"}:
        raise DecodeReportError(
            f"an snr point must be {{offset_s, snr_db}}, not {value!r}"
        )
    offset_s = _within(
        _finite(value["offset_s"], "snr offset_s"), "snr offset_s", recording_duration_s
    )
    snr_db = _finite(value["snr_db"], "snr_db")
    if offset_s is None or snr_db is None:
        raise DecodeReportError("an snr point needs both offset_s and snr_db")
    return SnrPoint(offset_s, snr_db)
