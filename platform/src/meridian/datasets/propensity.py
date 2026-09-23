"""How likely the historical policy was to attempt a pass — D-152, by counting.

``EVALUATION.md`` §4.2 weights outcomes by the inverse of P(attempted | what
was known before the pass). Stage 17 owns the numerical dependencies, and a
propensity that a viva can check by eye is worth more here than a smooth one,
so this estimate is a ratio of two counts.

**A cell is station × satellite × maximum-elevation band × local-solar-hour
band.** Its propensity is the share of its available passes that were
attempted. A cell with fewer than ``min_cell`` passes falls back, in order:

1. ``station_satellite_elevation_hour`` — the cell itself;
2. ``station_satellite_elevation`` — the hour band dropped;
3. ``station_elevation`` — the satellite dropped too. This last level is used
   however few passes it holds, and every estimate says which level it came
   from and how many passes stood behind it.

Local solar hour is UTC plus longitude ÷ 15, so it needs no timezone database
and moves with the sun rather than a government. A pass whose station has no
longitude skips the first level.

**A candidate carries no outcome.** :class:`Candidate` has the pre-pass
features and whether the pass was attempted — the policy's decision — and no
label, report or reception. What happened on the pass cannot reach its
propensity, because there is no field for it to arrive in (D-152).

**Behind a protocol.** :class:`PropensityModel` is what the weighting reads,
so Stage 17 can add a fitted model beside this one. This one stays as the
baseline any fitted one is compared with.

Reference: docs/DECISIONS.md D-148, D-152.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from meridian.datasets.selection_config import PropensityConfig

__all__ = [
    "LEVELS",
    "BinnedPropensity",
    "Candidate",
    "Estimate",
    "PropensityModel",
]

LEVELS = (
    "station_satellite_elevation_hour",
    "station_satellite_elevation",
    "station_elevation",
)
"""Finest first. The last is used however sparse it is."""

_MINUTES_PER_DAY = 1440
_MINUTES_PER_DEGREE = 4
"""The sun crosses 15° of longitude an hour: four minutes a degree."""

_ZENITH_DEG = 90


@dataclass(frozen=True, slots=True)
class Candidate:
    """An eligible pass as the policy saw it before the pass, and what it did."""

    population: str
    station: str
    satellite_id: str
    aos: datetime
    max_elevation_deg: float
    longitude_deg: float | None
    """The station's; None where it is not known, which skips the hour level."""

    attempted: bool
    """The policy's decision, not the pass's outcome."""


@dataclass(frozen=True, slots=True)
class Estimate:
    """One candidate's propensity, and the cell that stood behind it."""

    candidate: Candidate
    level: str
    cell: tuple[str, ...]
    available: int
    attempted: int

    @property
    def propensity(self) -> float:
        """Attempted over available in the cell used."""
        return self.attempted / self.available


class PropensityModel(Protocol):
    """What the weighting needs of any propensity estimate."""

    @property
    def name(self) -> str:
        """Recorded beside every estimate, so two models are never confused."""
        ...

    def estimate(self, candidates: Sequence[Candidate]) -> tuple[Estimate, ...]:
        """One estimate per candidate, in the order given."""
        ...


@dataclass(frozen=True, slots=True)
class BinnedPropensity:
    """D-152's estimate: observed over available within cells, with fallback."""

    config: PropensityConfig

    @property
    def name(self) -> str:
        """The model and its version."""
        return "binned-1"

    def estimate(self, candidates: Sequence[Candidate]) -> tuple[Estimate, ...]:
        """Count every cell at every level once, then give each candidate its own.

        Args:
            candidates: Every eligible pass of one or both populations. A
                station's population is part of its cell, so the two are
                never counted together.

        Returns:
            One estimate per candidate, in the order given.
        """
        keys = [self._keys(one) for one in candidates]
        available: Counter[tuple[str, ...]] = Counter()
        attempted: Counter[tuple[str, ...]] = Counter()
        for candidate, cells in zip(candidates, keys, strict=True):
            for cell in cells:
                if cell is None:
                    continue
                available[cell] += 1
                attempted[cell] += candidate.attempted
        return tuple(
            self._chosen(candidate, cells, available, attempted)
            for candidate, cells in zip(candidates, keys, strict=True)
        )

    def _chosen(
        self,
        candidate: Candidate,
        cells: tuple[tuple[str, ...] | None, ...],
        available: Counter[tuple[str, ...]],
        attempted: Counter[tuple[str, ...]],
    ) -> Estimate:
        """The finest cell holding ``min_cell`` passes, else the coarsest."""
        usable = [
            (level, cell)
            for level, cell in zip(LEVELS, cells, strict=True)
            if cell is not None
        ]
        level, cell = next(
            (
                (level, cell)
                for level, cell in usable
                if available[cell] >= self.config.min_cell
            ),
            usable[-1],
        )
        return Estimate(
            candidate=candidate,
            level=level,
            cell=cell,
            available=available[cell],
            attempted=attempted[cell],
        )

    def _keys(self, one: Candidate) -> tuple[tuple[str, ...] | None, ...]:
        """The candidate's cell at each level; None where a level cannot apply."""
        station = (one.population, one.station)
        elevation = self._elevation_band(one.max_elevation_deg)
        hour = (
            None
            if one.longitude_deg is None
            else self._hour_band(one, one.longitude_deg)
        )
        return (
            None if hour is None else (*station, one.satellite_id, elevation, hour),
            (*station, one.satellite_id, elevation),
            (*station, elevation),
        )

    def _elevation_band(self, peak_deg: float) -> str:
        edges = self.config.elevation_bands_deg
        index = bisect_right(edges, peak_deg)
        low = 0.0 if index == 0 else edges[index - 1]
        high = float(_ZENITH_DEG) if index == len(edges) else edges[index]
        return f"el {low:g}-{high:g}"

    def _hour_band(self, one: Candidate, longitude_deg: float) -> str:
        utc_minutes = one.aos.hour * 60 + one.aos.minute + one.aos.second / 60
        local = (utc_minutes + longitude_deg * _MINUTES_PER_DEGREE) % _MINUTES_PER_DAY
        width = self.config.hour_band_h
        start = int(local // (width * 60)) * width
        return f"lst {start:02d}-{start + width:02d}"
