"""``/api/v1/aggregates`` — today's passes, and a week by day, across the network.

The dashboard's live page reads this for its counts and its small charts. The
figures are counted by ``meridian.reliability.aggregates`` from the stored
classifications, under the same rules and configuration as ``/reliability``;
this module only shapes them (D-362).

The path was fixed in Stage 11 and answered ``not_yet_computed`` (D-086),
waiting for analytical views Stage 19 answered elsewhere (D-177). It now
answers ``computed``, and a client that branched on ``status`` keeps working.

The reliability file is read once per process and kept, as ``/reliability``
reads it, through the same dependency.

Reference: docs/DECISIONS.md D-083, D-086, D-177, D-184, D-362.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from meridian.api import platform_clock
from meridian.api.dependencies import get_connection
from meridian.api.public.models.aggregates import PublicAggregates
from meridian.api.public.reliability import deployed_reliability_config
from meridian.reliability.aggregates import read_live_aggregates
from meridian.reliability.config import ReliabilityConfig
from meridian.store.stations import Connection

__all__ = ["router"]

router = APIRouter()


@router.get("/aggregates")
def network_aggregates(
    conn: Connection = Depends(get_connection, scope="function"),
    config: ReliabilityConfig = Depends(deployed_reliability_config),
) -> PublicAggregates:
    """Today's passes by what became of them, and the week's figures by day.

    Args:
        conn: A pooled connection, injected.
        config: The deployment's reliability configuration, injected.

    Returns:
        For measured and simulated stations apart: today's scheduled, closed,
        classified and waiting passes, and seven days of capture rate and loss
        budget, each day marked settled or not.
    """
    aggregates = read_live_aggregates(conn, now=platform_clock.utc_now(), config=config)
    return PublicAggregates.of(aggregates)
