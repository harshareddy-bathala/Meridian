"""One reliability report, from classified passes, however they were read.

The live command reads its passes from ``pass_classifications``, and the
snapshot command reads them from a dataset's ``labels.jsonl``. Both hand them
here with whatever else their source holds: availability needs heartbeats held
whole, and submission delay needs each report's arrival time. A source that
lacks one says why, and the report prints that reason instead of a number
(D-086's rule: an absent figure is never a zero).

**Measured and simulated are two reports that never meet** (CLAUDE.md rule 5).
Each has its own indicators, its own budget and its own stations.

Standard library only, as the classification is (D-180).

Reference: docs/DECISIONS.md D-086, D-180, D-184, D-185.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from meridian.reliability.budget import LossBudget, loss_budget
from meridian.reliability.config import SloConfig
from meridian.reliability.slis import (
    Delays,
    PassRecord,
    Proportion,
    Share,
    assignment_completion_rate,
    capture_rate,
    confirmed_miss_rate,
    delays,
    schedule_execution_rate,
)

__all__ = [
    "FAILURE_DETECTION_NOT_MEASURED",
    "NotMeasured",
    "PopulationReport",
    "ReliabilityReport",
    "SloResult",
    "StationReport",
    "build_report",
    "report_lines",
    "slo_results",
]

FAILURE_DETECTION_NOT_MEASURED = (
    "time to detect is measured from an injected failure's own instant, which "
    "Stage 21's fault records supply; from heartbeats alone it would only "
    "restate the 90 s offline threshold"
)


@dataclass(frozen=True, slots=True)
class NotMeasured:
    """A figure this source cannot give, and why."""

    reason: str


@dataclass(frozen=True, slots=True)
class StationReport:
    """One station's share of its population's figures."""

    station_id: str
    capture: Proportion
    budget: LossBudget
    availability: Share | NotMeasured


@dataclass(frozen=True, slots=True)
class PopulationReport:
    """Every figure for one population: measured, or simulated."""

    simulated: bool
    passes: int
    capture: Proportion
    confirmed_miss: Proportion
    completion: Proportion
    execution: Proportion
    availability: Share | NotMeasured
    submission_delay: Delays | NotMeasured
    budget: LossBudget
    stations: tuple[StationReport, ...]


@dataclass(frozen=True, slots=True)
class ReliabilityReport:
    """Both populations over one window, with what they were counted under."""

    source: str
    """``live`` or ``snapshot <hash>``."""

    window: tuple[datetime, datetime]
    """Passes whose windows closed inside ``[start, end)``."""

    method: str
    classification_sha256: str
    slo: SloConfig
    measured: PopulationReport
    simulated: PopulationReport
    failure_detection: NotMeasured


@dataclass(frozen=True, slots=True)
class SloResult:
    """One figure judged against its target."""

    name: str
    target: float
    at_least: bool
    """True if the figure must reach the target; False if it must not pass it."""

    value: float | None
    claim: str | None
    """``SC-4`` or ``SC-5`` for the project's claims; None for a proposed target."""

    @property
    def met(self) -> bool | None:
        """Whether the target is met; None where there is no figure."""
        if self.value is None:
            return None
        return self.value >= self.target if self.at_least else self.value <= self.target


@dataclass(frozen=True, slots=True)
class _Source:
    """What a source holds beyond its passes, per population."""

    availability: Callable[[bool, str | None], Share | NotMeasured]
    submission_delays: Callable[[bool], Sequence[float] | NotMeasured]


def build_report(
    passes: Iterable[PassRecord],
    *,
    header: tuple[str, tuple[datetime, datetime], str, str],
    slo: SloConfig,
    availability: Mapping[str, tuple[bool, Share]] | NotMeasured,
    submission_delays: Mapping[bool, Sequence[float]] | NotMeasured,
) -> ReliabilityReport:
    """Assemble both populations' reports.

    Args:
        passes: Every classified pass inside the window, both populations.
        header: The source, the window, the classification method and the
            classification configuration's hash, as the report states them.
        slo: The window and targets.
        availability: Each station's population and seconds covered, or why
            this source cannot say.
        submission_delays: Each population's report delays in seconds, or why
            this source cannot say.

    Returns:
        The report.
    """
    source, window, method, classification_sha256 = header
    held = list(passes)

    def station_share(simulated: bool, station: str | None) -> Share | NotMeasured:
        if isinstance(availability, NotMeasured):
            return availability
        shares = [
            share
            for name, (population, share) in availability.items()
            if population == simulated and (station is None or name == station)
        ]
        if not shares:
            return NotMeasured("no registered station of this population")
        total = shares[0]
        for share in shares[1:]:
            total = total + share
        return total

    def population_delays(simulated: bool) -> Sequence[float] | NotMeasured:
        if isinstance(submission_delays, NotMeasured):
            return submission_delays
        return submission_delays.get(simulated, ())

    lookups = _Source(station_share, population_delays)
    return ReliabilityReport(
        source=source,
        window=window,
        method=method,
        classification_sha256=classification_sha256,
        slo=slo,
        measured=_population(held, False, slo, lookups),
        simulated=_population(held, True, slo, lookups),
        failure_detection=NotMeasured(FAILURE_DETECTION_NOT_MEASURED),
    )


