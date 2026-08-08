"""The MSP endpoints.

One router mounted at ``/msp/v0``, carrying MSP §7's version check as a
router-level dependency so that an endpoint added later cannot forget it. MSP §8
binds four endpoints to this prefix; this module currently implements ``time``
and ``register``, and the other two arrive with the stages that can serve them.

This module is **thin by rule**. It validates, calls, and serialises; it holds no
business logic and reaches no database directly. Where a decision belongs to a
service, the service makes it — ``register`` calls
:class:`meridian.registry.psycopg_registry.PsycopgRegistry` and does nothing MSP
§4.1's table itself does not already decide.

Reference: docs/MSP-SPEC.md §4.1, §7, §8; docs/DECISIONS.md D-016.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from meridian.api.dependencies import get_connection, get_settings
from meridian.api.errors import INVALID_INVITE, MspError
from meridian.api.models.registration import RegisterRequestBody, RegisterResponseBody
from meridian.api.versioning import require_msp_version
from meridian.config import Settings
from meridian.registry import InvalidInviteError
from meridian.registry.psycopg_registry import PsycopgRegistry
from meridian.store.stations import Connection

__all__ = ["router"]

_log = logging.getLogger(__name__)

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


@router.post("/register")
def register(
    body: RegisterRequestBody,
    conn: Connection = Depends(get_connection),
    settings: Settings = Depends(get_settings),
) -> RegisterResponseBody:
    """Admit a station, or recover/rotate its credentials, per MSP §4.1.

    The decision — create, recover, rotate, or reject — is entirely
    :class:`PsycopgRegistry`'s; this handler's job is the translation either
    side of that call. ``heartbeat_interval_s`` in the response is
    ``Settings.heartbeat_interval_s``, not something the registry decides
    per station.

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
