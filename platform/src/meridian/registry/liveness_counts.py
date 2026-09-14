"""Stations counted by liveness, against one clock reading.

The platform's liveness gauge needs every station classified at once. Classifying
here, with :func:`meridian.registry.liveness.derive_liveness` and a single
``now``, keeps the thresholds in one place (D-054) and measures every station
against the same instant rather than a slightly later one per row.

Pure: no I/O, no clock of its own.

Reference: docs/DECISIONS.md D-013, D-054, D-111.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import get_args

from meridian.registry.liveness import Liveness, derive_liveness

__all__ = ["LIVENESS_VALUES", "count_by_liveness"]

LIVENESS_VALUES: tuple[Liveness, ...] = get_args(Liveness)
"""All four liveness values, taken from the type so the two cannot drift."""


def count_by_liveness(
    stations: Iterable[tuple[datetime | None, bool]], *, now: datetime
) -> dict[tuple[Liveness, bool], int]:
    """Count stations by ``(liveness, simulated)``.

    Args:
        stations: Each station's last heartbeat instant, or ``None`` if it never
            sent one, and whether it is simulated.
        now: The single instant every station is measured against,
            timezone-aware UTC.

    Returns:
        A count for every combination of the four liveness values and both
        values of ``simulated``, zero where no station has it — so "no station
        is offline" is a reported zero, not a missing series.
    """
    counts = {
        (liveness, simulated): 0
        for liveness in LIVENESS_VALUES
        for simulated in (False, True)
    }
    for last_heartbeat_at, simulated in stations:
        counts[(derive_liveness(last_heartbeat_at, now=now), simulated)] += 1
    return counts
