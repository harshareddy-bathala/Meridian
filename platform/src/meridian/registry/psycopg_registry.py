"""``PsycopgRegistry`` — the concrete ``Registry`` backed by the store layer.

Implements ``Registry.register()``, ``Registry.authenticate()`` and
``Registry.liveness()`` against ``meridian.store.stations`` and
``meridian.store.invites``, and ``Registry.was_listening()`` against
``meridian.store.heartbeats``.

This module holds MSP §4.1's actual decision logic: which of the six rows of
its recovery table a presented ``(invite_token, registration_key)`` pair
selects. Everything below it is store calls; everything above it — parsing
the wire request into a ``RegistrationRequest``, translating
``InvalidInviteError`` into a `403` — is the API layer's job.

Reference: docs/MSP-SPEC.md §4.1, §4.2; docs/DECISIONS.md D-017, D-020, D-023,
D-034, D-054, D-056.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from meridian.registry import (
    InvalidInviteError,
    ListeningQuery,
    Liveness,
    Registration,
    RegistrationRequest,
    UnknownStationError,
)
from meridian.registry.doppler_tolerance import doppler_tolerance_hz
from meridian.registry.liveness import derive_liveness
from meridian.store.heartbeats import (
    ListeningEvidenceQuery,
    has_listening_evidence,
)
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
    find_station_heartbeat,
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
    """The whole ``Registry`` protocol, backed by the store layer.

    Complete: all four ``Registry`` methods are implemented here. Deliberately
    not declared to inherit from ``Registry`` — Python resolves the protocol
    structurally, so the annotation would buy nothing that ``mypy`` does not
    already check at every call site that expects a ``Registry``.

    Constructed per request with an open connection, the two ``Settings``
    fields it needs, and the instant to judge time-dependent rows against —
    the shape a FastAPI ``Depends()`` builds it with.

    ``now_utc`` is taken at construction rather than read from the clock
    inside each decision: one request is one instant, and a registry built
    with an explicit instant is testable without freezing the system clock.
    """

    def __init__(
        self,
        conn: Connection,
        *,
        pepper: str,
        recovery_window_s: int,
        now_utc: datetime,
    ) -> None:
        """Bind this instance to one request's connection, configuration and instant."""
        self._conn = conn
        self._pepper = pepper
        self._recovery_window_s = recovery_window_s
        self._now_utc = now_utc

    def register(self, request: RegistrationRequest) -> Registration:
        """See :meth:`meridian.registry.Registry.register` for the contract."""
        invite_hash = hash_invite_token(request.invite_token)
        invite = find_invite_by_hash(self._conn, invite_hash)
        if invite is None:
            raise InvalidInviteError("no such invite")

        # Checked before any row of MSP §4.1's table is selected: an operator
        # who ran `meridian invite revoke` has withdrawn the invite for every
        # outcome, recovery included, and a bound invite is exempt from
        # D-034's *window* but not from its own expiry. `is_expired` is
        # computed by the database rather than compared here, because
        # `revoke_invite` writes `expires_at` with the database's clock —
        # see Invite.is_expired and D-046.
        if invite.is_expired:
            raise InvalidInviteError("invite expired or withdrawn")

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

    def liveness(self, station_id: str, *, now: datetime) -> Liveness:
        """See :meth:`meridian.registry.Registry.liveness` for the contract.

        Two lines of work, deliberately: read the instant, then classify it.
        The classification lives in :func:`meridian.registry.liveness.
        derive_liveness`, which is pure and has its own unit tests, so the
        thresholds SC-5 fixes are not buried behind a database.
        """
        heartbeat = find_station_heartbeat(self._conn, station_id)
        if heartbeat is None:
            raise UnknownStationError(station_id)
        return derive_liveness(heartbeat.last_heartbeat_at, now=now)

    def was_listening(self, query: ListeningQuery) -> bool:
        """See :meth:`meridian.registry.Registry.was_listening` for the contract.

        The one domain judgement made here is how wide "the same frequency" is.
        A station retunes continuously across a pass, so it reports a
        Doppler-shifted frequency rather than the assignment's nominal one, and
        the store layer is handed an explicit range rather than a centre and a
        rule for widening it (D-056).
        """
        tolerance_hz = doppler_tolerance_hz(query.centre_freq_hz)
        window_start, window_end = query.window
        return has_listening_evidence(
            self._conn,
            ListeningEvidenceQuery(
                station_id=query.station_id,
                satellite_id=query.satellite_id,
                mode=query.mode,
                freq_min_hz=query.centre_freq_hz - tolerance_hz,
                freq_max_hz=query.centre_freq_hz + tolerance_hz,
                window_start=window_start,
                window_end=window_end,
            ),
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
            client_implementation=request.client_implementation,
            client_version=request.client_version,
        )
        with self._conn.transaction():
            insert_station(self._conn, new_station, request.capabilities)
            self._consume_or_raise(invite_hash, station_id)
        return Registration(station_id=station_id, bearer_token=bearer_plaintext)

    def _recover_unbound_station(
        self, station_id: str, request: RegistrationRequest
    ) -> Registration:
        """The consumed-unbound rows: eligible only inside D-023's window."""
        info = self._recovery_info_or_raise(station_id)
        if not is_recovery_eligible(
            last_heartbeat_at=info.last_heartbeat_at,
            registered_at=info.registered_at,
            now=self._now_utc,
            window_s=self._recovery_window_s,
        ):
            raise InvalidInviteError("registration recovery window has closed")
        if not self._key_matches(info, request.registration_key):
            raise InvalidInviteError("registration key does not match")
        self._reject_simulated_mismatch(info, request)
        return self._mint_and_rotate(station_id)

    def _recover_bound_station(
        self, station_id: str, invite_hash: bytes, request: RegistrationRequest
    ) -> Registration:
        """The unconsumed-bound rows: D-034 ignores the recovery window."""
        info = self._recovery_info_or_raise(station_id)
        if not self._key_matches(info, request.registration_key):
            raise InvalidInviteError("registration key does not match")
        self._reject_simulated_mismatch(info, request)
        with self._conn.transaction():
            registration = self._mint_and_rotate(station_id)
            self._consume_or_raise(invite_hash, station_id)
        return registration

    def _consume_or_raise(self, invite_hash: bytes, station_id: str) -> None:
        """Consume the invite, or reject the registration that lost the race."""
        # consume_invite's `where consumed_at is null` is the only thing
        # stopping one invite admitting two stations, and it reports the
        # outcome by return value rather than by raising. Discarding it would
        # leave both racing requests believing they had won, which is exactly
        # the property D-020 says invite_tokens exists to provide. Raising
        # inside the caller's transaction rolls the station row back with it.
        if not consume_invite(
            self._conn, token_sha256=invite_hash, station_id=station_id
        ):
            raise InvalidInviteError("invite consumed concurrently")

    def _reject_simulated_mismatch(
        self, info: StationRecoveryInfo, request: RegistrationRequest
    ) -> None:
        """Refuse a recovery that claims a different ``simulated`` than the row."""
        # Recovery restores an identity; it does not re-register (D-048). Name,
        # location, capabilities and client info on the request are ignored on
        # this path, but `simulated` cannot be, because ignoring it silently is
        # what lets a simulated station recover as a real one and file
        # measured-looking data ever after - CLAUDE.md's fifth rule.
        if request.simulated != info.simulated:
            raise InvalidInviteError("simulated flag does not match the station")

    def _recovery_info_or_raise(self, station_id: str) -> StationRecoveryInfo:
        info = find_station_for_recovery(self._conn, station_id)
        if info is None:  # pragma: no cover — the invite's own FK guarantees this
            raise InvalidInviteError("invite names no station")
        return info

    def _key_matches(self, info: StationRecoveryInfo, registration_key: str) -> bool:
        """Whether the presented registration key hashes to the stored value."""
        presented = hash_with_pepper(self._pepper, registration_key)
        # compare_digest, not `==`: this key authorises minting a new bearer
        # token on an existing station (D-023, D-034), so a timing oracle on
        # it is a credential-recovery path, not merely an information leak.
        return hmac.compare_digest(presented, info.registration_key_sha256)

    def _mint_and_rotate(self, station_id: str) -> Registration:
        bearer_plaintext, bearer_hash = generate_bearer_token(self._pepper)
        rotate_station_token(
            self._conn, station_id=station_id, token_sha256=bearer_hash
        )
        return Registration(station_id=station_id, bearer_token=bearer_plaintext)
