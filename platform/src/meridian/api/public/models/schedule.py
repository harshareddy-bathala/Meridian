"""A station's schedule, and the latest scheduling round, as the public API states them.

**A schedule** is a station's decisions over the next hours, scheduled and
skipped, each published as ``/api/v1/assignments`` publishes it, with its pass's
peak elevation to the degree (D-093) and the epoch of the element set that
predicted the pass. Element-set age is a first-class figure (CLAUDE.md), and the
epoch lets a reader see how old the orbit behind a decision was.

**The latest round** is the run's own record (D-170): its configuration, the
solver's status, objective and bound, how long it took, and its decisions
counted by population, so a round over simulated stations says so.

**Its gain over a baseline is not published from a live round.** The jobs
service records the configuration it scheduled with and no other, and a
configuration compared on another day is not a comparison. The gain is
measured by ``meridian schedule evaluate``, on a snapshot, against the greedy
baselines and the oracle (D-172), and a sealed report publishes it (rule 8).
The field says that, in words (D-086).

This module builds no SQL and opens no connection.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel

from meridian.api.public.models.assignments import PublicAssignment
from meridian.api.public.models.reliability import NotMeasuredFigure
from meridian.api.public.window_privacy import (
    ceil_minute,
    floor_minute,
    publish_angle,
)
from meridian.store.assignment_log import ScheduleEntry
from meridian.store.schedule_run_reads import LatestRun

__all__ = [
    "BASELINE_GAIN_REASON",
    "PublicScheduleEntry",
    "PublicScheduleRun",
    "PublicStationSchedule",
]

BASELINE_GAIN_REASON = (
    "A live round records only the configuration it scheduled with. The gain over"
    " the baselines is measured on a snapshot by `meridian schedule evaluate`"
    " (D-172) and published in a sealed evaluation report."
)


class PublicScheduleEntry(BaseModel):
    """One decision in a station's schedule, and what predicted its pass."""

    assignment: PublicAssignment
    max_elevation_deg: int
    """The pass's peak, to the degree (D-093)."""
    element_set_epoch: datetime
    """The epoch of the element set the pass was predicted from."""

    @classmethod
    def from_row(cls, row: ScheduleEntry) -> Self:
        """Publish one decision of a station's schedule."""
        return cls(
            assignment=PublicAssignment.from_row(row),
            max_elevation_deg=publish_angle(row.max_elevation_deg),
            element_set_epoch=row.element_set_epoch,
        )


class PublicStationSchedule(BaseModel):
    """A station's decisions over a stated span, soonest first."""

    station_id: str
    simulated: bool
    start: datetime
    """Floored to the minute."""
    end: datetime
    """Ceiled to the minute."""
    entries: list[PublicScheduleEntry]

    @classmethod
    def from_rows(
        cls,
        station_id: str,
        simulated: bool,
        start: datetime,
        end: datetime,
        rows: list[ScheduleEntry],
    ) -> Self:
        """Publish a station's schedule over ``[start, end)``."""
        return cls(
            station_id=station_id,
            simulated=simulated,
            start=floor_minute(start),
            end=ceil_minute(end),
            entries=[PublicScheduleEntry.from_row(one) for one in rows],
        )


class PublicScheduleRun(BaseModel):
    """The latest scheduling round's own record."""

    status: Literal["computed"] = "computed"
    run_id: str
    decided_at: datetime
    horizon_start: datetime
    horizon_end: datetime
    configuration: str
    """A, B, C or D (D-160)."""
    yield_source: Literal["model", "elevation_proxy"]
    model_sha256: str | None
    history_as_of: datetime | None
    solver: str
    solver_version: str
    solver_status: Literal["optimal", "time_limit", "fallback"]
    objective: float
    bound: float | None
    """The solver's bound, null for a greedy fallback, which proves none."""
    time_limit_s: float
    runtime_s: float
    stations: int
    candidates: int
    scheduled: int
    skipped: int
    simulated_decisions: int
    measured_decisions: int
    baseline_gain: NotMeasuredFigure
    """Not from a live round; see the module's docstring."""

    @classmethod
    def from_row(cls, row: LatestRun) -> Self:
        """Publish one run."""
        return cls(
            run_id=row.run_id,
            decided_at=row.decided_at,
            horizon_start=row.horizon_start,
            horizon_end=row.horizon_end,
            configuration=row.model_config,
            yield_source=_yield_source(row.yield_source),
            model_sha256=None if row.model_sha256 is None else row.model_sha256.hex(),
            history_as_of=row.history_as_of,
            solver=row.solver,
            solver_version=row.solver_version,
            solver_status=_solver_status(row.status),
            objective=row.objective,
            bound=row.bound,
            time_limit_s=row.time_limit_s,
            runtime_s=row.runtime_s,
            stations=row.stations,
            candidates=row.candidates,
            scheduled=row.scheduled,
            skipped=row.skipped,
            simulated_decisions=row.simulated_decisions,
            measured_decisions=row.measured_decisions,
            baseline_gain=NotMeasuredFigure(reason=BASELINE_GAIN_REASON),
        )


def _yield_source(value: str) -> Literal["model", "elevation_proxy"]:
    """Narrow the column's text to the vocabulary its CHECK constraint allows."""
    for source in ("model", "elevation_proxy"):
        if value == source:
            return source
    raise ValueError(f"schedule_runs.yield_source holds {value!r}, outside its CHECK")


def _solver_status(value: str) -> Literal["optimal", "time_limit", "fallback"]:
    """Narrow the column's text to the vocabulary its CHECK constraint allows."""
    for status in ("optimal", "time_limit", "fallback"):
        if value == status:
            return status
    raise ValueError(f"schedule_runs.status holds {value!r}, outside its CHECK")
