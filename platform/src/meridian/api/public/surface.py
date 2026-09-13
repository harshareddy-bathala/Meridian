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

from meridian.api.public import aggregates, reliability, satellites, stations
from meridian.api.public.envelope import API_PREFIX

__all__ = ["router"]

router = APIRouter(prefix=API_PREFIX, tags=["public"])

router.include_router(stations.router)
router.include_router(satellites.router)
router.include_router(reliability.router)
router.include_router(aggregates.router)
