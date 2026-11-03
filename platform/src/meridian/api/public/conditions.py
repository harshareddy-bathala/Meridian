"""``/api/v1/conditions`` and ``/api/v1/insights/model``: outside, and the model.

``/conditions`` is the latest planetary K index and cloud cover the ingest
machine stored, each credited to its source (D-134), read for display only.
The scheduler never reads them live: its conditions come from a snapshot
(D-224).

``/insights/model`` publishes the summary of a sealed model run: each
configuration's Brier score against the base rate, its calibration bins, and the
completeness ratio, with the label that says who measured the receptions behind
it. The summary is a file the deployment names with ``REPORT_SUMMARY``, written
by Stage 34's sealed run (D-332). Unnamed, the answer is ``not_yet_computed``
(D-086). Named and refused, it is a ``server_error`` that names the setting, and
never a partial body.

Reference: docs/DECISIONS.md D-086, D-134, D-222, D-224, D-331, D-332.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import Field, ValidationError

from meridian.api import platform_clock
from meridian.api.dependencies import get_connection, get_settings
from meridian.api.public.envelope import SERVER_ERROR, PublicError
from meridian.api.public.models.conditions import PublicConditions
from meridian.api.public.models.model_insights import PublicModelInsights
from meridian.api.public.models.not_yet_computed import NotYetComputed
from meridian.config import Settings
from meridian.store.conditions_reads import (
    CLOUD_COVER,
    KP_INDEX,
    find_latest_samples,
)
from meridian.store.stations import Connection

__all__ = ["MODEL_INSIGHTS_STAGE", "ModelInsights", "router"]

MODEL_INSIGHTS_STAGE = 34
"""Stage 34 — real-data ingest, whose sealed model run this publishes."""

ModelInsights = Annotated[
    PublicModelInsights | NotYetComputed, Field(discriminator="status")
]
"""What the insight serves; ``status`` says which, in the schema as on the wire."""

_log = logging.getLogger(__name__)

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
def model_insights(
    settings: Settings = Depends(get_settings),
) -> ModelInsights:
    """A sealed model run's summary, or ``not_yet_computed`` when none is named.

    Raises:
        PublicError: ``server_error`` when the named file cannot be read or is
            not a ``model-summary-1`` summary. The reason is logged for the
            operator; the reader is told only which setting.
    """
    if not settings.report_summary:
        return NotYetComputed(
            reason=(
                "No model run on measured receptions has been sealed and published"
                " yet. Stage 34 fits one on public archive data."
            ),
            available_from_stage=MODEL_INSIGHTS_STAGE,
        )
    try:
        held = Path(settings.report_summary).read_bytes()
        return PublicModelInsights.model_validate_json(held)
    except (OSError, ValidationError):
        _log.warning("model summary refused", exc_info=True)
        raise PublicError(
            SERVER_ERROR,
            "The model summary REPORT_SUMMARY names was refused; the platform's"
            " log says why.",
        ) from None
