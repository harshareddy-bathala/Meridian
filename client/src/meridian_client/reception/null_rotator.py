"""The rotator a fixed antenna has: none.

The funded station receives 137 MHz on a fixed QFH antenna (D-094), and Phase 1
schedules every station as fixed (D-066). So the only rotator that ships does
nothing, and a station that later adds tracking replaces this implementation
rather than the executor that calls it (D-126).

A station with this rotator never reports ``slewing``: there is nothing to slew.

Reference: docs/DECISIONS.md D-066, D-094, D-126.
"""

from __future__ import annotations

from meridian_client.reception.protocols import CapturePlan

__all__ = ["NullRotator"]


class NullRotator:
    """The ``RotatorController`` for an antenna that does not move."""

    def prepare(self, plan: CapturePlan) -> None:
        """Nothing to point: a fixed antenna is always as ready as it will be."""

    def release(self) -> None:
        """Nothing to release."""
