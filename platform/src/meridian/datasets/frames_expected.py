"""Frames expected for every assignment a raw snapshot holds.

The reception verdict reads frames decoded against frames expected (D-103), and
it reads them from a snapshot, as every model input is read (D-143). A station
reports the first; this computes the second from what the snapshot froze — the
assignment's downlink, its pass's acquisition and loss, and that downlink's
nominal frame interval — with :func:`meridian.observations.frames_expected`,
the one definition the platform has.

**Which interval.** The transmitter the assignment was pointed at: the pass's
satellite, the assignment's frequency and mode. A live row is preferred to a
withdrawn one, then the lowest-numbered, so the answer does not depend on file
order. No matching row, or a row whose interval nobody stated, gives ``None``.

**A snapshot exported before migration 0026** holds no interval column, and is
read as every interval unknown rather than refused: the verdict omits the ratio
for those receptions, which is exactly what it does for a downlink whose
interval is unknown today (D-250).

Reference: docs/DECISIONS.md D-103, D-104, D-143, D-250.
"""

from __future__ import annotations

from collections.abc import Mapping

from meridian.datasets.row_fields import (
    MalformedSnapshotError,
    instant,
    integer,
    jsonl_rows,
    optional_instant,
    text,
)
from meridian.observations.frames_expected import frames_expected

__all__ = ["read_frames_expected"]

_Downlink = tuple[str, int, str]
"""Satellite, centre frequency in hertz, mode: a transmitter's identity."""


def read_frames_expected(files: Mapping[str, bytes]) -> dict[str, int | None]:
    """Each assignment's frames expected, keyed by assignment id.

    Args:
        files: A raw snapshot's files, by name, as read and verified.

    Returns:
        One entry per assignment, ``None`` where the downlink's interval is
        unknown. Every revision of an assignment's report shares it: the pass
        did not change when the station resubmitted.

    Raises:
        MalformedSnapshotError: A needed file is missing, a row lacks a field,
            or an assignment names a pass the snapshot does not hold.
    """
    passes = {
        integer(one, "id"): (
            text(one, "satellite_id"),
            instant(one, "aos"),
            instant(one, "los"),
        )
        for one in _lines(files, "passes")
    }
    intervals = _intervals(_lines(files, "transmitters"))

    expected: dict[str, int | None] = {}
    for one in _lines(files, "assignments"):
        assignment_id = text(one, "assignment_id")
        pass_id = integer(one, "pass_id")
        if pass_id not in passes:
            message = f"assignment {assignment_id} names pass {pass_id}, not held"
            raise MalformedSnapshotError(message)
        satellite_id, aos, los = passes[pass_id]
        downlink = (satellite_id, integer(one, "centre_freq_hz"), text(one, "mode"))
        expected[assignment_id] = frames_expected(aos, los, intervals.get(downlink))
    return expected


def _intervals(
    transmitters: list[Mapping[str, object]],
) -> dict[_Downlink, float | None]:
    """Each downlink's frame interval, from its preferred transmitter row."""
    ranked = sorted(
        transmitters,
        key=lambda one: (
            optional_instant(one, "deleted_at") is not None,
            integer(one, "id"),
        ),
    )
    intervals: dict[_Downlink, float | None] = {}
    for one in ranked:
        downlink = (
            text(one, "satellite_id"),
            integer(one, "centre_freq_hz"),
            text(one, "mode"),
        )
        intervals.setdefault(downlink, _interval(one))
    return intervals


def _interval(row: Mapping[str, object]) -> float | None:
    """The row's interval; absent from a snapshot made before migration 0026."""
    value = row.get("frame_interval_s")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        message = f"a transmitter's frame_interval_s is not positive: {value!r}"
        raise MalformedSnapshotError(message)
    return float(value)


def _lines(files: Mapping[str, bytes], name: str) -> list[Mapping[str, object]]:
    """Every row of one file, parsed, or a refusal naming the missing file."""
    try:
        data = files[f"{name}.jsonl"]
    except KeyError as exc:
        message = f"the raw snapshot has no {name}.jsonl"
        raise MalformedSnapshotError(message) from exc
    return jsonl_rows(data, f"{name}.jsonl")
