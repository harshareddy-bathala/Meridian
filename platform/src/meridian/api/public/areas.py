"""``/api/v1/areas`` — areas of interest, once the team has said they may be public.

Until D-137 is settled nothing about an area is published (D-227). D-326
proposes the answer; the operator says it is accepted by setting
``AREAS_PUBLISHED=true``, and names the file ``meridian regions summary`` wrote
with ``AREAS_SUMMARY``. Either missing, the endpoint answers ``not_yet_computed``
with the reason and no field a chart could read (D-086).

**The file, not the report.** No runtime module may import ``meridian.regions``
(D-228), so the API reads the summary the operator published. It is read on
each request — a small file, changed by a person — and a file that cannot be
read or does not parse is a ``server_error`` naming the setting, never a partial
body.

Reference: docs/DECISIONS.md D-086, D-137, D-227, D-228, D-326.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter
from pydantic import ValidationError

from meridian.api.public.envelope import SERVER_ERROR, PublicError
from meridian.api.public.models.areas import AreasSummary, PublicAreas
from meridian.api.public.models.not_yet_computed import NotYetComputed

__all__ = ["AREAS_PUBLISHED_ENV", "AREAS_STAGE", "AREAS_SUMMARY_ENV", "router"]

AREAS_PUBLISHED_ENV = "AREAS_PUBLISHED"
AREAS_SUMMARY_ENV = "AREAS_SUMMARY"
AREAS_STAGE = 34

_log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/areas")
def areas_of_interest() -> PublicAreas | NotYetComputed:
    """Each published area, its public-product series and its changes.

    Raises:
        PublicError: ``server_error``, if the published file is refused.
    """
    published = os.environ.get(AREAS_PUBLISHED_ENV, "").strip().lower() == "true"
    named = os.environ.get(AREAS_SUMMARY_ENV, "").strip()
    if not published or not named:
        return NotYetComputed(
            reason=(
                "Areas of interest are not published until the team settles "
                "who may register one and whether a registration is public "
                "(D-137, proposed by D-326)."
            ),
            available_from_stage=AREAS_STAGE,
        )
    try:
        raw = json.loads(Path(named).read_text(encoding="utf-8"))
        summary = AreasSummary.model_validate(raw)
    except (OSError, ValueError, ValidationError):
        _log.warning("the published areas file was refused", exc_info=True)
        raise PublicError(
            SERVER_ERROR,
            f"The file {AREAS_SUMMARY_ENV} names was refused; the platform's log "
            "says why.",
        ) from None
    return PublicAreas(summary=summary)
