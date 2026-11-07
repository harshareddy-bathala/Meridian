"""The public read API, assembled — every endpoint module mounted under one router.

``meridian.api.app`` mounts this and needs to know nothing else about the
package. New endpoint groups are added here and nowhere else.

**This is deliberately not ``__init__.py``**, which is where the MSP package puts
the same thing. Importing *any* submodule of a package executes that package's
``__init__`` first, and ``meridian.api.errors`` imports this package's
``envelope`` to answer a failure in the right vocabulary. If the assembly lived
in ``__init__``, that one import would drag every endpoint module in with it —
and each of those imports ``meridian.api.dependencies``, which imports
``meridian.api.errors``, which had not finished loading. Keeping ``__init__``
free of imports makes the cycle impossible rather than merely absent.

Reference: docs/DECISIONS.md D-083, D-084.
"""

from __future__ import annotations

from fastapi import APIRouter

from meridian.api.public import (
    aggregates,
    areas,
    assignments,
    chain_warnings,
    conditions,
    events,
    losses,
    observations,
    passes,
    reliability,
    results,
    satellites,
    schedule,
    stations,
    tracks,
)
from meridian.api.public.envelope import API_PREFIX

__all__ = ["router"]

router = APIRouter(prefix=API_PREFIX, tags=["public"])

router.include_router(stations.router)
# Before the catalogue: `/satellites/{satellite_id}` would take `tracks` as an id.
router.include_router(tracks.router)
router.include_router(satellites.router)
router.include_router(passes.router)
router.include_router(assignments.router)
router.include_router(observations.router)
router.include_router(reliability.router)
router.include_router(aggregates.router)
router.include_router(areas.router)
router.include_router(losses.router)
router.include_router(chain_warnings.router)
router.include_router(schedule.router)
router.include_router(conditions.router)
router.include_router(events.router)
router.include_router(results.router)