def _population(
    passes: Sequence[PassRecord], simulated: bool, slo: SloConfig, source: _Source
) -> PopulationReport:
    mine = [one for one in passes if one.simulated == simulated]
    target = slo.capture_rate_min
    stations = sorted({one.station_id for one in mine})
    recorded = source.submission_delays(simulated)
    return PopulationReport(
        simulated=simulated,
        passes=len(mine),
        capture=capture_rate(mine),
        confirmed_miss=confirmed_miss_rate(mine),
        completion=assignment_completion_rate(mine),
        execution=schedule_execution_rate(mine),
        availability=source.availability(simulated, None),
        submission_delay=(
            recorded if isinstance(recorded, NotMeasured) else delays(recorded)
        ),
        budget=loss_budget(mine, capture_target=target),
        stations=tuple(
            StationReport(
                station_id=station,
                capture=capture_rate(one for one in mine if one.station_id == station),
                budget=loss_budget(
                    (one for one in mine if one.station_id == station),
                    capture_target=target,
                ),
                availability=source.availability(simulated, station),
            )
            for station in stations
        ),
    )


def slo_results(population: PopulationReport, slo: SloConfig) -> tuple[SloResult, ...]:
    """Each figure of one population judged against its target."""
    availability = population.availability
    delay = population.submission_delay
    return (
        SloResult(
            "pass capture rate",
            slo.capture_rate_min,
            True,
            population.capture.estimate,
            "SC-4",
        ),
        SloResult(
            "confirmed miss rate",
            slo.confirmed_miss_rate_max,
            False,
            population.confirmed_miss.estimate,
            None,
        ),
        SloResult(
            "station availability",
            slo.station_availability_min,
            True,
            None if isinstance(availability, NotMeasured) else availability.estimate,
            None,
        ),
        SloResult(
            "assignment completion rate",
            slo.assignment_completion_rate_min,
            True,
            population.completion.estimate,
            None,
        ),
        SloResult(
            "schedule execution rate",
            slo.schedule_execution_rate_min,
            True,
            population.execution.estimate,
            None,
        ),
        SloResult(
            "submission delay p95 (s)",
            slo.submission_delay_p95_max_s,
            False,
            None if isinstance(delay, NotMeasured) else delay.p95_s,
            None,
        ),
        SloResult(
            "failure detection (s)", slo.failure_detection_max_s, False, None, "SC-5"
        ),
    )


def report_lines(report: ReliabilityReport) -> list[str]:
    """The report as the commands print it."""
    start, end = report.window
    lines = [
        f"reliability from {report.source}",
        f"  window             {start.isoformat()} … {end.isoformat()}",
        f"  classified by      {report.method}, parameters "
        f"{report.classification_sha256[:16]}…",
    ]
    for population in (report.measured, report.simulated):
        lines += _population_lines(population, report.slo)
    lines.append(f"failure detection: not measured — {report.failure_detection.reason}")
    return lines


def _population_lines(population: PopulationReport, slo: SloConfig) -> list[str]:
    name = "simulated" if population.simulated else "measured"
    if not population.passes:
        return [f"{name}: no classified passes in this window"]
    budget = population.budget
    lines = [
        f"{name}: {population.passes} classified passes",
        f"  pass capture rate          {_proportion(population.capture)}",
        f"  confirmed miss rate        {_proportion(population.confirmed_miss)}",
        f"  assignment completion rate {_proportion(population.completion)}",
        f"  schedule execution rate    {_proportion(population.execution)}",
        f"  station availability       {_share(population.availability)}",
        f"  submission delay           {_delays(population.submission_delay)}",
        f"  loss budget                {_budget(budget)}",
    ]
    lines += [
        f"    {reason:<32} {count}"
        for reason, count in budget.by_reason().items()
        if count
    ]
    lines.append("  targets")
    for result in slo_results(population, slo):
        verdict = {True: "met", False: "NOT MET", None: "no figure"}[result.met]
        claim = result.claim or "proposed"
        sign = "≥" if result.at_least else "≤"
        lines.append(
            f"    {result.name:<28} {sign} {result.target:<8g} {verdict:<9} ({claim})"
        )
    lines.append("  stations")
    lines += [
        f"    {one.station_id:<24} capture {_proportion(one.capture)}; "
        f"budget {_budget(one.budget)}"
        for one in population.stations
    ]
    return lines


def _proportion(value: Proportion) -> str:
    if value.estimate is None or value.interval is None:
        return f"{value.numerator}/{value.denominator} (nothing to count)"
    low, high = value.interval
    return (
        f"{value.numerator}/{value.denominator} = {value.estimate:.1%} "
        f"(95% {low:.1%}–{high:.1%})"
    )


def _share(value: Share | NotMeasured) -> str:
    if isinstance(value, NotMeasured):
        return f"not measured — {value.reason}"
    if value.estimate is None:
        return "nothing measured"
    return f"{value.estimate:.2%} of {value.span_s / 3600:.1f} station-hours"


def _delays(value: Delays | NotMeasured) -> str:
    if isinstance(value, NotMeasured):
        return f"not measured — {value.reason}"
    if value.n == 0:
        return "no reports"
    return f"p50 {value.p50_s:.0f} s, p95 {value.p95_s:.0f} s over {value.n} reports"


def _budget(value: LossBudget) -> str:
    if value.remaining_ratio is None:
        return "nothing eligible"
    state = "EXHAUSTED" if value.exhausted else f"{value.remaining_ratio:.0%} left"
    return f"{value.spent} lost of {value.allowed:.1f} allowed, {state}"
