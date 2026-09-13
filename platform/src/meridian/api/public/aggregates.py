"""``/api/v1/aggregates`` — network-wide metrics, once they are computed.

Answered with :class:`NotYetComputed` (D-086). The aggregates the roadmap lists —
pass completeness, element-set divergence, timing error, scheduler performance —
are analytical views over downsampled continuous aggregates, which Stage 19
creates. Counting rows here in the meantime would publish numbers nobody has
defined, and a dashboard would chart them as though somebody had.

Reference: docs/DECISIONS.md D-083, D-086; docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md
Stage 19.
"""

from __future__ import annotations

from fastapi import APIRouter

from meridian.api.public.models.not_yet_computed import NotYetComputed

__all__ = ["AGGREGATES_STAGE", "router"]

AGGREGATES_STAGE = 19
"""Stage 19 — complete deferred storage, whose analytical views these are."""

router = APIRouter()


@router.get("/aggregates")
def network_aggregates() -> NotYetComputed:
    """Network-wide aggregate metrics: not yet computed.

    Returns:
        A placeholder with no numeric field, naming the stage that computes it.
    """
    return NotYetComputed(
        reason=(
            "Aggregate metrics are analytical views over continuous aggregates"
            " that have not been defined or created yet."
        ),
        available_from_stage=AGGREGATES_STAGE,
    )
