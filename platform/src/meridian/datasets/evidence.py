"""Was the satellite transmitting? D-147's answer, from contemporaneous receptions.

A station confirmed listening that heard nothing has either missed the pass or
listened to a satellite that was not transmitting. ``EVALUATION.md`` §5 tells
them apart by looking at other receptions of the same satellite at about the
same time, and marks the rest indeterminate. This module is that look.

**What counts as evidence**, within ``silent_window_s`` of the pass:

* our own receptions of the satellite at any station, including this one's
  other passes — the latest revision of each report, from the same population
  as the pass: a simulated reception is never evidence about a measured pass,
  nor the other way round;
* archive receptions, matched when their key is a NORAD number equal to ours,
  and used only for measured passes, since an archive describes the real sky.

**What it concludes**, in order:

* any reception with a signal → the satellite was transmitting, so the pass
  was a **confirmed miss**;
* otherwise, at least ``silent_min_attempts`` attempts that heard nothing —
  our own ``no_signal`` *with listening confirmed*, or an archive's
  ``no_data`` → **satellite silent**;
* otherwise → **indeterminate**. With one physical station and sparse archives
  this will be common, and the manifest says how common.

Reference: docs/DECISIONS.md D-145, D-147; docs/EVALUATION.md §5.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from meridian.datasets.snapshot_rows import ArchiveReception, PassRow

__all__ = [
    "SIGNAL",
    "EvidenceIndex",
    "OwnReception",
    "SatelliteState",
    "satellite_state",
]

SIGNAL = frozenset(("decoded", "signal_no_decode"))
"""Outcomes that prove a transmitter was on. The two vocabularies agree here."""

NORAD_PREFIX = "norad:"

SatelliteState = Literal["transmitting", "silent", "indeterminate"]


@dataclass(frozen=True, slots=True)
class OwnReception:
    """One of our own passes' reports, reduced to what D-147 reads."""

    pass_id: int
    satellite_id: str
    at: PassRow
    outcome: str
    listening_confirmed: bool
    simulated: bool


@dataclass(frozen=True, slots=True)
class EvidenceIndex:
    """Every reception a pass might be judged against, grouped by satellite."""

    own: dict[str, tuple[OwnReception, ...]]
    archive: dict[str, tuple[ArchiveReception, ...]]

    @classmethod
    def build(
        cls, own: list[OwnReception], archive: tuple[ArchiveReception, ...]
    ) -> EvidenceIndex:
        """Group receptions by satellite id; archive rows we cannot match drop out."""
        by_satellite: dict[str, list[OwnReception]] = {}
        for one in own:
            by_satellite.setdefault(one.satellite_id, []).append(one)
        matched: dict[str, list[ArchiveReception]] = {}
        for row in archive:
            if row.satellite_key_kind == "norad":
                matched.setdefault(NORAD_PREFIX + row.satellite_key, []).append(row)
        return cls(
            own={key: tuple(value) for key, value in by_satellite.items()},
            archive={key: tuple(value) for key, value in matched.items()},
        )


def satellite_state(
    target: PassRow,
    index: EvidenceIndex,
    *,
    window_s: int,
    min_silent_attempts: int,
) -> SatelliteState:
    """Judge whether the satellite was transmitting during one pass.

    Args:
        target: The pass whose station was confirmed listening and heard nothing.
        index: Every reception in the snapshot.
        window_s: How far either side of the pass a reception still counts.
        min_silent_attempts: Attempts that heard nothing needed to call it silent.

    Returns:
        ``transmitting``, ``silent`` or ``indeterminate``.
    """
    start = target.aos - timedelta(seconds=window_s)
    end = target.los + timedelta(seconds=window_s)
    own = _own_evidence(target, index, start, end)
    archive = (
        (0, 0) if target.simulated else _archive_evidence(target, index, start, end)
    )
    signals, silences = own[0] + archive[0], own[1] + archive[1]
    if signals:
        return "transmitting"
    if silences >= min_silent_attempts:
        return "silent"
    return "indeterminate"


def _own_evidence(
    target: PassRow, index: EvidenceIndex, start: datetime, end: datetime
) -> tuple[int, int]:
    """Signals and confirmed silences among our own other passes in the window."""
    signals = silences = 0
    for one in index.own.get(target.satellite_id, ()):
        if one.pass_id == target.pass_id or one.simulated != target.simulated:
            continue
        if start <= one.at.aos and one.at.los <= end:
            signals += one.outcome in SIGNAL
            silences += one.outcome == "no_signal" and one.listening_confirmed
    return signals, silences


def _archive_evidence(
    target: PassRow, index: EvidenceIndex, start: datetime, end: datetime
) -> tuple[int, int]:
    """Signals and ``no_data`` among the archive's receptions in the window."""
    within = [
        row
        for row in index.archive.get(target.satellite_id, ())
        if start <= row.started_at <= end
    ]
    signals = sum(1 for row in within if row.archive_outcome in SIGNAL)
    silences = sum(1 for row in within if row.archive_outcome == "no_data")
    return signals, silences
