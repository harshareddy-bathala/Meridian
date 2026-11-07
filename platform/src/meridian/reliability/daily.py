"""The network's figures by UTC day, from classified passes.

The dashboard's live page shows today's passes by what became of them, and a
week of capture rate and loss budget as small charts. Both are counted here, by
the same rules the reliability report uses: ``slis.capture_rate`` and
``budget.loss_budget``, applied to one day's passes at a time. Nothing here
restates what is captured or lost (D-182).

A pass belongs to the day its window closed, in UTC, as the report's window
counts window ends.

Pure: no database and no clock. ``meridian.reliability.aggregates`` reads the
rows and the time.

Reference: docs/DECISIONS.md D-182, D-184, D-185, D-362.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from meridian.reliability.budget import LossBudget, loss_budget
from meridian.reliability.classification import PASS_CLASSES
from meridian.reliability.slis import PassRecord, Proportion, capture_rate

__all__ = ["DayFigures", "class_counts", "day_bounds", "figures_by_day"]


@dataclass(frozen=True, slots=True)
class DayFigures:
    """One population's passes that closed on one UTC day."""

    day: date
    passes: int
    """Classified passes, every class."""

    capture: Proportion
    budget: LossBudget
    """The day's own budget: a tenth of its eligible passes at SC-4's target."""


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """``[00:00, 24:00)`` of a UTC day."""
    start = datetime.combine(day, time(), UTC)
    return start, start + timedelta(days=1)


def figures_by_day(
    passes: Iterable[PassRecord], *, days: Sequence[date], capture_target: float
) -> list[DayFigures]:
    """Each day's capture rate and loss budget, one entry per day asked for.

    Args:
        passes: One population's classified passes.
        days: The days to report, in the order to report them. A day with no
            pass is still reported, with nothing counted.
        capture_target: SC-4's target, which sets each day's budget.

    Returns:
        One entry per day in ``days``. A pass closing on another day is left out.
    """
    held: dict[date, list[PassRecord]] = {day: [] for day in days}
    for one in passes:
        bucket = held.get(one.window_end.astimezone(UTC).date())
        if bucket is not None:
            bucket.append(one)
    return [
        DayFigures(
            day=day,
            passes=len(held[day]),
            capture=capture_rate(held[day]),
            budget=loss_budget(held[day], capture_target=capture_target),
        )
        for day in days
    ]


def class_counts(passes: Iterable[PassRecord]) -> dict[str, int]:
    """Passes per class, every class present, in the order reports list them."""
    counts: dict[str, int] = dict.fromkeys(PASS_CLASSES, 0)
    for one in passes:
        counts[one.classification] += 1
    return counts
