"""``POST /msp/v0/register`` — admitting a station, or recovering its credentials.

The decision — create, recover, rotate, or reject — belongs entirely to
:class:`meridian.registry.psycopg_registry.PsycopgRegistry`. This module is the
translation either side of that call.

Reference: docs/MSP-SPEC.md §4.1; docs/DECISIONS.md D-006, D-023, D-034, D-048.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from meridian.api import platform_clock
from meridian.api.dependencies import get_connection, get_settings
from meridian.api.errors import INVALID_INVITE, MspError
from meridian.api.models.registration import RegisterRequestBody, RegisterResponseBody
from meridian.config import Settings
from meridian.registry import InvalidInviteError
from meridian.registry.psycopg_registry import PsycopgRegistry
from meridian.store.stations import Connection

__all__ = ["router"]

_log = logging.getLogger(__name__)

router = APIRouter()

INVALID_INVITE_MESSAGE = (
    "Invite token and registration key did not admit a registration."
)
"""Fixed and generic, never ``str(exc)``.

``PsycopgRegistry``'s internal reasons ("registration key does not match",
"bound invite already consumed") are exactly the distinguishing detail MSP §3
says a client must not learn — it may know an invite was rejected, never why.
The specific reason is logged server-side instead, matching how
``meridian.api.errors``'s validation handler already treats a rejected
``register`` body: generic to the client, specific in the log.
"""


@router.post("/register")
def register(
    body: RegisterRequestBody,
    conn: Connection = Depends(get_connection),
    settings: Settings = Depends(get_settings),
) -> RegisterResponseBody:
    """Admit a station, or recover/rotate its credentials, per MSP §4.1.

    ``heartbeat_interval_s`` in the response is ``Settings.heartbeat_interval_s``,
    not something the registry decides per station.

    Returns:
        ``{"station_id": ..., "token": ..., "heartbeat_interval_s": ...}`` —
        MSP §4.1's response, exactly.

    Raises:
        MspError: ``invalid_invite`` (403) when the invite token and
            registration key together admit no row of MSP §4.1's table.
    """
    registry = PsycopgRegistry(
        conn,
        pepper=settings.token_hash_pepper,
        recovery_window_s=settings.registration_recovery_window_s,
        now_utc=platform_clock.utc_now(),
    )
    try:
        result = registry.register(body.to_registration_request())
    except InvalidInviteError as exc:
        _log.info("registration rejected: %s", exc)
        raise MspError(INVALID_INVITE, INVALID_INVITE_MESSAGE) from exc

    return RegisterResponseBody(
        station_id=result.station_id,
        token=result.bearer_token,
        heartbeat_interval_s=settings.heartbeat_interval_s,
    )
