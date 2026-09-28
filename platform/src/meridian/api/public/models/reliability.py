"""The public reliability report, as a body a chart can read.

The same figures ``meridian reliability report`` prints.

Every figure is a count over a count, so a reader can check it; a figure the
platform cannot give is ``{"status": "not_measured", "reason": …}``, never a
zero or a ``null`` standing where a number would be (D-086). Measured and
simulated are two objects, each saying which it is (CLAUDE.md rule 5).

**Debits are published as counts by reason, not one by one.** A debit names a
pass and the instant its window closed, and a public list of exact windows is
what D-093 coarsens so that a schedule cannot be inverted into a station's
position. ``meridian reliability report`` and ``explain`` list them for an
operator (D-187).

Reference: docs/DECISIONS.md D-086, D-093, D-184, D-185, D-187.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from meridian.reliability.budget import LossBudget
from meridian.reliability.config import SloConfig
from meridian.reliability.report import (
    NotMeasured,
    PopulationReport,
    ReliabilityReport,
    StationReport,
    slo_results,
)
from meridian.reliability.slis import Delays, Proportion, Share

__all__ = ["PublicReliability"]


class NotMeasuredFigure(BaseModel):
    """A figure this platform cannot give yet, and why."""

    status: Literal["not_measured"] = "not_measured"
    reason: str

    @classmethod
    def of(cls, value: NotMeasured) -> NotMeasuredFigure:
        return cls(reason=value.reason)


class PublicProportion(BaseModel):
    """A count over a count, with its Wilson 95% interval where there is one."""

    numerator: int
    denominator: int
    estimate: float | None
    interval: tuple[float, float] | None

    @classmethod
    def of(cls, value: Proportion) -> PublicProportion:
        return cls(
            numerator=value.numerator,
            denominator=value.denominator,
            estimate=value.estimate,
            interval=value.interval,
        )


class PublicShare(BaseModel):
    """Seconds covered by heartbeats over seconds measured."""

    covered_s: float
    span_s: float
    estimate: float | None

    @classmethod
    def of(cls, value: Share | NotMeasured) -> PublicShare | NotMeasuredFigure:
        if isinstance(value, NotMeasured):
            return NotMeasuredFigure.of(value)
        return cls(
            covered_s=value.covered_s, span_s=value.span_s, estimate=value.estimate
        )


class PublicDelays(BaseModel):
    """Report delay after a window closed: median and 95th percentile."""

    n: int
    p50_s: float | None
    p95_s: float | None

    @classmethod
    def of(cls, value: Delays | NotMeasured) -> PublicDelays | NotMeasuredFigure:
        if isinstance(value, NotMeasured):
            return NotMeasuredFigure.of(value)
        return cls(n=value.n, p50_s=value.p50_s, p95_s=value.p95_s)


class PublicBudget(BaseModel):
    """The loss budget SC-4 sets, and what spent it, by reason (D-185)."""

    capture_target: float
    eligible: int
    allowed: float
    spent: int
    remaining: float
    remaining_ratio: float | None
    exhausted: bool
    by_reason: dict[str, int]
    """Only ``confirmed_miss`` is a miss; the rest were lost for other reasons."""

    @classmethod
    def of(cls, value: LossBudget) -> PublicBudget:
        return cls(
            capture_target=value.capture_target,
            eligible=value.eligible,
            allowed=value.allowed,
            spent=value.spent,
            remaining=value.remaining,
            remaining_ratio=value.remaining_ratio,
            exhausted=value.exhausted,
            by_reason=value.by_reason(),
        )


class PublicTarget(BaseModel):
    """One figure judged against its target."""

    name: str
    target: float
    at_least: bool
    value: float | None
    met: bool | None
    claim: str | None
    """``SC-4`` or ``SC-5``; null for a target proposed, to agree with the team."""


class PublicStationReliability(BaseModel):
    """One station's capture, budget and availability."""

    station_id: str
    capture: PublicProportion
    budget: PublicBudget
    availability: PublicShare | NotMeasuredFigure

    @classmethod
    def of(cls, value: StationReport) -> PublicStationReliability:
        return cls(
            station_id=value.station_id,
            capture=PublicProportion.of(value.capture),
            budget=PublicBudget.of(value.budget),
            availability=PublicShare.of(value.availability),
        )


class PublicPopulation(BaseModel):
    """Every figure for measured stations, or for simulated ones."""

    simulated: bool
    passes: int
    capture_rate: PublicProportion
    confirmed_miss_rate: PublicProportion
    assignment_completion_rate: PublicProportion
    schedule_execution_rate: PublicProportion
    station_availability: PublicShare | NotMeasuredFigure
    submission_delay: PublicDelays | NotMeasuredFigure
    loss_budget: PublicBudget
    targets: list[PublicTarget]
    stations: list[PublicStationReliability]

    @classmethod
    def of(cls, value: PopulationReport, slo: SloConfig) -> PublicPopulation:
        return cls(
            simulated=value.simulated,
            passes=value.passes,
            capture_rate=PublicProportion.of(value.capture),
            confirmed_miss_rate=PublicProportion.of(value.confirmed_miss),
            assignment_completion_rate=PublicProportion.of(value.completion),
            schedule_execution_rate=PublicProportion.of(value.execution),
            station_availability=PublicShare.of(value.availability),
            submission_delay=PublicDelays.of(value.submission_delay),
            loss_budget=PublicBudget.of(value.budget),
            targets=[
                PublicTarget(
                    name=one.name,
                    target=one.target,
                    at_least=one.at_least,
                    value=one.value,
                    met=one.met,
                    claim=one.claim,
                )
                for one in slo_results(value, slo)
            ],
            stations=[PublicStationReliability.of(one) for one in value.stations],
        )


class PublicReliability(BaseModel):
    """``/api/v1/reliability``: both populations over the SLO window."""

    status: Literal["computed"] = "computed"
    window_start: datetime
    window_end: datetime
    """Passes whose windows closed in ``[window_start, window_end)``. A pass is
    classified a day after its window, so the most recent day is not in it."""

    method: str
    classification_sha256: str
    measured: PublicPopulation
    simulated: PublicPopulation
    failure_detection: NotMeasuredFigure

    @classmethod
    def of(cls, report: ReliabilityReport) -> PublicReliability:
        """The report, as it is published."""
        start, end = report.window
        return cls(
            window_start=start,
            window_end=end,
            method=report.method,
            classification_sha256=report.classification_sha256,
            measured=PublicPopulation.of(report.measured, report.slo),
            simulated=PublicPopulation.of(report.simulated, report.slo),
            failure_detection=NotMeasuredFigure.of(report.failure_detection),
        )
