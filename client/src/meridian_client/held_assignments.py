"""What this station holds, written down before it says it holds anything.

MSP §4.2's ``held_assignments`` is the station's whole answer to "what work do
you have", and D-003 makes absence from that list the only way to decline. So the
list has to be backed by something that survives a reboot: a station that
reported holding work it lost with its memory has told the platform a pass is
covered when it is not, and the platform has no way to find out until the pass is
gone.

The rule this module exists to enforce, in one sentence: **an assignment reaches
this file before it reaches the list.** If the write fails the station simply
does not name it, the platform reads that as a decline, and the outcome is right
with no error path at all.

The decisions are module-level functions on plain values; only
:class:`AssignmentRecord` touches the disk. Reference:
docs/MSP-SPEC.md §4.2, §4.3; docs/DECISIONS.md D-003, D-022.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path

from meridian_client.assignment_message import (
    Assignment,
    MalformedAssignmentError,
    parse_assignment,
)

__all__ = [
    "AssignmentRecord",
    "due_now",
    "merge_by_id",
    "still_current",
]


def merge_by_id(
    held: Sequence[Assignment], arriving: Iterable[Assignment]
) -> tuple[Assignment, ...]:
    """Fold newly delivered assignments into the held set, keyed on their id.

    Args:
        held: What the station already has.
        arriving: What the latest heartbeat response carried.

    Returns:
        The union, ordered by ``start_at`` so the soonest work reads first.

    Note:
        **Redelivery is not a new assignment.** MSP §4.2 returns the same
        assignment on every heartbeat until it is reported or its window passes,
        and requires a client to deduplicate by ``assignment_id`` — so the whole
        of that requirement is this function.

        An arriving copy **replaces** the held one rather than being discarded.
        The two are normally identical, and where they differ the platform's is
        the newer statement of a window it may have recomputed from a fresher
        element set.
    """
    merged = {one.assignment_id: one for one in held}
    for one in arriving:
        merged[one.assignment_id] = one
    return tuple(
        sorted(merged.values(), key=lambda one: (one.start_at, one.assignment_id))
    )


def still_current(held: Sequence[Assignment], now: datetime) -> tuple[Assignment, ...]:
    """The held assignments whose windows have not closed.

    Args:
        held: What the station has.
        now: Timezone-aware UTC.

    Returns:
        Those with ``end_at`` at or after ``now``, in the order given.

    Note:
        Dropped on ``end_at``, not on ``start_at``: an assignment whose window has
        opened is the station's current work. A pass is 8 to 15 minutes and never
        repeats, so the moment its window closes there is nothing left to do with
        it and continuing to name it would keep the platform from expiring it
        (D-067).
    """
    return tuple(one for one in held if one.end_at >= now)


def due_now(held: Sequence[Assignment], now: datetime) -> Assignment | None:
    """The held assignment a station should be receiving at ``now``, if any.

    Args:
        held: What the station has.
        now: Timezone-aware UTC.

    Returns:
        The earliest assignment whose window is open, or ``None`` when the
        station has nothing to do.

    Note:
        **Earliest first, and only one.** A station has one antenna chain in
        Phase 1, and the scheduler already guarantees the windows it issues do
        not overlap for one station (D-065). If two are open anyway — because two
        configurations were scheduled, or a clock stepped — taking the earliest
        is the choice that finishes work rather than abandoning a pass already
        under way for one that just began.
    """
    open_now = [one for one in held if one.start_at <= now <= one.end_at]
    if not open_now:
        return None
    return min(open_now, key=lambda one: (one.start_at, one.assignment_id))


class AssignmentRecord:
    """The station's held assignments, kept in one JSON file.

    Args:
        path: Where the record lives. Read on construction, created on first
            write, and its parent directories made as needed.
    """

    def __init__(self, path: Path) -> None:
        """Open the record at ``path``, loading whatever a previous run left."""
        self._path = path
        self._held: tuple[Assignment, ...] = _read(path)

    def held(self) -> tuple[Assignment, ...]:
        """Everything currently recorded, soonest first."""
        return self._held

    def held_ids(self) -> list[str]:
        """MSP §4.2's ``held_assignments``, ready to put on the wire.

        A list rather than a tuple because it is about to be serialised as a JSON
        array, and an empty one is meaningful: §4.2 says it must be sent as
        ``[]`` rather than omitted, because "I hold nothing" is a statement.
        """
        return [one.assignment_id for one in self._held]

    def accept(self, arriving: Sequence[Assignment]) -> tuple[Assignment, ...]:
        """Take delivery of assignments, writing them down before returning.

        Args:
            arriving: What the heartbeat response carried, already parsed.

        Returns:
            The full held set after the merge.

        Raises:
            OSError: The record could not be written. **Deliberately not caught
                here**: the caller must not add these to its next
                ``held_assignments``, and swallowing the failure is exactly how a
                station ends up claiming work it did not keep.
        """
        merged = merge_by_id(self._held, arriving)
        _write(self._path, merged)
        self._held = merged
        return merged

    def drop_closed(self, now: datetime) -> tuple[Assignment, ...]:
        """Forget assignments whose windows have passed, and say which went.

        Args:
            now: Timezone-aware UTC.

        Returns:
            The assignments dropped, so a caller can log what the station stopped
            holding rather than noticing the list got shorter.
        """
        remaining = still_current(self._held, now)
        if len(remaining) == len(self._held):
            return ()

        dropped = tuple(one for one in self._held if one not in remaining)
        _write(self._path, remaining)
        self._held = remaining
        return dropped


def _write(path: Path, held: Sequence[Assignment]) -> None:
    """Replace the record atomically, so a power cut cannot truncate it.

    A half-written record is worse than an absent one. Absent is a station that
    holds nothing and says so — recoverable, and honest. Truncated is a station
    that cannot read its own file and cannot say anything at all.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    partial.write_text(
        json.dumps([_to_stored(one) for one in held], indent=2) + "\n", encoding="utf-8"
    )
    os.replace(partial, path)  # noqa: PTH105 — Path.replace is the same call


def _read(path: Path) -> tuple[Assignment, ...]:
    """Load the record, or an empty one for a station that has held nothing yet.

    Raises:
        MalformedAssignmentError: The file exists and is not the array this
            module writes. Refused rather than silently emptied — a station that
            treated an unreadable record as "I hold nothing" would decline every
            pass it had already accepted, and do it quietly.
    """
    if not path.exists():
        return ()
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        return tuple(parse_assignment(one) for one in stored)
    except (json.JSONDecodeError, TypeError, AttributeError) as exc:
        raise MalformedAssignmentError(
            f"{path} is not a readable record: {exc}"
        ) from exc


def _to_stored(one: Assignment) -> dict[str, object]:
    """One assignment in the same shape MSP §4.3 delivered it.

    The wire format is reused as the file format so that reading a record and
    reading a response are the same code path — a second representation would be
    a second parser, and the two would drift.
    """
    return {
        "assignment_id": one.assignment_id,
        "satellite_id": one.satellite_id,
        "start_at": one.start_at.isoformat(),
        "end_at": one.end_at.isoformat(),
        "centre_freq_hz": one.centre_freq_hz,
        "mode": one.mode,
        "expected_max_elevation_deg": one.expected_max_elevation_deg,
        "predicted_yield": one.predicted_yield,
        "element_set": {
            "epoch": one.element_set.epoch.isoformat(),
            "line1": one.element_set.line1,
            "line2": one.element_set.line2,
        },
        "timing_uncertainty_s": one.timing_uncertainty_s,
        "priority": one.priority,
    }
