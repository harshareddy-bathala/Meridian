"""``/api/v1/reliability`` — the reliability summary, once there is one.

Fixed in the URL surface now and answered with :class:`NotYetComputed` (D-086),
so the path does not move when the computation lands. Only ``meridian.reliability``
may decide what counts as a miss (ARCHITECTURE.md rule 3), and that module is an
interface until Stage 20 — publishing a figure before then would mean deciding it
here, which is the one thing this layer must not do.

Reference: docs/DECISIONS.md D-083, D-086.
"""

from __future__ import annotations

from fastapi import APIRouter

from meridian.api.public.models.not_yet_computed import NotYetComputed

__all__ = ["RELIABILITY_STAGE", "router"]

RELIABILITY_STAGE = 20
"""Stage 20 — reliability and loss accounting."""

router = APIRouter()


@router.get("/reliability")
def reliability_summary() -> NotYetComputed:
    """The network's reliability summary: not yet computed.

    Returns:
        A placeholder with no numeric field, naming the stage that computes it.
    """
    return NotYetComputed(
        reason=(
            "Reliability is measured by the reliability layer, which does not"
            " compute anything yet; a miss is only counted once a heartbeat"
            " confirms the station was listening."
        ),
        available_from_stage=RELIABILITY_STAGE,
    )
