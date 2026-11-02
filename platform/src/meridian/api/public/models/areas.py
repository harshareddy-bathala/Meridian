"""``/api/v1/areas``'s body: areas of interest and their public-product series.

The fields are ``areas-summary-1``'s, written by ``meridian regions summary``
from a sealed regional report (D-229, D-326), and parsed strictly: an unknown
key, a wrong type or another schema refuses the file rather than publishing
part of it. No note and no polygon is published, only a label and a bounding
box (D-227), and no reception count: coverage is our own network's and may be
simulated.

``simulated`` is stated and is always false: every value here is a public
product's, none is a station's (rule 5).

Reference: docs/DECISIONS.md D-086, D-227, D-229, D-326.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AreaChange",
    "AreaPeriod",
    "AreaSeriesPoint",
    "AreaSource",
    "AreasSummary",
    "PublicArea",
    "PublicAreas",
]

_STRICT = ConfigDict(extra="forbid", serialize_by_alias=True, validate_by_name=True)
"""Unknown keys refuse the file; a value of the wrong type refuses it too."""


class AreaSource(BaseModel):
    """A product an area's values came from, with the terms it arrived under."""

    model_config = _STRICT
    source_id: str
    licence: str
    terms_url: str


class PublicArea(BaseModel):
    """A place and a label, never a person (D-227)."""

    model_config = _STRICT
    area_id: int
    label: str
    area_km2: float
    bbox: list[float]
    """West, south, east, north, in degrees."""


class AreaSeriesPoint(BaseModel):
    """One period's value for one area, or why there is none."""

    model_config = _STRICT
    area_id: int
    quantity: str
    unit: str
    period_from: str
    period_until: str
    value: float | None
    missing_reason: str | None
    count: int
    sources: list[str]


class AreaPeriod(BaseModel):
    """A span of a change's comparison."""

    model_config = _STRICT
    starts: str = Field(alias="from")
    """``from`` in the file and the body; a keyword in Python."""

    until: str


class AreaChange(BaseModel):
    """One area's change in one quantity, with its interval and verdict."""

    model_config = _STRICT
    area_id: int
    quantity: str
    verdict: str
    reason: str
    baseline: AreaPeriod
    current: AreaPeriod
    baseline_mean: float | None
    current_mean: float | None
    change: float | None
    low: float | None
    high: float | None


class AreasSummary(BaseModel):
    """``areas-summary-1`` exactly, as the file holds it."""

    model_config = _STRICT
    schema_: Literal["areas-summary-1"] = Field(alias="schema")
    report_sha256: str
    method: str
    since: str
    as_of: str
    sources: list[AreaSource]
    areas: list[PublicArea]
    series: list[AreaSeriesPoint]
    changes: list[AreaChange]


class PublicAreas(BaseModel):
    """The published body: the summary, said to be computed and not simulated."""

    status: Literal["computed"] = "computed"
    simulated: Literal[False] = False
    summary: AreasSummary
