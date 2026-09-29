"""Was the satellite transmitting? D-147's verdict, from counted evidence.

A station confirmed listening that heard nothing either missed the pass or
listened to a satellite that was not transmitting. ``EVALUATION.md`` §5 tells
the two apart by other receptions of the same satellite at about the same
time. Which receptions count is the caller's to gather — the snapshot
labeller reads a snapshot, the live accounting reads the database — and this
module is what both conclude from them, in order:

* any reception with a signal → ``transmitting``, so the pass was a miss;
* otherwise, at least ``min_silent_attempts`` attempts that heard nothing →
  ``silent``;
* otherwise → ``indeterminate``.

Standard library only, as :mod:`meridian.reliability.classification` is.

Reference: docs/DECISIONS.md D-147, D-180; docs/EVALUATION.md §5.
"""

from __future__ import annotations

from typing import Literal

__all__ = ["SIGNAL", "SatelliteState", "judge_satellite"]

SIGNAL = frozenset(("decoded", "signal_no_decode"))
"""Outcomes that prove a transmitter was on. Our vocabulary and the archive's
agree on these two."""

SatelliteState = Literal["transmitting", "silent", "indeterminate"]


def judge_satellite(
    *, signals: int, silences: int, min_silent_attempts: int
) -> SatelliteState:
    """Conclude from counted receptions whether the satellite was transmitting.

    Args:
        signals: Receptions in the window that heard the satellite.
        silences: Attempts in the window that heard nothing and count as
            evidence of silence: our own ``no_signal`` with listening
            confirmed, or an archive's ``no_data``.
        min_silent_attempts: How many silences it takes to call it silent.

    Returns:
        ``transmitting``, ``silent`` or ``indeterminate``.
    """
    if signals:
        return "transmitting"
    if silences >= min_silent_attempts:
        return "silent"
    return "indeterminate"
