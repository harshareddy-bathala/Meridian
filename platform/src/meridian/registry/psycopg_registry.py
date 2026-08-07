"""``PsycopgRegistry`` — the concrete ``Registry`` backed by the store layer.

Implements ``Registry.register()`` and ``Registry.authenticate()`` against
``meridian.store.stations`` and ``meridian.store.invites``. ``liveness()`` and
``was_listening()`` are not implemented here — Stage 5, once heartbeats carry
data to derive them from.

This module holds MSP §4.1's actual decision logic: which of the six rows of
its recovery table a presented ``(invite_token, registration_key)`` pair
selects. Everything below it is store calls; everything above it — parsing
the wire request into a ``RegistrationRequest``, translating
``InvalidInviteError`` into a `403` — is the API layer's job.

Reference: docs/MSP-SPEC.md §4.1; docs/DECISIONS.md D-017, D-020, D-023, D-034.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from meridian.registry import InvalidInviteError, Registration, RegistrationRequest
from meridian.store.invites import (
    consume_invite,
    find_invite_by_hash,
    hash_invite_token,
)
from meridian.store.stations import (
    Connection,
    NewStation,
    StationRecoveryInfo,
    find_station_for_recovery,
    find_station_id_by_token_hash,
    insert_station,
    rotate_station_token,
)

__all__ = [
    "PsycopgRegistry",
    "generate_bearer_token",
    "generate_station_id",
    "hash_with_pepper",
    "is_recovery_eligible",
]


def hash_with_pepper(pepper: str, secret: str) -> bytes:
    """``sha256(pepper ‖ secret)`` — the peppered hash D-017 and D-023 both use.

    Unlike an invite token (:func:`meridian.store.invites.hash_invite_token`),
    a station's bearer token and registration key are long-lived credentials,
    and the pepper is what keeps a read-only database leak from being enough
    on its own to forge one.
    """
    return hashlib.sha256(pepper.encode("utf-8") + secret.encode("utf-8")).digest()


def generate_station_id() -> str:
    """A fresh station identifier: ``st_`` plus 6 hex characters.

    Matches the shape MSP-SPEC.md's own examples use (``st_7fa3c1``). Not
    cryptographically unguessable by design — a ``station_id`` is public,
    sent on the wire in every message. Collision resistance is the only
    property that matters here, and 24 bits is ample for the number of
    stations Phase 1 through Phase 3 will ever register.
    """
    return f"st_{secrets.token_hex(3)}"


def generate_bearer_token(pepper: str) -> tuple[str, bytes]:
    """A fresh bearer token: the plaintext to return once, and its peppered hash.

    The station-credential equivalent of
    :func:`meridian.store.invites.generate_invite_token` — the same
    construction, 32 bytes from ``secrets.token_urlsafe`` — but peppered,
    because unlike an invite this credential is long-lived (D-017).
    """
    plaintext = secrets.token_urlsafe(32)
    return plaintext, hash_with_pepper(pepper, plaintext)


def is_recovery_eligible(
    *,
    last_heartbeat_at: datetime | None,
    registered_at: datetime,
    now: datetime,
    window_s: int,
) -> bool:
    """D-023: recovery requires no heartbeat yet **and** being inside the window.

    Both conditions, not either — a station that has heartbeat holds a
    working token by definition, and a station that never heartbeat but
    registered months ago would otherwise leave a consumed invite live
    forever for anyone who finds it.
    """
    if last_heartbeat_at is not None:
        return False
    return now - registered_at <= timedelta(seconds=window_s)


class PsycopgRegistry:
    """``Registry.register()`` and ``.authenticate()``, backed by the store layer.

    A **partial** implementation: ``liveness()`` and ``was_listening()`` are
    not here. Deliberately not declared to inherit from ``Registry`` —
    Python resolves the protocol structurally, and there is no benefit to
    forcing a subclass relationship before the class actually satisfies it.
    Stage 5 completes it once heartbeat data exists to derive them from.

    Constructed per request with an open connection and the two ``Settings``
    fields it needs — the shape a FastAPI ``Depends()`` builds it with.
    """

    def __init__(
        self, conn: Connection, *, pepper: str, recovery_window_s: int
    ) -> None:
        """Bind this instance to one request's connection and configuration."""
        self._conn = conn
        self._pepper = pepper
        self._recovery_window_s = recovery_window_s

    def register(self, request: RegistrationRequest) -> Registration:
        """See :meth:`meridian.registry.Registry.register` for the contract."""
        invite_hash = hash_invite_token(request.invite_token)
        invite = find_invite_by_hash(self._conn, invite_hash)
        if invite is None:
            raise InvalidInviteError("no such invite")

        if invite.issued_for_station_id is not None:
            if invite.consumed_at is not None:
                raise InvalidInviteError("bound invite already consumed")
            return self._recover_bound_station(
                invite.issued_for_station_id, invite_hash, request
            )

        if invite.consumed_at is None:
            return self._create_station(invite_hash, request)

        consumed_by = invite.consumed_by_station_id
        if consumed_by is None:  # pragma: no cover — invite_consumed_together CHECK
            raise InvalidInviteError("invite is consumed with no owning station")
        return self._recover_unbound_station(consumed_by, request)

    def authenticate(self, bearer_token: str) -> str | None:
        """See :meth:`meridian.registry.Registry.authenticate` for the contract."""
        return find_station_id_by_token_hash(
            self._conn, hash_with_pepper(self._pepper, bearer_token)
        )

    def _create_station(
        self, invite_hash: bytes, request: RegistrationRequest
    ) -> Registration:
        """MSP §4.1's first row: unconsumed, unbound — a brand new station."""
        station_id = generate_station_id()
        bearer_plaintext, bearer_hash = generate_bearer_token(self._pepper)
        new_station = NewStation(
            station_id=station_id,
            name=request.name,
            operator=request.operator,
            lat_deg=request.lat_deg,
            lon_deg=request.lon_deg,
            alt_m=request.alt_m,
            token_sha256=bearer_hash,
            registration_key_sha256=hash_with_pepper(
                self._pepper, request.registration_key
            ),
            simulated=request.simulated,
            simulator_run_id=request.simulator_run_id,
            seed=request.seed,
            client_impl=request.client_impl,
            client_version=request.client_version,
        )
        with self._conn.transaction():
            insert_station(self._conn, new_station, request.capabilities)
            consume_invite(self._conn, token_sha256=invite_hash, station_id=station_id)
        return Registration(station_id=station_id, bearer_token=bearer_plaintext)

    def _recover_unbound_station(
        self, station_id: str, request: RegistrationRequest
    ) -> Registration:
        """The consumed-unbound rows: eligible only inside D-023's window."""
        info = self._recovery_info_or_raise(station_id)
        if not is_recovery_eligible(
            last_heartbeat_at=info.last_heartbeat_at,
            registered_at=info.registered_at,
            now=datetime.now(UTC),
            window_s=self._recovery_window_s,
        ):
            raise InvalidInviteError("registration recovery window has closed")
        if not self._key_matches(info, request.registration_key):
            raise InvalidInviteError("registration key does not match")
        return self._mint_and_rotate(station_id)

    def _recover_bound_station(
        self, station_id: str, invite_hash: bytes, request: RegistrationRequest
    ) -> Registration:
        """The unconsumed-bound rows: D-034 ignores the recovery window."""
        info = self._recovery_info_or_raise(station_id)
        if not self._key_matches(info, request.registration_key):
            raise InvalidInviteError("registration key does not match")
        with self._conn.transaction():
            registration = self._mint_and_rotate(station_id)
            consume_invite(self._conn, token_sha256=invite_hash, station_id=station_id)
        return registration

    def _recovery_info_or_raise(self, station_id: str) -> StationRecoveryInfo:
        info = find_station_for_recovery(self._conn, station_id)
        if info is None:  # pragma: no cover — the invite's own FK guarantees this
            raise InvalidInviteError("invite names no station")
        return info

    def _key_matches(self, info: StationRecoveryInfo, registration_key: str) -> bool:
        presented = hash_with_pepper(self._pepper, registration_key)
        return presented == info.registration_key_sha256

    def _mint_and_rotate(self, station_id: str) -> Registration:
        bearer_plaintext, bearer_hash = generate_bearer_token(self._pepper)
        rotate_station_token(
            self._conn, station_id=station_id, token_sha256=bearer_hash
        )
        return Registration(station_id=station_id, bearer_token=bearer_plaintext)
