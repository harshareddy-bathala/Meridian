"""A scheduling decision, as the public API states it.

Built from ``store.assignment_log.LoggedAssignment``. The window is widened to
whole minutes on the way here (D-093); everything else is the scheduler's own
record, published as written, because the reason and the score are the point.

``decision`` and ``state`` are two different facts and both are published:
``decision`` is what the scheduler wanted, ``state`` is what the station did with
it (D-008). A pass can be ``scheduled`` and ``expired``. A ``skipped`` pass
has no state: it was never delivered, so ``state`` is null (D-165).

``explanation`` is why: the terms of the pass's value, the passes it was weighed
against and the rule that decided it, as the scheduler stored it when it
decided (D-170). Null for a decision made before Stage 18, which no recorded
run made.

Reference: docs/DATA-MODEL.md ``assignments``; docs/DECISIONS.md D-008, D-093,
D-170.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field

from meridian.api.public.window_privacy import publish_window
from meridian.store.assignment_log import LoggedAssignment

__all__ = [
    "AssignmentDecision",
    "AssignmentState",
    "Explanation",
    "ExplanationRun",
    "ExplanationTerms",
    "PublicAssignment",
    "WeighedPass",
]

AssignmentDecision = Literal["scheduled", "skipped"]
AssignmentState = Literal[
    "issued", "held", "in_progress", "reported", "expired", "revoked"
]


class ExplanationTerms(BaseModel):
    """A pass's value, and the terms it is the product of (D-168)."""

    model_config = ConfigDict(serialize_by_alias=True, validate_by_name=True)

    value: float
    yield_: float = Field(alias="yield")
    """The probability of a decode: a model's, or the elevation proxy."""
    yield_source: Literal["model", "elevation_proxy"]
    yield_path: str | None
    """The model's route (D-161); null for the proxy."""
    yield_reason: str
    frames: float
    frames_term: str
    priority: float
    priority_weighted: bool


class WeighedPass(BaseModel):
    """A pass this one could not share the station with."""

    pass_id: int
    decision: Literal["scheduled", "skipped", "committed"]
    """``committed``: an assignment an earlier run made, which bound this one."""
    value: float | None
    """Null for a commitment, whose value this run did not weigh."""
    assignment_id: str | None
    """Set for a commitment, whose id already existed."""


class ExplanationRun(BaseModel):
    """What the decision's run shares with every other decision in it."""

    status: Literal["optimal", "time_limit", "fallback"]
    history_as_of: datetime | None


class Explanation(BaseModel):
    """Why a decision went the way it did (D-170)."""

    terms: ExplanationTerms
    weighed_against: list[WeighedPass]
    """Best value first; commitments last."""
    rule: Literal["overlap", "eligible_cap"] | None
    """For a skip, the constraint that decided it (D-166)."""
    alternative: WeighedPass | None
    """For a skip, what took its slot; for a selection, the best it displaced."""
    run: ExplanationRun


class PublicAssignment(BaseModel):
    """One pass the scheduler decided about, and why."""

    model_config = ConfigDict(serialize_by_alias=True)

    assignment_id: str
    pass_id: int
    station_id: str
    satellite_id: str

    start_at: datetime
    """Floored to the minute (D-093)."""
    end_at: datetime
    """Ceiled to the minute (D-093)."""
    issued_at: datetime

    centre_freq_hz: int
    mode: str
    timing_uncertainty_s: float

    decision: AssignmentDecision
    reason: str
    """Human-readable, and shown on the dashboard beside the decision."""
    score: float | None
    """The scheduler's score for this pass, or null for a policy that does not
    score (a baseline that takes passes in order)."""
    conflicts_with_assignment_id: str | None
    """For a skipped pass, the one it lost to."""
    priority: float
    predicted_yield: float | None
    """Null until a prediction model runs (Stage 17). Never zero for "unknown"."""
    prediction_config: str | None = Field(serialization_alias="model_config")
    """Which evaluation configuration (A–D) produced the prediction, if any.

    Published as ``model_config``, the column's name in ``DATA-MODEL.md``; held
    under another name here only because Pydantic reserves that one."""

    state: AssignmentState | None
    """What the station did with the assignment; null for a skip, which a
    station is never given (D-165)."""
    simulated: bool

    schedule_run_id: str | None
    """The run that decided it; null before Stage 18."""
    model_sha256: str | None
    """The model that gave ``predicted_yield``, as hex; null without one."""
    explanation: Explanation | None
    revision: int
    """Which decision about the pass this is; the list shows the current one
    (D-171)."""
    revoked_reason: Literal["declined", "offline"] | None
    """Why a ``revoked`` assignment was taken back; null otherwise."""

    @classmethod
    def from_row(cls, row: LoggedAssignment) -> Self:
        """Publish one stored decision."""
        window = publish_window(row.start_at, row.end_at)
        return cls(
            assignment_id=row.assignment_id,
            pass_id=row.pass_id,
            station_id=row.station_id,
            satellite_id=row.satellite_id,
            start_at=window.start,
            end_at=window.end,
            issued_at=row.issued_at,
            centre_freq_hz=row.centre_freq_hz,
            mode=row.mode,
            timing_uncertainty_s=row.timing_uncertainty_s,
            decision=_decision(row.decision),
            reason=row.reason,
            score=row.score,
            conflicts_with_assignment_id=row.conflicts_with_assignment_id,
            priority=row.priority,
            predicted_yield=row.predicted_yield,
            prediction_config=row.model_config,
            state=None if row.decision == "skipped" else _state(row.state),
            simulated=row.simulated,
            schedule_run_id=row.schedule_run_id,
            model_sha256=None if row.model_sha256 is None else row.model_sha256.hex(),
            explanation=None
            if row.explanation is None
            else Explanation.model_validate(row.explanation),
            revision=row.revision,
            revoked_reason=_revoked_reason(row.revoked_reason),
        )


def _decision(value: str) -> AssignmentDecision:
    """Narrow the column's text to the vocabulary its CHECK constraint allows."""
    for decision in ("scheduled", "skipped"):
        if value == decision:
            return decision
    raise ValueError(f"assignments.decision holds {value!r}, outside its CHECK")


def _state(value: str) -> AssignmentState:
    """Narrow the column's text to the vocabulary its CHECK constraint allows."""
    for state in ("issued", "held", "in_progress", "reported", "expired", "revoked"):
        if value == state:
            return state
    raise ValueError(f"assignments.state holds {value!r}, outside its CHECK")


def _revoked_reason(value: str | None) -> Literal["declined", "offline"] | None:
    """Narrow the column's text to the vocabulary its CHECK constraint allows."""
    if value is None:
        return None
    for reason in ("declined", "offline"):
        if value == reason:
            return reason
    raise ValueError(f"assignments.revoked_reason holds {value!r}, outside its CHECK")
