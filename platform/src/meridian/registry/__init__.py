"""Station registration, capabilities, tokens, health state, last-heartbeat age.

**The authority on whether a station was listening at a given moment.** Every
reliability metric depends on this being right, which is why
:meth:`Registry.was_listening` is declared here in Phase 1 even though
``meridian.reliability`` does not exist until Phase 3. Left undeclared until
then, it would be reconstructed from whatever the heartbeat table happened to
contain, and the distinction between "heard nothing" and "was not listening"
would erode exactly as docs/ARCHITECTURE.md warns.

This module declares interfaces and value types only. It performs no I/O,
holds no connection and contains no SQL — ``meridian.registry.psycopg_registry``
is the concrete implementation, and it is the only module that may compose
``meridian.store`` calls into MSP §4.1's decision table.

**Implementation status.** ``register``, ``authenticate`` and ``liveness`` are
implemented in ``psycopg_registry``. ``was_listening`` is *planned* — it reads
heartbeat rows, and no endpoint writes them yet. They are
declared now because their contract constrains the schema, not because anything
calls them yet (CLAUDE.local.md §8, interface before implementation).

Reference: docs/MSP-SPEC.md §4.1; docs/ARCHITECTURE.md; docs/DECISIONS.md
D-017, D-020, D-023, D-034.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

# Re-exported: `Liveness` and its thresholds live in the leaf module so that
# `meridian.config` can check a setting against them without an import cycle.
# Callers keep importing the name from this package.
from meridian.registry.liveness import Liveness
from meridian.store.stations import Capability

__all__ = [
    "InvalidInviteError",
    "Liveness",
    "Registration",
    "RegistrationRequest",
    "Registry",
    "UnknownStationError",
]


class UnknownStationError(LookupError):
    """No station with that id exists, or it has been soft-deleted.

    Raised only by methods asked about a *named* station — a caller reaches
    :meth:`Registry.liveness` with an id it already listed, so an id the
    registry cannot find is a caller bug rather than a state to branch on.
    Distinct from :class:`InvalidInviteError`, which is a protocol outcome a
    station is told about; this one never reaches the wire.

    A ``LookupError`` rather than a bare ``Exception``: "asked for something
    that is not there" is exactly what that base class means, so a caller
    already writing ``except LookupError`` around a lookup catches it.
    """

    def __init__(self, station_id: str) -> None:
        """Name the station that was not found."""
        super().__init__(f"no such station: {station_id}")
        self.station_id = station_id


class InvalidInviteError(Exception):
    """The invite token and/or registration key did not admit a registration.

    Covers every rejecting row of MSP §4.1's table without distinguishing
    which: an unknown invite, one already consumed, one out of D-023's
    recovery window, a registration key that does not match, and a bound
    invite (D-034) presented by the wrong station all raise this same error.
    One type because the wire response is identical for all of them —
    `403 invalid_invite` — and MSP §3 does not let a client learn why its
    invite was rejected, only that it was.
    """


@dataclass(frozen=True, slots=True)
class RegistrationRequest:
    """One MSP §4.1 ``register`` payload, already parsed and type-checked.

    Bundled into one dataclass rather than passed as separate parameters —
    the same reason ``store.stations.NewStation`` is: CLAUDE.local.md caps a
    function at four parameters (five, hard), and this payload alone has ten
    fields before ``capabilities`` is even counted.

    Producing one of these from the wire request — including turning MSP
    §4.1's ``horizon_mask`` array into ``Capability.horizon_mask_json`` text
    — is the API layer's job, not this module's.
    """

    invite_token: str
    registration_key: str
    name: str
    operator: str
    lat_deg: float
    lon_deg: float
    alt_m: float
    simulated: bool
    simulator_run_id: str | None
    seed: int | None
    capabilities: Sequence[Capability]
    client_implementation: str | None
    client_version: str | None


@dataclass(frozen=True, slots=True)
class Registration:
    """What a successful :meth:`Registry.register` call hands back.

    Not ``heartbeat_interval_s`` — that is ``Settings.heartbeat_interval_s``,
    a static config value the API layer adds to the response body; the
    registry has no station-specific reason to decide it.
    """

    station_id: str
    bearer_token: str


class Registry(Protocol):
    """Station registration, identity and liveness."""

    def register(self, request: RegistrationRequest) -> Registration:
        """Admit a station, or recover/rotate its credentials, per MSP §4.1.

        The invite and the presented ``registration_key`` together select
        exactly one outcome (MSP §4.1's table, condensed):

        * Unconsumed, unbound invite, any key — create a station, store the
          key hash, mint a token.
        * Consumed, unbound, in recovery, key matches the station that
          consumed it — same ``station_id``, newly minted token.
        * Bound to a station, unconsumed, key matches that station — same
          ``station_id``, newly minted token, D-023's window ignored.
        * Everything else — consumed and out of recovery, a key that does
          not match, or a bound invite presented by the wrong station —
          ``InvalidInviteError``.

        An invite whose ``expires_at`` has passed admits nothing, checked
        before the table is consulted at all. That covers withdrawal as well
        as lapsing, because ``store.invites.revoke_invite`` implements
        revocation by setting ``expires_at``. A bound invite is exempt from
        D-034's recovery *window*, not from its own expiry.

        **On the two recovery rows, this restores an identity — it does not
        re-register.** ``name``, ``operator``, the location, ``capabilities``
        and the client fields are read from the request and deliberately not
        written: recovery exists because a response was lost or an operator
        authorised a rotation, not because the station's description changed.
        A station that has genuinely moved is re-registered, not recovered.

        ``simulated`` is the one field that is not merely ignored. A station
        cannot change its own nature, so a value disagreeing with the stored
        row is ``InvalidInviteError`` rather than a silent discard (D-048).

        "In recovery" means ``last_heartbeat_at is null`` **and** the request
        arrives within ``Settings.registration_recovery_window_s`` of
        ``registered_at`` — both conditions, not either (D-023). A bound
        invite ignores the window entirely: the operator issuing it against a
        named ``station_id`` is the authorisation the window otherwise exists
        to require (D-034).

        Both the bearer token and the registration key are hashed with
        ``Settings.token_hash_pepper`` before being persisted — never the
        invite token, which carries no pepper (D-020) because it is already
        single-use and short-lived.

        Args:
            request: The parsed, validated registration payload.

        Returns:
            The new or rotated ``station_id`` and a freshly minted bearer
            token, in plaintext, exactly once — MSP §3.

        Raises:
            InvalidInviteError: None of the table's admitting rows apply.
        """
        ...

    def authenticate(self, bearer_token: str) -> str | None:
        """Return the ``station_id`` for a bearer token, or ``None``.

        Tokens are opaque secrets, not signed tokens (docs/DECISIONS.md
        D-017) — MSP §6 defines ``unauthorized`` as covering *revoked*
        tokens, and revocation needs the lookup this method performs anyway.

        The token is hashed with ``Settings.token_hash_pepper`` and the hash
        is *looked up*; the plaintext is never compared. There is therefore
        no Python-level comparison here to make constant-time, unlike the
        registration key in ``register()``, which is fetched and then
        compared and so uses ``hmac.compare_digest``.

        Args:
            bearer_token: The token exactly as presented in the
                ``Authorization: Bearer`` header, unhashed.

        Returns:
            The owning ``station_id``, or ``None`` when the token is unknown,
            revoked, or belongs to a deleted station. The three are
            deliberately indistinguishable — MSP §3 does not let a client
            learn which.
        """
        ...

    def liveness(self, station_id: str, *, now: datetime) -> Liveness:
        """Derive liveness from the age of the station's most recent heartbeat.

        **Derived on read, never stored** (D-054). A stored conclusion is only
        correct until the clock passes its next threshold, and nothing moves
        the clock on the platform's behalf — so a station that went quiet would
        keep reading ``online`` until some unrelated write refreshed it, which
        is precisely the case liveness exists to detect.

        Thresholds are absolute seconds fixed by SC-5, not multiples of
        ``Settings.heartbeat_interval_s``: ``stale`` at 60 s, ``offline`` at
        90 s. D-013 records the direction of that dependency deliberately — the
        success criterion sets the threshold rather than the other way round.

        Args:
            station_id: The station to classify. Already known to the caller,
                which is why an unknown one raises rather than returning a
                fifth value nobody would branch on.
            now: The instant to measure against, timezone-aware UTC. Passed
                rather than read inside, so a caller classifying a page of
                stations gives them all one instant and a test can state an age
                instead of sleeping for it.

        Returns:
            One of ``Liveness``' four values.

        Raises:
            UnknownStationError: No such station, or it is soft-deleted.
            ValueError: ``now`` is naive. CLAUDE.local.md §6 makes that a bug,
                not a tolerance.
        """
        ...

    def was_listening(
        self,
        station_id: str,
        satellite_id: str,
        centre_freq_hz: int,
        window: tuple[datetime, datetime],
    ) -> bool:
        """Whether heartbeats confirm this station was listening for this target.

        **This method is why absence can be interpreted at all.** Without a
        heartbeat asserting a station was tuned to a specific frequency for a
        specific satellite at a specific time, a missing observation means
        nothing — it could be a miss, or a station that was switched off.

        ``meridian.reliability`` calls this and nothing else to decide what counts
        as a miss. No other module may reimplement the judgement.
        """
        ...
