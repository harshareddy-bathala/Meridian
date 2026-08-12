"""``GET /msp/v0/time`` — the platform's clock, for a station estimating its offset.

The cheapest endpoint in the system and the only one that answers without
reading a row. The clock itself and its wire rendering live in
:mod:`meridian.api.platform_clock`, which the other operations stamp their
responses with too.

Reference: docs/MSP-SPEC.md §8; docs/DECISIONS.md D-016, D-025.
"""

from __future__ import annotations

from fastapi import APIRouter

from meridian.api import platform_clock

__all__ = ["router"]

router = APIRouter()


@router.get("/time")
def get_time() -> dict[str, str]:
    """Platform time, for a station estimating its clock offset.

    **Unauthenticated, and touches no database.** A station that has lost its
    token still needs to establish clock offset before it can re-register, and
    the response contains nothing that is not already public (MSP §8).

    Returns:
        ``{"server_time": "2026-08-14T09:31:02.123Z"}`` — one field, as MSP §8
        specifies, and no others.

    Note:
        The station pairs this with its own send and receive instants to compute
        ``clock_offset_s = platform clock − station clock`` (D-025). The
        platform states only its own time; it never computes a station's offset,
        because only the station knows when it sent the request.

        ``platform_clock.utc_now()`` is called through the module rather than
        imported by name, so a test substituting the clock reaches this call too.
    """
    return {"server_time": platform_clock.format_server_time(platform_clock.utc_now())}
