"""A sealed run's success criteria, as the file states them and the API serves them.

``meridian report criteria`` writes ``criteria-summary-1`` from a sealed
evaluation report, every figure copied from the run's own rows (D-368). The
deployment names the file with ``REPORT_CRITERIA``.

**The file is checked, not trusted**, as the model summary is (D-332):
- the schema string must match exactly, and no key may be unknown;
- each criterion appears once;
- a measured one carries at least one figure and its section's verdict;
- one not measured carries its reason and **no figure and no verdict at all**,
  never a zero (D-086);
- and the simulated flag must be the one its criterion is measured on: SC-5, SC-8
  and SC-9 on simulated fault runs, the rest on our measured stations (rule 5).

A file that fails any of this is refused whole.

This module builds no SQL and opens no connection.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "SIMULATED_CRITERIA",
    "CriterionFigure",
    "MeasuredCriterion",
    "PublicCriteria",
    "UnmeasuredCriterion",
]

SIMULATED_CRITERIA = frozenset({"SC-5", "SC-8", "SC-9"})
"""Criteria judged on simulated fault runs; every other one on measured stations."""

CriterionId = Literal["SC-1", "SC-2", "SC-3", "SC-4", "SC-5", "SC-7", "SC-8", "SC-9"]


class _Strict(BaseModel):
    """A part of the file: every key known, nothing coerced from another type."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class CriterionFigure(_Strict):
    """One figure a criterion is judged by, against its target."""

    name: str = Field(pattern=r"^[a-z0-9_]+$")
    value: float
    low: float | None
    high: float | None
    """The 95% interval, where the section computed one."""

    target: float
    direction: Literal["at_least", "at_most"]
    """Which side of the target meets it."""

    n: int | None = Field(ge=0)
    """What the figure counts over, where the section states it."""


class MeasuredCriterion(_Strict):
    """A criterion the run judged, with its figures and its section's verdict."""

    id: CriterionId
    simulated: bool
    proposed: bool
    status: Literal["measured"]
    reason: None
    figures: list[CriterionFigure] = Field(min_length=1)
    met: bool
    interval_clears: bool | None
    """Whether the whole interval is on the target's side, where it was judged."""

    claimable: bool
    caveat: str | None
    """Why a target met may not be claimed yet, or ``None``."""


class UnmeasuredCriterion(_Strict):
    """A criterion the run could not judge, and why: no number at all."""

    id: CriterionId
    simulated: bool
    proposed: bool
    status: Literal["not_measured", "not_run"]
    reason: str = Field(min_length=1)
    figures: list[CriterionFigure] = Field(max_length=0)
    met: None
    interval_clears: None
    claimable: Literal[False]
    caveat: None


Criterion = Annotated[
    MeasuredCriterion | UnmeasuredCriterion, Field(discriminator="status")
]


class PublicCriteria(_Strict):
    """A sealed run's criteria, served as the file states them."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        validate_by_name=True,
        serialize_by_alias=True,
    )

    status: Literal["computed"] = "computed"
    schema_: Literal["criteria-summary-1"] = Field(alias="schema")
    run_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    method: str = Field(pattern=r"^report-[0-9]+$")
    since: datetime
    as_of: datetime
    criteria: list[Criterion]

    @model_validator(mode="after")
    def _each_once_and_labelled(self) -> Self:
        ids = [one.id for one in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError(f"a criterion is named twice: {ids}")
        for one in self.criteria:
            if one.simulated != (one.id in SIMULATED_CRITERIA):
                raise ValueError(f"{one.id} is labelled simulated={one.simulated}")
        return self
