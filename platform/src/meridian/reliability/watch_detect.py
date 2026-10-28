"""A receive chain's shortfall against its baseline, and whether it warns.

The rules alone, on plain values; :mod:`~meridian.reliability.watch_run`
gathers the receptions and writes the warnings.

**A reception's shortfall** is how far its own median signal in each bin falls
below the baseline's median there, reduced to the median across the bins it
crossed (D-294). A reception that crossed fewer than ``min_bins`` of the
baseline's bins is not judged: a low pass seen at one elevation says too little.

**A chain's shortfall is the median of its latest receptions'**, three by
default. One bad pass cannot move the median of three; two can. That is the
roadmap's "a persistent shortfall, not a single bad pass", and a step loss
raises a warning on its second reception.

**A warning needs the shortfall across elevations.** Each bin's shortfall is
the median of the recent receptions that crossed it, and at least ``min_bins``
bins must be at or past ``raise_db``. A loss in one sector of sky at one
elevation is an obstruction's shape, which Stage 27 names, not a chain's.

**It clears at a lower bar than it raises**, ``clear_db``, so a chain hovering
at the threshold does not raise and clear by turns.

Reference: docs/DECISIONS.md D-184, D-292, D-294.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from meridian.reliability.config import WatchConfig
from meridian.reliability.watch_baseline import nearest_rank

__all__ = [
    "METHOD",
    "Judgement",
    "ReceptionShortfall",
    "compare",
    "judge",
]

METHOD = "watch-1"
"""The detection method, recorded with every warning."""


@dataclass(frozen=True, slots=True)
class ReceptionShortfall:
    """One reception against the baseline."""

    assignment_id: str
    started_at: datetime
    end_at: datetime
    by_bin: dict[float, float]
    """The baseline's median less the reception's, by bin, in decibels."""

    shortfall_db: float
    """The median of :attr:`by_bin`."""


@dataclass(frozen=True, slots=True)
class Judgement:
    """A chain's recent receptions, judged."""

    shortfall_db: float
    affected: list[dict[str, object]]
    """Every bin whose shortfall reached ``raise_db``, with how many receptions
    crossed it, as ``affected_bins_json`` stores them."""

    raises: bool
    clears: bool
    receptions: tuple[ReceptionShortfall, ...]


def compare(  # noqa: PLR0913 — one reception's identity and signal, each by name
    bins: dict[float, tuple[float, int]],
    baseline: dict[float, float],
    config: WatchConfig,
    *,
    assignment_id: str,
    started_at: datetime,
    end_at: datetime,
) -> ReceptionShortfall | None:
    """One reception's shortfall, or ``None`` when it crossed too few bins.

    Args:
        bins: Its median signal by bin, from ``reception_bins``.
        baseline: The baseline's median signal by bin.
        config: The watch's settings.
        assignment_id: The reception's assignment.
        started_at: When it began.
        end_at: When its window ended.
    """
    common = sorted(edge for edge in bins if edge in baseline)
    if len(common) < config.min_bins:
        return None
    by_bin = {edge: round(baseline[edge] - bins[edge][0], 3) for edge in common}
    return ReceptionShortfall(
        assignment_id=assignment_id,
        started_at=started_at,
        end_at=end_at,
        by_bin=by_bin,
        shortfall_db=round(nearest_rank(list(by_bin.values()), 0.5), 3),
    )


def judge(
    recent: Sequence[ReceptionShortfall], config: WatchConfig
) -> Judgement | None:
    """Judge the chain on its latest ``recent_receptions`` receptions.

    Args:
        recent: The chain's judged receptions, oldest first.
        config: The watch's settings.

    Returns:
        The judgement, or ``None`` with too few receptions to judge.
    """
    if len(recent) < config.recent_receptions:
        return None
    latest = tuple(recent[-config.recent_receptions :])
    shortfall = round(nearest_rank([one.shortfall_db for one in latest], 0.5), 3)
    crossed: dict[float, list[float]] = defaultdict(list)
    for one in latest:
        for edge, value in one.by_bin.items():
            crossed[edge].append(value)
    affected: list[dict[str, object]] = [
        {
            "elevation_deg": edge,
            "shortfall_db": round(nearest_rank(values, 0.5), 3),
            "receptions": len(values),
        }
        for edge, values in sorted(crossed.items())
        if nearest_rank(values, 0.5) >= config.raise_db
    ]
    return Judgement(
        shortfall_db=shortfall,
        affected=affected,
        raises=shortfall >= config.raise_db and len(affected) >= config.min_bins,
        clears=shortfall <= config.clear_db,
        receptions=latest,
    )
