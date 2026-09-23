"""Predictions of one rise, grouped into the physical pass they predict — D-148.

``passes`` keeps every prediction (D-063): a newer element set predicting the
same rise is a second row, on purpose, because the series is the uncertainty
model's input. A label, a completeness ratio or a propensity is about the rise
itself, and counting predictions instead would score one pass scheduled once
as one taken and one refused.

**Predictions of the same station and satellite whose ``[aos, los)`` windows
overlap are one physical pass**, transitively. Overlap rather than equal
``aos``, because two element sets put the same acquisition seconds apart and
neither is the true one.

**The representative is the newest prediction available before the pass**:
of the members computed no later than the group's earliest ``aos``, the one
whose element-set epoch is latest, lowest ``pass_id`` on a tie. Where every
member was computed after the rise, the one computed first is used.
Availability is when the prediction was made, not its elements' epoch: sets
are published hours after their epoch, so an epoch before the pass can still be
knowledge from after it. The representative's geometry is what completeness
and the propensity read, so neither sees a prediction made after the pass.

Reference: docs/DECISIONS.md D-063, D-148.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from meridian.datasets.snapshot_rows import PassRow

__all__ = ["PhysicalPass", "group_physical_passes"]


@dataclass(frozen=True, slots=True)
class PhysicalPass:
    """One rise of one satellite over one station, and every prediction of it."""

    representative: PassRow
    members: tuple[PassRow, ...]
    """In pass-id order; the representative is one of them."""

    @property
    def pass_ids(self) -> tuple[int, ...]:
        """Every member's id, sorted."""
        return tuple(one.pass_id for one in self.members)

    @property
    def first_aos(self) -> datetime:
        """The earliest acquisition any member predicts: when the rise begins."""
        return min(one.aos for one in self.members)

    @property
    def last_los(self) -> datetime:
        """The latest loss of signal any member predicts."""
        return max(one.los for one in self.members)

    @property
    def simulated(self) -> bool:
        """Whether any prediction of the rise is simulated."""
        return any(one.simulated for one in self.members)


def group_physical_passes(passes: Iterable[PassRow]) -> tuple[PhysicalPass, ...]:
    """Group predictions into physical passes.

    Args:
        passes: Every prediction, in any order.

    Returns:
        One physical pass per rise, in the representative's pass-id order.
    """
    by_pair: dict[tuple[str, str], list[PassRow]] = {}
    for one in passes:
        by_pair.setdefault((one.station_id, one.satellite_id), []).append(one)
    grouped = [
        _physical(members)
        for predictions in by_pair.values()
        for members in _overlapping(predictions)
    ]
    return tuple(sorted(grouped, key=lambda one: one.representative.pass_id))


def _overlapping(predictions: list[PassRow]) -> list[list[PassRow]]:
    """Sweep in acquisition order; a window starting before the group ends joins it.

    Half-open windows: one that starts exactly when the group ends is the next
    rise, not this one.
    """
    groups: list[list[PassRow]] = []
    ends: datetime | None = None
    for one in sorted(predictions, key=lambda one: (one.aos, one.pass_id)):
        if ends is not None and one.aos < ends:
            groups[-1].append(one)
            ends = max(ends, one.los)
        else:
            groups.append([one])
            ends = one.los
    return groups


def _physical(members: list[PassRow]) -> PhysicalPass:
    rises = min(one.aos for one in members)
    before = [one for one in members if one.computed_at <= rises]
    if before:
        representative = max(
            before, key=lambda one: (one.element_set_epoch, -one.pass_id)
        )
    else:
        representative = min(members, key=lambda one: (one.computed_at, one.pass_id))
    return PhysicalPass(
        representative=representative,
        members=tuple(sorted(members, key=lambda one: one.pass_id)),
    )
