"""``/api/v1/results`` — every settled pass across the network, and its result.

The network-wide results view Stage 24 owed (D-365): each pass the platform
classified under the deployment's own method and configuration, its effect on
SC-4, and its diagnosed cause where it was lost. Paged by keyset on the
classification's id, which the result itself publishes (D-085, D-210).

Reference: docs/DECISIONS.md D-085, D-093, D-182, D-210, D-272, D-365.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from meridian.api.dependencies import get_connection
from meridian.api.public.envelope import INVALID_QUERY, PublicError
from meridian.api.public.models import Page
from meridian.api.public.models.results import PublicResult
from meridian.api.public.pagination import (
    PageRequest,
    encode_cursor,
    page_request,
    single_cursor_value,
    trim_overfetch,
)
from meridian.api.public.reliability import deployed_reliability_config
from meridian.reliability.classification import METHOD as CLASSIFICATION_METHOD
from meridian.reliability.config import ReliabilityConfig
from meridian.reliability.diagnosis import METHOD as DIAGNOSIS_METHOD
from meridian.store.result_reads import find_results
from meridian.store.stations import Connection

__all__ = ["router"]

router = APIRouter()


@router.get("/results")
def list_results(
    conn: Connection = Depends(get_connection, scope="function"),
    page: PageRequest = Depends(page_request),
    config: ReliabilityConfig = Depends(deployed_reliability_config),
    station_id: Annotated[str | None, Query()] = None,
) -> Page[PublicResult]:
    """Settled passes, the most recently classified first.

    Args:
        conn: A pooled connection, injected.
        page: ``limit`` and ``cursor``, already validated and decoded.
        config: The deployment's reliability configuration, which names the
            classification and the diagnosis read.
        station_id: Only this station's passes.

    Returns:
        One page of results, and the next cursor or null. An empty page is the
        answer for a network none of whose passes has settled yet.

    Raises:
        PublicError: ``invalid_query`` for a cursor this endpoint did not issue.
    """
    before = single_cursor_value(page.cursor_key)
    if before is not None and not before.isdigit():
        raise PublicError(INVALID_QUERY, "cursor is not a valid cursor.")
    fetched = find_results(
        conn,
        classified_under=(CLASSIFICATION_METHOD, config.classification.sha256()),
        diagnosed_under=(DIAGNOSIS_METHOD, config.diagnosis.sha256()),
        station_id=station_id,
        before_classification_id=None if before is None else int(before),
        limit=page.limit + 1,
    )
    trimmed = trim_overfetch(fetched, page.limit)

    next_cursor = None
    if trimmed.has_more_rows and trimmed.rows:
        next_cursor = encode_cursor((str(trimmed.rows[-1].classification_id),))

    return Page(
        items=[PublicResult.from_row(row) for row in trimmed.rows],
        next_cursor=next_cursor,
    )
