"""A scheduling decision, as the public API states it.

Built from ``store.assignment_log.LoggedAssignment``. The window is widened to
whole minutes on the way here (D-093); everything else is the scheduler's own
record, published as written, because the reason and the score are the point.

``decision`` and ``state`` are two different facts and both are published:
``decision`` is what the scheduler wanted, ``state`` is what the station did with
it (D-008). A pass can be ``scheduled`` and ``expired``.

Reference: docs/DATA-MODEL.md ``assignments``; docs/DECISIONS.md D-008, D-093.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field

from meridian.api.public.window_privacy import publish_window
from meridian.store.assignment_log import LoggedAssignment

__all__ = ["AssignmentDecision", "AssignmentState", "PublicAssignment"]

AssignmentDecision = Literal["scheduled", "skipped"]
AssignmentState = Literal["issued", "held", "in_progress", "reported", "expired"]


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

    state: AssignmentState
    simulated: bool

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
            state=_state(row.state),
            simulated=row.simulated,
        )


def _decision(value: str) -> AssignmentDecision:
    """Narrow the column's text to the vocabulary its CHECK constraint allows."""
    for decision in ("scheduled", "skipped"):
        if value == decision:
            return decision
    raise ValueError(f"assignments.decision holds {value!r}, outside its CHECK")


def _state(value: str) -> AssignmentState:
    """Narrow the column's text to the vocabulary its CHECK constraint allows."""
    for state in ("issued", "held", "in_progress", "reported", "expired"):
        if value == state:
            return state
    raise ValueError(f"assignments.state holds {value!r}, outside its CHECK")
