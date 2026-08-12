"""The MSP endpoints — one module per operation of MSP §8.

This package assembles the four operation routers under a single ``/msp/v0``
parent that carries MSP §7's version check, so an endpoint added later cannot
forget it. Each operation module owns a bare router and is mounted here; the
parent is the only thing ``meridian.api.app`` needs to know about.

Every module in this package is **thin by rule**. It validates, calls, and
serialises; it holds no business logic and reaches no database directly. Where a
decision belongs to a service, the service makes it — ``register`` calls
:class:`meridian.registry.psycopg_registry.PsycopgRegistry` and ``observations``
calls :mod:`meridian.observations.ingest`, and neither route decides anything
those services already decide.

MSP §8 binds four endpoints to this prefix; `observations` is not built yet.

Reference: docs/MSP-SPEC.md §4.1, §4.2, §4.3, §4.4, §7, §8.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from meridian.api.msp import heartbeat, register, server_time
from meridian.api.versioning import require_msp_version

__all__ = ["router"]

router = APIRouter(
    prefix="/msp/v0",
    tags=["msp"],
    # Router-level, not per-route. Stage 3's completion gate is that a new MSP
    # endpoint cannot be added without the shared version, error and logging
    # behaviour — a dependency listed once on the parent is what makes that
    # structural rather than a thing a reviewer has to notice.
    dependencies=[Depends(require_msp_version)],
)

router.include_router(server_time.router)
router.include_router(register.router)
router.include_router(heartbeat.router)
