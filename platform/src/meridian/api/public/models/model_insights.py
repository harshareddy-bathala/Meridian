"""A sealed model run's summary, as the file states it and the public API serves it.

Stage 34 seals a model run on public archive receptions and writes its summary,
schema ``model-summary-1``, to a file the deployment names with
``REPORT_SUMMARY`` (D-332). Every figure in it is copied from the sealed
report's prediction rows, so nothing here is computed and the run's hash names
where each came from (rule 8).

**The file is checked, not trusted.** The schema string must match exactly, no
key may be unknown, a computable configuration carries its ten calibration bins,
and a configuration the archive cannot support carries a reason and **no
number at all**, never a zero (D-086). A file that fails any of this is refused
whole, as every other configuration file is, rather than served in part.

**The label travels with the figures.** It says who measured the receptions
behind the model, and that an archive holds only the passes someone chose to
observe (``EVALUATION.md``, selection bias). The dashboard prints it beside
every number it draws from here.

This module builds no SQL and opens no connection.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "BINS",
    "SCHEMA",
    "ComputableConfiguration",
    "NotComputableConfiguration",
    "PublicModelInsights",
    "SummaryBin",
    "SummarySource",
    "SummarySplit",
]

SCHEMA = "model-summary-1"
"""The only summary format this platform reads."""

BINS = 10
"""Calibration bins in a computable configuration, empty ones kept."""

Interval = tuple[float, float] | None


class _Strict(BaseModel):
    """A part of the file: every key known, nothing coerced from another type."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class SummarySource(_Strict):
    """Who measured the receptions the model was fitted on, and on what terms."""

    name: str = Field(min_length=1)
    licence: str = Field(min_length=1)
    terms_url: str = Field(pattern=r"^https://")
    retrieved_from: datetime
    retrieved_until: datetime


class SummarySplit(_Strict):
    """The temporal split the run fitted, validated and tested on (rule 6)."""

    train_until: datetime
    validate_until: datetime
    """Each an instant, as the sealed report states its split's bounds."""
    as_of: datetime


class SummaryBin(_Strict):
    """One calibration bin: predicted against observed, from ``low`` to ``high``."""

    low: float = Field(ge=0.0, le=1.0)
    high: float = Field(ge=0.0, le=1.0)
    n: int = Field(ge=0)
    mean_predicted: float | None
    observed: float | None
    observed_interval: Interval


class ComputableConfiguration(_Strict):
    """A configuration the run fitted and judged."""

    name: Literal["A", "B", "C", "D"]
    computable: Literal[True]
    n_test: int = Field(ge=0)
    n_decoded: int = Field(ge=0)
    brier: float
    brier_interval: Interval
    base_rate: float
    base_brier: float
    skill: float | None
    skill_interval: Interval
    bins: list[SummaryBin]

    @model_validator(mode="after")
    def _ten_bins(self) -> Self:
        if len(self.bins) != BINS:
            raise ValueError(f"configuration {self.name} has {len(self.bins)} bins")
        return self


class NotComputableConfiguration(_Strict):
    """A configuration the archive cannot support, and why: no number at all."""

    name: Literal["A", "B", "C", "D"]
    computable: Literal[False]
    reason: str = Field(min_length=1)


Configuration = Annotated[
    ComputableConfiguration | NotComputableConfiguration,
    Field(discriminator="computable"),
]


class PublicModelInsights(_Strict):
    """A sealed model run's summary, served as the file states it."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        validate_by_name=True,
        serialize_by_alias=True,
    )

    status: Literal["computed"] = "computed"
    schema_: Literal["model-summary-1"] = Field(alias="schema")
    run_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    method: str = Field(pattern=r"^report-[0-9]+$")
    label: str = Field(min_length=1)
    source: SummarySource
    simulated: bool
    completeness_ratio: float | None = Field(ge=0.0, le=1.0)
    split: SummarySplit
    configurations: list[Configuration]

    @model_validator(mode="after")
    def _each_configuration_once(self) -> Self:
        names = [one.name for one in self.configurations]
        if len(names) != len(set(names)):
            raise ValueError(f"a configuration is named twice: {names}")
        return self
