"""The network's live aggregates, as the public API states them.

Shapes :class:`~meridian.reliability.aggregates.NetworkAggregates`: today's
passes by what became of them, and a week of capture rate and loss budget by
day, for measured and simulated stations apart (rule 5). The rates and budgets
are the reliability report's own shapes, so a day's figure reads like the
thirty-day one on ``/reliability``.

**Counts, never a list of passes.** No pass, window or station is named, so
nothing here can be inverted into a schedule or a position (D-093, D-187).

**A zero here is a count.** Each figure counts rows that exist, so no pass
classified today is ``0``, not an absence. Where a rate has nothing to divide,
its estimate is ``null``, as on ``/reliability`` (D-086).

Reference: docs/DECISIONS.md D-184, D-185, D-187, D-362.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel

from meridian.api.public.models.reliability import PublicBudget, PublicProportion
from meridian.reliability.aggregates import (
    NetworkAggregates,
    PopulationAggregates,
    SettledDay,
    Today,
)

__all__ = ["PublicAggregates", "PublicDay", "PublicPopulationAggregates", "PublicToday"]


class PublicToday(BaseModel):
    """One population's passes closing on the UTC day so far."""

    day: date
    scheduled: int
    """Every pass scheduled to close today, including those still to come."""

    ended: int
    """Of those, the ones whose window has closed."""

    classified: int
    by_class: dict[str, int]
    """Classified passes per class, every class present."""

    listening_confirmed: int
    """Classified passes with a station confirmed listening."""

    awaiting: int
    """Closed and not classified yet: a pass is classified only once the settle
    margin has passed, so a report still on its way is not read as absence."""

    @classmethod
    def of(cls, value: Today) -> Self:
        """Shape one Today for the wire."""
        return cls(
            day=value.day,
            scheduled=value.scheduled,
            ended=value.ended,
            classified=value.classified,
            by_class=value.by_class,
            listening_confirmed=value.listening_confirmed,
            awaiting=value.awaiting,
        )


class PublicDay(BaseModel):
    """One UTC day's capture rate and loss budget."""

    day: date
    settled: bool
    """Whether the day's last window closed more than the settle margin ago.
    An unsettled day's figures can still change."""

    passes: int
    capture_rate: PublicProportion
    loss_budget: PublicBudget

    @classmethod
    def of(cls, value: SettledDay) -> Self:
        """Shape one SettledDay for the wire."""
        return cls(
            day=value.figures.day,
            settled=value.settled,
            passes=value.figures.passes,
            capture_rate=PublicProportion.of(value.figures.capture),
            loss_budget=PublicBudget.of(value.figures.budget),
        )


class PublicPopulationAggregates(BaseModel):
    """Measured or simulated stations' aggregates, never pooled."""

    simulated: bool
    today: PublicToday
    days: list[PublicDay]
    """Seven UTC days, oldest first, today last."""

    @classmethod
    def of(cls, value: PopulationAggregates) -> Self:
        """Shape one PopulationAggregates for the wire."""
        return cls(
            simulated=value.simulated,
            today=PublicToday.of(value.today),
            days=[PublicDay.of(one) for one in value.days],
        )


class PublicAggregates(BaseModel):
    """The network's live aggregates."""

    status: Literal["computed"] = "computed"
    as_of: datetime
    method: str
    """The classification method the passes were classified under."""

    config_sha256: str
    """The classification configuration's hash, as on ``/reliability``."""

    settle_margin_s: int
    capture_target: float
    measured: PublicPopulationAggregates
    simulated: PublicPopulationAggregates

    @classmethod
    def of(cls, value: NetworkAggregates) -> Self:
        """Shape one NetworkAggregates for the wire."""
        return cls(
            as_of=value.as_of,
            method=value.method,
            config_sha256=value.config_sha256,
            settle_margin_s=value.settle_margin_s,
            capture_target=value.capture_target,
            measured=PublicPopulationAggregates.of(value.measured),
            simulated=PublicPopulationAggregates.of(value.simulated),
        )
