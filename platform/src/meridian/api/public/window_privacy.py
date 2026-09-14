"""Coarsening what a pass reveals about where its station is.

Pass times and angles are computed from a station's *stored* position, so
publishing them exactly would let anyone fit that position back out of them and
undo the precision the operator declared (D-082). D-093 is the rule, and this
module is the one place it is applied: windows are widened to whole minutes and
angles rounded to whole degrees.

Widened, not rounded. A start is floored and an end is ceiled, so a published
window always contains the real one — a reader is never told that a pass ends
before it does.

Pure computation, like ``coordinate_privacy``: no I/O, no framework.

Reference: docs/DECISIONS.md D-082, D-093.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

__all__ = [
    "PublishedWindow",
    "ceil_minute",
    "floor_minute",
    "publish_angle",
    "publish_window",
]

_MINUTE = timedelta(minutes=1)


def floor_minute(instant: datetime) -> datetime:
    """The start of the minute containing ``instant``, in UTC.

    Raises:
        ValueError: ``instant`` is naive. Every timestamp in the store is
            timezone-aware, so a naive one came from somewhere it should not.
    """
    if instant.tzinfo is None:
        raise ValueError("a published instant must be timezone-aware")
    return instant.astimezone(UTC).replace(second=0, microsecond=0)


def ceil_minute(instant: datetime) -> datetime:
    """The first whole minute at or after ``instant``, in UTC."""
    floored = floor_minute(instant)
    return floored if floored == instant else floored + _MINUTE


@dataclass(frozen=True, slots=True)
class PublishedWindow:
    """A time window as the public API may state it: whole minutes, widened."""

    start: datetime
    end: datetime


def publish_window(start: datetime, end: datetime) -> PublishedWindow:
    """Widen ``[start, end]`` to the enclosing whole minutes.

    Args:
        start: The window's true start, timezone-aware.
        end: The window's true end, timezone-aware, not before ``start``.

    Returns:
        A window that contains the true one and whose bounds are whole minutes.
    """
    return PublishedWindow(start=floor_minute(start), end=ceil_minute(end))


def publish_angle(degrees: float) -> int:
    """An elevation or azimuth, as whole degrees.

    Azimuth 359.6° becomes 360, not 0: the two are the same bearing, and wrapping
    would make a published azimuth disagree with its own rounding for no gain.
    """
    return round(degrees)
