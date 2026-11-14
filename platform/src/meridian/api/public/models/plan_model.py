"""``plan-model-1``: configuration A's model, as the planner reads it from a file.

``meridian model planner`` writes it into ``PUBLISHED_DIR`` from a verified
model directory, and :mod:`meridian.api.public.plan_model` reads it back. It
is an input, never a response: nothing here is published as it stands.

Every key known, nothing coerced, as D-332's summaries are read.

Reference: docs/DECISIONS.md D-163, D-332, D-370.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["PlanModelFile"]


class PlanModelFile(BaseModel):
    """``plan-model-1``, as ``meridian model planner`` writes it."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_: Literal["plan-model-1"] = Field(alias="schema")
    model_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    configuration: Literal["A"]
    population: Literal["own", "archive"]
    as_of: datetime
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fitted: dict[str, object]
    """``model.json``'s document, unchanged."""
