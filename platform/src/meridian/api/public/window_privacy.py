"""Coarsening what a pass reveals about where its station is.

Pass times and angles are computed from a station's *stored* position, so
publishing them exactly would let anyone fit that position back out of them and
undo the precision the operator declared (D-082). D-093 is the rule. It lives in
``meridian.public_precision``, which the evidence dataset's public cut applies
too (D-369); this module is where the public API applies it, and every public
response model imports it from here: windows are widened to whole minutes and
angles rounded to whole degrees.

Widened, not rounded. A start is floored and an end is ceiled, so a published
window always contains the real one — a reader is never told that a pass ends
before it does.

Pure computation, like ``coordinate_privacy``: no I/O, no framework.

Reference: docs/DECISIONS.md D-082, D-093, D-369.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from meridian.public_precision import ceil_minute, floor_minute, publish_angle

__all__ = [
    "PublishedWindow",
    "ceil_minute",
    "floor_minute",
    "publish_angle",
    "publish_window",
]


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
