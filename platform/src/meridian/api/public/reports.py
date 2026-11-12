"""``/api/v1/reports/weekly`` — the network's latest settled week, in plain words.

The reports page reads it. The text is template ``network-weekly-1``, rendered
on the request from the stored classifications under the deployment's own
reliability configuration, so it says what ``/results`` and ``/aggregates``
count (D-367). Nothing is stored for it.

**A settled week only.** The latest is the default. An earlier one is asked
for by its Monday, and a day that is not a Monday, or a week still settling,
is an ``invalid_query`` that says which.

Reference: docs/DECISIONS.md D-086, D-182, D-306, D-367.
"""

from __future__ import annotations

from datetime import UTC, date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from meridian.api import platform_clock
from meridian.api.dependencies import get_connection
from meridian.api.public.envelope import INVALID_QUERY, PublicError
from meridian.api.public.models.network_report import PublicNetworkWeekly
from meridian.api.public.reliability import deployed_reliability_config
from meridian.notifications.network import (
    CURRENT_NETWORK,
    network_inputs_sha256,
    render_network,
)
from meridian.notifications.network_reading import (
    latest_settled_week,
    network_week_facts,
)
from meridian.notifications.templates import content_sha256
from meridian.reliability.config import ReliabilityConfig
from meridian.store.network_week_reads import first_registered_at
from meridian.store.stations import Connection

__all__ = ["router"]

router = APIRouter()

_WEEK = timedelta(days=7)


@router.get("/reports/weekly")
def network_weekly(
    conn: Connection = Depends(get_connection, scope="function"),
    config: ReliabilityConfig = Depends(deployed_reliability_config),
    week: Annotated[date | None, Query()] = None,
) -> PublicNetworkWeekly:
    """The network's week from ``week``, or the latest settled one.

    Raises:
        PublicError: ``invalid_query`` for a day that is not a Monday, or a
            week that has not settled yet.
    """
    now = platform_clock.utc_now()
    latest = latest_settled_week(now, config)
    monday = latest if week is None else week
    if monday.weekday() != 0:
        raise PublicError(INVALID_QUERY, "week is not a Monday.")
    if monday > latest:
        raise PublicError(
            INVALID_QUERY,
            f"week has not settled yet; the latest settled week is {latest}.",
        )
    facts = network_week_facts(conn, monday, config)
    rendered = render_network(CURRENT_NETWORK, facts)
    first = first_registered_at(conn)
    earliest = None if first is None else _monday_of(first.astimezone(UTC).date())
    previous = monday - _WEEK
    return PublicNetworkWeekly(
        template=CURRENT_NETWORK,
        period_start=facts.period_start,
        period_end=facts.period_end,
        previous_week=previous
        if earliest is not None and previous >= earliest
        else None,
        as_of=now,
        includes_simulated=facts.simulated.stations > 0,
        subject=rendered.subject,
        body=rendered.body,
        inputs_sha256=network_inputs_sha256(facts).hex(),
        content_sha256=content_sha256(rendered).hex(),
    )


def _monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())
