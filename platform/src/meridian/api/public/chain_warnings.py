"""``/api/v1/receive-chain-warnings`` — every station's receive-chain warnings.

The network's list of Stage 28's warnings, the most recently raised first:
``state=open``, the default, for those not yet cleared, or ``state=all``. Each
is published as a station's own receive-chain response publishes it (D-295),
with the station and chain beside it, and read under the deployment's own watch
method and configuration only.

An empty list is the answer for a network with no open warning. It is never a
placeholder: a fleet whose chains have no baseline yet has nothing to warn of,
and says so through each station's receive-chain response.

Reference: docs/DECISIONS.md D-085, D-292, D-295, D-331.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from meridian.api.dependencies import get_connection
from meridian.api.public.envelope import INVALID_QUERY, PublicError
from meridian.api.public.models import Page
from meridian.api.public.models.receive_chain import PublicNetworkWarning
from meridian.api.public.pagination import (
    PageRequest,
    encode_cursor,
    page_request,
    single_cursor_value,
    trim_overfetch,
)
from meridian.api.public.reliability import deployed_reliability_config
from meridian.reliability.config import ReliabilityConfig
from meridian.reliability.watch_run import watched_under
from meridian.store.stations import Connection
from meridian.store.watch_reads import find_network_warnings

__all__ = ["router"]

router = APIRouter()


@router.get("/receive-chain-warnings")
def list_receive_chain_warnings(
    conn: Connection = Depends(get_connection, scope="function"),
    page: PageRequest = Depends(page_request),
    config: ReliabilityConfig = Depends(deployed_reliability_config),
    state: Annotated[Literal["open", "all"], Query()] = "open",
) -> Page[PublicNetworkWarning]:
    """Receive-chain warnings across the network, newest first.

    Args:
        conn: A pooled connection, injected.
        page: ``limit`` and ``cursor``, already validated and decoded.
        config: The deployment's reliability configuration, whose baseline and
            watch tables name the warnings read.
        state: ``open`` or ``all``; any other value is ``invalid_query``.

    Raises:
        PublicError: ``invalid_query`` for a cursor this endpoint did not issue.
    """
    before = single_cursor_value(page.cursor_key)
    if before is not None and not before.isdigit():
        raise PublicError(INVALID_QUERY, "cursor is not a valid cursor.")
    fetched = find_network_warnings(
        conn,
        under=watched_under(config),
        open_only=state == "open",
        before_id=None if before is None else int(before),
        limit=page.limit + 1,
    )
    trimmed = trim_overfetch(fetched, page.limit)

    next_cursor = None
    if trimmed.has_more_rows and trimmed.rows:
        next_cursor = encode_cursor((str(trimmed.rows[-1].warning.id),))

    return Page(
        items=[PublicNetworkWarning.from_network(row) for row in trimmed.rows],
        next_cursor=next_cursor,
    )
