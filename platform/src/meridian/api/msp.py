"""The MSP endpoints.

One router mounted at ``/msp/v0``, carrying MSP §7's version check as a
router-level dependency so that an endpoint added later cannot forget it. MSP §8
binds four endpoints to this prefix; this module currently implements ``time``,
and the other three arrive with the stages that can serve them.

This module is **thin by rule**. It validates, calls, and serialises; it holds no
business logic and reaches no database directly. Where a decision belongs to a
service, the service makes it.

Reference: docs/MSP-SPEC.md §7, §8; docs/DECISIONS.md D-016.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from meridian.api.versioning import require_msp_version

__all__ = ["router"]

router = APIRouter(
    prefix="/msp/v0",
    tags=["msp"],
    # Router-level, not per-route. Stage 3's completion gate is that a new MSP
    # endpoint cannot be added without the shared version, error and logging
    # behaviour — a dependency listed once on the router is what makes that
    # structural rather than a thing a reviewer has to notice.
    dependencies=[Depends(require_msp_version)],
)


def utc_now() -> datetime:
    """The platform clock, as timezone-aware UTC.

    A named function rather than a call inline, so a test can substitute it and
    assert on an exact wire string instead of on a value that moves while it is
    being compared.
    """
    return datetime.now(UTC)


def format_server_time(now: datetime) -> str:
    """Render an instant in the form MSP §8 shows on the wire.

    MSP §8's example is ``2026-08-14T09:31:02Z``. Python's ``isoformat`` writes
    the offset as ``+00:00``, which is the same instant and a different string —
    and a microcontroller client comparing suffixes, or one whose parser accepts
    only the ``Z`` form, sees a different thing. The specification shows one form,
    so the platform emits that one.

    Args:
        now: A timezone-aware UTC instant.

    Returns:
        ISO-8601 with millisecond precision and a ``Z`` suffix.

    Raises:
        ValueError: If ``now`` is naive. A naive datetime here would be rendered
            as though it were UTC and would silently publish a wrong clock to
            every station estimating its offset against it.
    """
    if now.tzinfo is None:
        raise ValueError("server_time must be timezone-aware UTC")
    return now.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@router.get("/time")
def get_time() -> dict[str, str]:
    """Platform time, for a station estimating its clock offset.

    **Unauthenticated, and touches no database.** A station that has lost its
    token still needs to establish clock offset before it can re-register, and
    the response contains nothing that is not already public (MSP §8). It is the
    cheapest endpoint in the system and the only one that answers without reading
    a row.

    Returns:
        ``{"server_time": "2026-08-14T09:31:02.123Z"}`` — one field, as MSP §8
        specifies, and no others.

    Note:
        The station pairs this with its own send and receive instants to compute
        ``clock_offset_s = platform clock − station clock`` (D-025). The
        platform states only its own time; it never computes a station's offset,
        because only the station knows when it sent the request.
    """
    return {"server_time": format_server_time(utc_now())}
