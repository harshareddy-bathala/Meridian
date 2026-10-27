"""A receive chain's baseline: its own signal at each elevation, from its past.

The rules alone, on plain values; :mod:`~meridian.reliability.watch_build`
gathers the readings and writes the result.

**Each reception is one vote per bin** (D-293). A reception's samples in one
elevation bin are reduced to their median first, and the bin's baseline is
taken across receptions. A pass that spent four minutes at 20° and one that
crossed it in forty seconds then count once each. Samples from one pass are not
independent of each other, and counting them as if they were would let a few
long passes stand for a chain's whole history.

**Median and tenth percentile, by nearest rank**, as every figure here is
counted (D-184). The median is what a shortfall is measured from; the tenth
percentile says how low a normal pass already goes.

**A bin with too few receptions is left out, and a baseline with too few bins
is not written.** A shortfall is judged across elevations, never in one bin.

Reference: docs/DECISIONS.md D-184, D-290, D-291, D-293.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from meridian.reliability.config import BaselineConfig

__all__ = [
    "EXCLUDED_CAUSES",
    "METHOD",
    "Bin",
    "Reading",
    "bin_of",
    "nearest_rank",
    "reception_bins",
    "train",
]

METHOD = "baseline-1"
"""The training method, recorded with every baseline."""

EXCLUDED_CAUSES = ("obstruction", "interference", "satellite_silent")
"""Stage 27's causes that are not the receive chain's (D-290)."""


@dataclass(frozen=True, slots=True)
class Reading:
    """Signal heard at one elevation."""

    elevation_deg: float
    snr_db: float


@dataclass(frozen=True, slots=True)
class Bin:
    """One elevation bin of a baseline."""

    elevation_deg: float
    """The bin's lower edge."""

    median_db: float
    p10_db: float
    receptions: int
    samples: int

    def as_json(self) -> dict[str, object]:
        """The bin as ``bins_json`` stores it."""
        return {
            "elevation_deg": self.elevation_deg,
            "median_db": self.median_db,
            "p10_db": self.p10_db,
            "receptions": self.receptions,
            "samples": self.samples,
        }


def bin_of(elevation_deg: float, width_deg: float) -> float:
    """The lower edge of the bin an elevation falls in."""
    return round(math.floor(elevation_deg / width_deg) * width_deg, 6)


def nearest_rank(values: list[float], share: float) -> float:
    """The ``share`` quantile of ``values`` by nearest rank.

    Raises:
        ValueError: ``values`` is empty.
    """
    if not values:
        raise ValueError("no values")
    ordered = sorted(values)
    return ordered[max(math.ceil(share * len(ordered)) - 1, 0)]


def reception_bins(
    readings: Iterable[Reading], config: BaselineConfig
) -> dict[float, tuple[float, int]]:
    """One reception's median signal in each bin, and how many samples made it.

    A reading below ``min_elevation_deg`` is left out.
    """
    held: dict[float, list[float]] = defaultdict(list)
    for one in readings:
        if one.elevation_deg >= config.min_elevation_deg:
            held[bin_of(one.elevation_deg, config.bin_width_deg)].append(one.snr_db)
    return {
        edge: (nearest_rank(values, 0.5), len(values))
        for edge, values in sorted(held.items())
    }


def train(
    receptions: Iterable[dict[float, tuple[float, int]]], config: BaselineConfig
) -> list[Bin]:
    """A baseline's bins from its receptions' :func:`reception_bins`.

    Returns:
        The bins with at least ``min_receptions`` receptions, lowest first, or
        nothing when fewer than ``min_bins`` qualify.
    """
    votes: dict[float, list[float]] = defaultdict(list)
    samples: dict[float, int] = defaultdict(int)
    for one in receptions:
        for edge, (median, count) in one.items():
            votes[edge].append(median)
            samples[edge] += count
    bins = [
        Bin(
            elevation_deg=edge,
            median_db=round(nearest_rank(values, 0.5), 3),
            p10_db=round(nearest_rank(values, 0.1), 3),
            receptions=len(values),
            samples=samples[edge],
        )
        for edge, values in sorted(votes.items())
        if len(values) >= config.min_receptions
    ]
    return bins if len(bins) >= config.min_bins else []
