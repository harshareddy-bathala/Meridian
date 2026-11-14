"""Every settled pass and what became of it, as the public API states it.

A result is a stored classification (D-182) with its window widened to whole
minutes, as every published window is (D-093), so listing passes gives away
nothing D-187 protects by counting the budget's debits instead of listing them
(D-331 made the same argument for losses). ``effect`` is the reliability
module's reading of the class, so a reader never has to know which classes
spend the budget (D-365).

Reference: docs/DECISIONS.md D-093, D-182, D-185, D-187, D-331, D-365.
"""

from __future__ import annotations

from datetime import datetime
from typing import Self

from pydantic import BaseModel

from meridian.api.public.window_privacy import publish_window
from meridian.reliability.budget import PassEffect, effect_of
from meridian.store.result_reads import ListedResult

__all__ = ["PublicResult"]


class PublicResult(BaseModel):
    """One settled pass."""

    classification_id: int
    assignment_id: str
    station_id: str
    satellite_id: str
    window_start: datetime
    """Floored to the minute (D-093)."""

    window_end: datetime
    """Ceiled to the minute (D-093)."""

    classification: str
    """The pass's class (D-182)."""

    effect: PassEffect
    """What the class does to SC-4: ``captured``, ``lost`` (it spends the loss
    budget), or ``satellite`` (the satellite's own doing, neither)."""

    listening_confirmed: bool
    """Whether the registry confirmed a station was listening (rule 7)."""

    outcome: str | None
    """The station's report, or ``null`` when none arrived."""

    diagnosis_id: int | None
    """The loss's diagnosis on ``/losses``, or ``null`` if none was made."""

    cause: str | None
    """The diagnosed cause or ``undetermined``; ``null`` if not diagnosed."""

    simulated: bool

    @classmethod
    def from_row(cls, row: ListedResult) -> Self:
        """Publish one settled pass."""
        window = publish_window(row.window_start, row.window_end)
        return cls(
            classification_id=row.classification_id,
            assignment_id=row.assignment_id,
            station_id=row.station_id,
            satellite_id=row.satellite_id,
            window_start=window.start,
            window_end=window.end,
            classification=row.classification,
            effect=effect_of(row.classification),
            listening_confirmed=row.listening_confirmed,
            outcome=row.outcome,
            diagnosis_id=row.diagnosis_id,
            cause=row.cause,
            simulated=row.simulated,
        )
