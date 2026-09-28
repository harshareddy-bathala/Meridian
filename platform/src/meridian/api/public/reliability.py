"""``/api/v1/reliability`` — the network's reliability, as the platform counts it.

Thin by rule, as every public endpoint is: the report is counted by
``meridian.reliability.live`` from the stored classifications, the same report
``meridian reliability report`` prints, and this module only shapes it. Only
``meridian.reliability`` decides what a miss is (ARCHITECTURE.md rule 3).

The path was fixed in Stage 11 and answered ``not_yet_computed`` until now
(D-086); a client that branched on ``status`` reads ``computed`` here.

Reference: docs/DECISIONS.md D-083, D-086, D-184, D-187.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from meridian.api import platform_clock
from meridian.api.dependencies import get_connection
from meridian.api.public.models.reliability import PublicReliability
from meridian.reliability.config import load_deployed_reliability_config
from meridian.reliability.live import read_live_report
from meridian.store.stations import Connection

__all__ = ["router"]

router = APIRouter()


@router.get("/reliability")
def reliability_summary(
    conn: Connection = Depends(get_connection, scope="function"),
) -> PublicReliability:
    """Both populations' figures over the SLO window ending now.

    Args:
        conn: A pooled connection, injected.

    Returns:
        Every figure as a count over a count, the loss budget with its debits
        counted by reason, and each target judged, for measured and simulated
        stations apart.
    """
    report = read_live_report(
        conn,
        now=platform_clock.utc_now(),
        config=load_deployed_reliability_config(),
    )
    return PublicReliability.of(report)
