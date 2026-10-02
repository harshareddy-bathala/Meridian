"""Where a station's own passes lose signal they should have kept: its loss map.

The roadmap names a sector ``horizon_profiles`` marks obstructed. The learned
profile cannot mark a new obstruction: it records where a signal is first
*heard*, and a simulated station has no track to learn one from (D-158). So an
obstruction is read from where signal is *lost* (D-274).

**A sample is lost where it would have been heard** when it reads below the
detection bar while the same pass, on the other side of its culmination, was
heard at about the same elevation. Elevation sets how strong a pass is at a
station, so the same height on the pass's other side is the comparison that
needs no model of the link. A loss on both sides, as at the start and end of
every pass, is the pass being low, and is never counted.

**A sector is marked** when its lost samples come from enough distinct earlier
passes and make up enough of what the sector could have heard. It is marked up
to the highest elevation it lost a sample at. A pass whose floor was raised is
left out, because a raised floor explains its own losses (D-275).

Declared horizon bins mark too: a loss behind the station's own mask is an
obstruction it already knew about.

Standard library only (D-180).

Reference: docs/DECISIONS.md D-158, D-159, D-274, D-275.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from meridian.reliability.config import DiagnosisConfig
from meridian.reliability.diagnosis_evidence import (
    HistoryPass,
    HorizonFloor,
    SkySample,
)

__all__ = ["ObstructionMap", "build_map", "lost_where_heard"]


def lost_where_heard(
    samples: Sequence[SkySample], config: DiagnosisConfig
) -> tuple[int, ...]:
    """The samples lost where the same pass, mirrored, was heard.

    Args:
        samples: One pass's samples, in time order.
        config: The thresholds.

    Returns:
        The indices of every sample at or under the ceiling, below the bar,
        with a heard sample on the far side of culmination within the matching
        elevation.
    """
    if not samples:
        return ()
    peak = max(range(len(samples)), key=lambda i: samples[i].elevation_deg)
    lost = []
    for i, one in enumerate(samples):
        if (
            one.snr_db >= config.heard_snr_db
            or one.elevation_deg > config.obstruction_ceiling_deg
            or i == peak
        ):
            continue
        mirror = samples[peak + 1 :] if i < peak else samples[:peak]
        if any(
            other.snr_db >= config.heard_snr_db
            and abs(other.elevation_deg - one.elevation_deg)
            <= config.elevation_match_deg
            for other in mirror
        ):
            lost.append(i)
    return tuple(lost)


@dataclass(frozen=True, slots=True)
class ObstructionMap:
    """Each marked sector's ceiling, by sector index, and the declared floors."""

    sector_deg: float
    ceilings: dict[int, float]
    declared: tuple[HorizonFloor, ...] = ()

    def sector(self, azimuth_deg: float) -> int:
        """The index of the sector holding ``azimuth_deg``."""
        return int((azimuth_deg % 360.0) // self.sector_deg)

    def covers(self, azimuth_deg: float, elevation_deg: float) -> bool:
        """Whether a direction lies inside something the map marks."""
        ceiling = self.ceilings.get(self.sector(azimuth_deg))
        if ceiling is not None and elevation_deg <= ceiling:
            return True
        return any(
            (azimuth_deg - floor.azimuth_deg) % 360.0 < floor.width_deg
            and elevation_deg < floor.min_elevation_deg
            for floor in self.declared
        )

    def marked(self) -> list[dict[str, float]]:
        """The marked sectors, for a diagnosis's record."""
        return [
            {
                "azimuth_deg": round(index * self.sector_deg, 2),
                "width_deg": self.sector_deg,
                "ceiling_deg": round(ceiling, 2),
            }
            for index, ceiling in sorted(self.ceilings.items())
        ]


def build_map(
    history: Iterable[HistoryPass],
    *,
    baseline_dbfs: float | None,
    declared: Sequence[HorizonFloor],
    config: DiagnosisConfig,
) -> ObstructionMap:
    """Mark the sectors a station's own earlier passes lost signal in.

    Args:
        history: The station's earlier receptions in the lookback.
        baseline_dbfs: The station's usual floor at its usual gain; a pass more
            than the interference threshold above it is left out. ``None``
            when there is no baseline, and then no pass can be shown raised.
        declared: The station's declared horizon, as of the window.
        config: The thresholds.

    Returns:
        The map.
    """
    sector_deg = config.obstruction_sector_deg
    lost_by: defaultdict[int, set[int]] = defaultdict(set)
    lost_count: defaultdict[int, int] = defaultdict(int)
    heard_count: defaultdict[int, int] = defaultdict(int)
    highest: dict[int, float] = {}
    for number, one in enumerate(history):
        if _raised(one, baseline_dbfs, config):
            continue
        lost = set(lost_where_heard(one.samples, config))
        for i, sample in enumerate(one.samples):
            if sample.elevation_deg > config.obstruction_ceiling_deg:
                continue
            index = int((sample.azimuth_deg % 360.0) // sector_deg)
            if i in lost:
                lost_by[index].add(number)
                lost_count[index] += 1
                highest[index] = max(highest.get(index, 0.0), sample.elevation_deg)
            elif sample.snr_db >= config.heard_snr_db:
                heard_count[index] += 1
    ceilings = {
        index: highest[index]
        for index, passes in lost_by.items()
        if len(passes) >= config.obstruction_min_passes
        and lost_count[index] / (lost_count[index] + heard_count[index])
        >= config.obstruction_min_lost_share
    }
    return ObstructionMap(sector_deg, ceilings, tuple(declared))


def _raised(
    one: HistoryPass, baseline_dbfs: float | None, config: DiagnosisConfig
) -> bool:
    return (
        baseline_dbfs is not None
        and one.noise_floor_dbfs is not None
        and one.noise_floor_dbfs - baseline_dbfs >= config.interference_lift_db
    )
