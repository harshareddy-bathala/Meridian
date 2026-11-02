"""``/api/v1/conditions`` and ``/api/v1/insights/model``: outside, and the model.

``/conditions`` is the latest planetary K index and cloud cover the ingest
machine stored, each credited to its source (D-134), read for display only.
The scheduler never reads them live: its conditions come from a snapshot
(D-224).

``/insights/model`` will publish the summary of a sealed model run: the
reliability diagram, the Brier score against the base rate and the completeness
ratio, with the label that says who measured the receptions behind it. The run
on public archive data is Stage 34's, and until it is sealed and its summary
named, the answer is ``not_yet_computed`` (D-086).

Reference: docs/DECISIONS.md D-086, D-134, D-222, D-224, D-331.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from meridian.api import platform_clock
from meridian.api.dependencies import get_connection
from meridian.api.public.models.conditions import PublicConditions
from meridian.api.public.models.not_yet_computed import NotYetComputed
from meridian.store.conditions_reads import (
    CLOUD_COVER,
    KP_INDEX,
    find_latest_samples,
)
from meridian.store.stations import Connection

__all__ = ["MODEL_INSIGHTS_STAGE", "router"]

MODEL_INSIGHTS_STAGE = 34
"""Stage 34 — real-data ingest, whose sealed model run this publishes."""

router = APIRouter()


@router.get("/conditions")
def latest_conditions(
    conn: Connection = Depends(get_connection, scope="function"),
) -> PublicConditions:
    """The newest Kp, and each point's cloud cover for the current hour."""
    now = platform_clock.utc_now()
    return PublicConditions.from_rows(
        now,
        find_latest_samples(conn, KP_INDEX, now=now, covering=False),
        find_latest_samples(conn, CLOUD_COVER, now=now, covering=True),
    )


@router.get("/insights/model")
def model_insights() -> NotYetComputed:
    """A sealed model run's summary: not yet computed."""
    return NotYetComputed(
        reason=(
            "No model run on measured receptions has been sealed and published"
            " yet. Stage 34 fits one on public archive data."
        ),
        available_from_stage=MODEL_INSIGHTS_STAGE,
    )
