"""The declared horizon: an obstruction the operator already knows about.

A capability may declare a mask, a list of ``{az_deg, min_el_deg}`` points
(D-031). It is the one horizon that constrains scheduling (D-175). The learned
horizon reaches the scheduler as a feature instead, because a learned floor
used as a constraint could never come down: a sector nobody is sent to is a
sector nobody hears.

**How a mask is read.** It is a step function. Each point's floor holds from
its azimuth, clockwise, to the next point's, wrapping at 360°. So a lone point
holds all the way round. Two points at one azimuth keep the higher floor, the
reading that promises less.

**What clearing means.** A pass clears a mask if any sample of its track is
above the floor at that sample's azimuth. An empty mask constrains nothing, so a
station that declares none schedules exactly as it did before.

A leaf: no I/O and no clock.

Reference: docs/DECISIONS.md D-031, D-175.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

__all__ = ["DeclaredMask", "clears_any"]


@dataclass(frozen=True, slots=True)
class DeclaredMask:
    """One capability's declared mask, as a step function of azimuth."""

    azimuths: tuple[float, ...]
    """Where each step begins, ascending, every one in ``[0, 360)``."""

    floors: tuple[float, ...]
    """The floor from each azimuth to the next."""

    @classmethod
    def from_stored(cls, points: Sequence[Mapping[str, float]]) -> DeclaredMask:
        """The mask as ``station_capabilities.horizon_mask_json`` holds it.

        Args:
            points: ``{"az_deg": …, "min_el_deg": …}``, in any order.
        """
        highest: dict[float, float] = {}
        for point in points:
            azimuth = float(point["az_deg"]) % 360.0
            floor = float(point["min_el_deg"])
            highest[azimuth] = max(floor, highest.get(azimuth, floor))
        ordered = sorted(highest.items())
        return cls(
            azimuths=tuple(azimuth for azimuth, _ in ordered),
            floors=tuple(floor for _, floor in ordered),
        )

    @property
    def empty(self) -> bool:
        """Whether this mask declares nothing, and so constrains nothing."""
        return not self.azimuths

    def floor_at(self, azimuth_deg: float) -> float:
        """The declared floor at one azimuth.

        Raises:
            ValueError: The mask is empty, and has no floor anywhere.
        """
        if self.empty:
            raise ValueError("an empty mask declares no floor")
        index = bisect_right(self.azimuths, azimuth_deg % 360.0) - 1
        # Before the first point, the last point's step is still in force: it
        # runs clockwise past 360° and round to the first.
        return self.floors[index]

    def clears(self, track: Iterable[tuple[float, float]]) -> bool:
        """Whether any ``(azimuth, elevation)`` sample is above the floor."""
        if self.empty:
            return True
        return any(elevation > self.floor_at(azimuth) for azimuth, elevation in track)


def clears_any(
    masks: Sequence[DeclaredMask], track: Sequence[tuple[float, float]]
) -> bool:
    """Whether some chain that could receive the pass sees it over its mask.

    Args:
        masks: The masks of every capability that covers the pass's downlink.
        track: The pass's samples, as ``(azimuth, elevation)``.

    Returns:
        True when there is no mask to apply or the track clears one of them. A
        station with two antennas is constrained only where both are blocked.
    """
    if not masks or any(one.empty for one in masks):
        return True
    return any(one.clears(track) for one in masks)
