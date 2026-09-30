"""How many frames a pass should have produced, had every one been received.

The denominator of the reception verdict's frames ratio: frames decoded, which a
station reports, over frames expected, which **the platform computes and no
station sends** (D-103). One definition for every station, so two stations'
ratios are the same quantity whatever decoder each runs.

Expected is the pass's duration over the transmitter's nominal frame interval.
The pass is acquisition to loss as the platform predicted it — not the
assignment's window, which the platform widened by its own timing uncertainty
(D-021), and not the station's recording, which a station may widen further. A
ratio whose denominator grew with the platform's uncertainty would read as a
worse reception whenever the elements were older.

It is an upper bound, and is meant as one. Near the horizon a station hears
nothing, so no real reception reaches it; the ratio says how much of what the
satellite sent during the pass was recovered, and the verdict learns what a good
reception's ratio looks like rather than this module guessing it.

Pure: no database, no clock. Reference: docs/DECISIONS.md D-103, D-104, D-250.
"""

from __future__ import annotations

import math
from datetime import datetime

__all__ = ["frames_expected"]


def frames_expected(
    aos: datetime, los: datetime, frame_interval_s: float | None
) -> int | None:
    """Whole frames a transmitter sends between acquisition and loss.

    Args:
        aos: The pass's predicted acquisition of signal.
        los: Its predicted loss of signal.
        frame_interval_s: The transmitter's nominal seconds between frames, or
            ``None`` where nobody has stated it.

    Returns:
        The count, rounded down, because a frame the pass ended part-way
        through was never sent whole. ``None`` when the interval is unknown:
        the verdict then omits the ratio rather than dividing by a guess
        (D-104).

    Raises:
        ValueError: The interval is not positive, or the pass ends before it
            begins. Both are a caller's bug, and the table refuses the first.
    """
    if frame_interval_s is None:
        return None
    if not frame_interval_s > 0:
        raise ValueError(f"a frame interval must be positive, got {frame_interval_s}")
    duration_s = (los - aos).total_seconds()
    if duration_s < 0:
        raise ValueError(f"the pass ends at {los} before it begins at {aos}")
    return math.floor(duration_s / frame_interval_s)
