"""``/api/v1/losses`` — every lost pass the platform diagnosed, and why.

One list, the most recently diagnosed first, filterable by station. Each loss
carries its cause or *undetermined*, every cause that was tested with what it
found, and the method that tested them (Stage 27). Read from the stored
diagnoses under the deployment's own method and configuration, never from a
simulator's ledger, which the platform does not hold (D-189).

**A list, where the loss budget is a count.** D-187 counts the budget's debits
and does not list them, because a debit names the exact instant a window closed.
A loss here names its window widened to whole minutes, as every published window
is (D-093), so a list costs nothing D-187 protects (D-331).

Reference: docs/DECISIONS.md D-085, D-093, D-187, D-272, D-273, D-331.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from meridian.api.dependencies import get_connection
from meridian.api.public.envelope import INVALID_QUERY, PublicError
from meridian.api.public.models import Page
from meridian.api.public.models.losses import PublicLoss
from meridian.api.public.pagination import (
    PageRequest,
    encode_cursor,
    page_request,
    single_cursor_value,
    trim_overfetch,
)
from meridian.api.public.reliability import deployed_reliability_config
from meridian.reliability.config import ReliabilityConfig
from meridian.reliability.diagnosis import METHOD
from meridian.store.loss_reads import find_losses
from meridian.store.stations import Connection

__all__ = ["router"]

router = APIRouter()


@router.get("/losses")
def list_losses(
    conn: Connection = Depends(get_connection, scope="function"),
    page: PageRequest = Depends(page_request),
    config: ReliabilityConfig = Depends(deployed_reliability_config),
    station_id: Annotated[str | None, Query()] = None,
) -> Page[PublicLoss]:
    """Diagnosed losses, the most recently diagnosed first.

    Args:
        conn: A pooled connection, injected.
        page: ``limit`` and ``cursor``, already validated and decoded.
        config: The deployment's reliability configuration, whose
            ``[diagnosis]`` table names the diagnoses read.
        station_id: Only this station's losses.

    Returns:
        One page of losses, and the next cursor or null. An empty page is the
        answer for a network that has lost nothing, or not yet diagnosed it.

    Raises:
        PublicError: ``invalid_query`` for a cursor this endpoint did not issue.
    """
    fetched = find_losses(
        conn,
        method=METHOD,
        config_sha256=config.diagnosis.sha256(),
        station_id=station_id,
        before_diagnosis_id=_diagnosis_id(single_cursor_value(page.cursor_key)),
        limit=page.limit + 1,
    )
    trimmed = trim_overfetch(fetched, page.limit)

    next_cursor = None
    if trimmed.has_more_rows and trimmed.rows:
        next_cursor = encode_cursor((str(trimmed.rows[-1].diagnosis_id),))

    return Page(
        items=[PublicLoss.from_row(row) for row in trimmed.rows],
        next_cursor=next_cursor,
    )


def _diagnosis_id(value: str | None) -> int | None:
    """The cursor's diagnosis id, refused when it is not one."""
    if value is None:
        return None
    if not value.isdigit():
        raise PublicError(INVALID_QUERY, "cursor is not a valid cursor.")
    return int(value)
