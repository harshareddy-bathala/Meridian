"""``/api/v1/reliability`` — the network's reliability, as the platform counts it.

Thin by rule, as every public endpoint is: the report is counted by
``meridian.reliability.live`` from the stored classifications, the same report
``meridian reliability report`` prints, and this module only shapes it. Only
``meridian.reliability`` decides what a miss is (ARCHITECTURE.md rule 3).

The path was fixed in Stage 11 and answered ``not_yet_computed`` until now
(D-086); a client that branched on ``status`` reads ``computed`` here.

**The reliability file is read once per process**, as the metrics collector
reads it, and kept on ``app.state``: a public request costs no file read, and
an edit to the file takes effect when the processes that read it restart
together, not in the API alone while the jobs service classifies under the old
one. A file that is refused is a ``server_error`` naming the cause, never an
unhandled exception, and is read again on the next request.

Reference: docs/DECISIONS.md D-083, D-086, D-184, D-187.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request

from meridian.api import platform_clock
from meridian.api.dependencies import get_connection
from meridian.api.public.envelope import SERVER_ERROR, PublicError
from meridian.api.public.models.reliability import PublicReliability
from meridian.reliability.config import (
    ReliabilityConfig,
    ReliabilityConfigError,
    load_deployed_reliability_config,
)
from meridian.reliability.live import read_live_report
from meridian.store.stations import Connection

__all__ = ["router"]

_log = logging.getLogger(__name__)

router = APIRouter()


def deployed_reliability_config(request: Request) -> ReliabilityConfig:
    """The deployment's reliability configuration, read on first use and kept.

    Raises:
        PublicError: ``server_error``, if the file is refused. The reason is
            logged for the operator; the reader is told only which file.
    """
    held: ReliabilityConfig | None = getattr(
        request.app.state, "reliability_config", None
    )
    if held is None:
        try:
            held = load_deployed_reliability_config()
        except ReliabilityConfigError:
            _log.warning("reliability configuration refused", exc_info=True)
            raise PublicError(
                SERVER_ERROR,
                "The reliability configuration was refused; the platform's log "
                "says why.",
            ) from None
        request.app.state.reliability_config = held
    return held


@router.get("/reliability")
def reliability_summary(
    conn: Connection = Depends(get_connection, scope="function"),
    config: ReliabilityConfig = Depends(deployed_reliability_config),
) -> PublicReliability:
    """Both populations' figures over the SLO window ending now.

    Args:
        conn: A pooled connection, injected.
        config: The deployment's reliability configuration, injected.

    Returns:
        Every figure as a count over a count, the loss budget with its debits
        counted by reason, and each target judged, for measured and simulated
        stations apart.
    """
    report = read_live_report(conn, now=platform_clock.utc_now(), config=config)
    return PublicReliability.of(report)
