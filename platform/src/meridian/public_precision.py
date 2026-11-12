"""How precisely a published time or angle may be stated: D-093's rule.

Pass times and angles are computed from a station's *stored* position, so
publishing them exactly would let anyone fit that position back out of them and
undo the precision the operator declared (D-082). Wherever such a value leaves
the platform, it is stated to the minute and the degree, by these functions
and no others:

* the public API, through ``meridian.api.public.window_privacy``;
* the public cut of the evidence dataset (D-369), which ``meridian.datasets``
  builds, and which may not import ``meridian.api`` (D-143).

Widened, not rounded. A start is floored and an end is ceiled, so a published
window always contains the real one: a reader is never told that a pass ends
before it does.

Pure computation: no I/O, no framework.

Reference: docs/DECISIONS.md D-082, D-093, D-369.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

__all__ = ["ceil_minute", "floor_minute", "publish_angle"]

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


def publish_angle(degrees: float) -> int:
    """An elevation or azimuth, as whole degrees.

    Azimuth 359.6° becomes 360, not 0: the two are the same bearing, and wrapping
    would make a published azimuth disagree with its own rounding for no gain.
    """
    return round(degrees)
