"""``/api/v1/events`` — what just happened across the network, newest first.

The live page's feed: a station joined, a pass was decoded, a loss was
diagnosed, a receive-chain warning was raised or cleared. Each is read from the
record that says so (D-363), over the last :data:`WINDOW_DAYS` days, and paged
by keyset like every public list (D-085).

The cursor carries the last event's time, kind and record id, each of which the
event itself publishes, so the cursor discloses nothing the page does not
(D-210).

Reference: docs/DECISIONS.md D-085, D-210, D-272, D-292, D-363.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends

from meridian.api import platform_clock
from meridian.api.dependencies import get_connection
from meridian.api.public.envelope import INVALID_QUERY, PublicError
from meridian.api.public.models import Page
from meridian.api.public.models.events import PublicEvent
from meridian.api.public.pagination import (
    PageRequest,
    encode_cursor,
    page_request,
    trim_overfetch,
)
from meridian.api.public.reliability import deployed_reliability_config
from meridian.reliability.config import ReliabilityConfig
from meridian.reliability.diagnosis import METHOD as DIAGNOSIS_METHOD
from meridian.reliability.watch_run import watched_under
from meridian.store.event_reads import EVENT_KINDS, EventKey, find_events
from meridian.store.stations import Connection

__all__ = ["WINDOW_DAYS", "router"]

WINDOW_DAYS = 7
"""How far back the feed reaches. Older history is each module's own page:
``/losses``, ``/observations``, ``/receive-chain-warnings``, ``/stations``."""

router = APIRouter()


@router.get("/events")
def list_events(
    conn: Connection = Depends(get_connection, scope="function"),
    page: PageRequest = Depends(page_request),
    config: ReliabilityConfig = Depends(deployed_reliability_config),
) -> Page[PublicEvent]:
    """The network's events over the last week, newest first.

    Args:
        conn: A pooled connection, injected.
        page: ``limit`` and ``cursor``, already validated and decoded.
        config: The deployment's reliability configuration, which names the
            diagnoses and warnings read.

    Returns:
        One page of events, and the next cursor or null. An empty page is the
        answer for a week in which nothing was recorded.

    Raises:
        PublicError: ``invalid_query`` for a cursor this endpoint did not issue.
    """
    watch = watched_under(config)
    fetched = find_events(
        conn,
        since=platform_clock.utc_now() - timedelta(days=WINDOW_DAYS),
        diagnosed_under=(DIAGNOSIS_METHOD, config.diagnosis.sha256()),
        watched_under=(watch.watch_method, watch.watch_sha256),
        after=_after(page.cursor_key),
        limit=page.limit + 1,
    )
    trimmed = trim_overfetch(fetched, page.limit)

    next_cursor = None
    if trimmed.has_more_rows and trimmed.rows:
        last = trimmed.rows[-1]
        next_cursor = encode_cursor((last.at.isoformat(), last.kind, last.ref))

    return Page(
        items=[PublicEvent.from_row(row) for row in trimmed.rows],
        next_cursor=next_cursor,
    )


def _after(key: tuple[str, ...] | None) -> EventKey | None:
    """The cursor's last event, refused when it is not one this endpoint made."""
    if key is None:
        return None
    if len(key) != 3 or key[1] not in EVENT_KINDS:  # noqa: PLR2004 — at, kind, ref
        raise PublicError(INVALID_QUERY, "cursor is not a valid cursor.")
    try:
        at = datetime.fromisoformat(key[0])
    except ValueError:
        raise PublicError(INVALID_QUERY, "cursor is not a valid cursor.") from None
    if at.tzinfo is None:
        raise PublicError(INVALID_QUERY, "cursor is not a valid cursor.")
    return EventKey(at=at, kind=key[1], ref=key[2])
