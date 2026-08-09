"""Station registration, capabilities, tokens, health state, last-heartbeat age.

**The authority on whether a station was listening at a given moment.** Every
reliability metric depends on this being right, which is why
:meth:`Registry.was_listening` is declared here in Phase 1 even though
``meridian.reliability`` does not exist until Phase 3. Left undeclared until
then, it would be reconstructed from whatever the heartbeat table happened to
contain, and the distinction between "heard nothing" and "was not listening"
would erode exactly as docs/ARCHITECTURE.md warns.

This module declares interfaces and value types only. It performs no I/O, holds
no connection and contains no SQL, so it can be read to learn what the registry
promises without reading any SQL at all.
``meridian.registry.psycopg_registry`` is the concrete implementation: MSP
§4.1's decision table is composed there, out of ``meridian.store`` calls.

**Implementation status.** All four methods are implemented in
``psycopg_registry``. ``was_listening`` has no production caller yet — the
module that consumes it, ``meridian.reliability``, is Phase 3 — so its
correctness rests entirely on ``tests/integration/test_was_listening.py``
rather than on anything exercising it in anger.

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
    "ListeningQuery",
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
class ListeningQuery:
    """One question for :meth:`Registry.was_listening`.

    Bundled rather than passed as five parameters: CLAUDE.local.md §2 caps a
    function at four (five, hard) and says to take a dataclass beyond that.
    ``mode`` is the field that pushed it over, and it is not optional — D-028
    stored ``heartbeats.listening_mode`` precisely so this question could be
    asked honestly, because a station tuned to the right frequency running the
    wrong demodulator did not observe the pass.
    """

    station_id: str
    satellite_id: str
    centre_freq_hz: int
    """The frequency the *assignment* named, not the one the station reported.

    Matched within a Doppler-sized tolerance rather than exactly — see
    ``registry.doppler_tolerance``. A station retunes across a pass, so an
    exact comparison would answer False for every station that works.
    """

    mode: str
    window: tuple[datetime, datetime]
    """Start and end, timezone-aware UTC, compared against ``received_at``."""


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

    def was_listening(self, query: ListeningQuery) -> bool:
        """Whether heartbeats confirm this station was listening for this target.

        **This method is why absence can be interpreted at all.** Without a
        heartbeat asserting a station was tuned to a specific frequency for a
        specific satellite at a specific time, a missing observation means
        nothing — it could be a miss, or a station that was switched off.

        ``meridian.reliability`` decides what counts as a miss by calling this
        and nothing else, so the answer to "was the station working?" comes from
        one place and every reliability figure in the project rests on it.

        Four things must hold together, which is the roadmap's Stage 5 list:
        the heartbeat overlaps the window, names the satellite, reports a
        frequency within Doppler of the one asked about, and reports the same
        mode. A fifth is not a property of the heartbeat alone — the
        assignment it names must be one actually issued to this station, or a
        station could assert listening against an id it invented.

        Args:
            query: What is being asked about. A dataclass rather than five
                parameters, per CLAUDE.local.md §2 (D-056).

        Returns:
            True when at least one heartbeat confirms all of the above.

        Note:
            **Overlap, not coverage.** One confirming heartbeat inside the
            window satisfies this, so a station that listened for thirty
            seconds of a twelve-minute pass and stopped counts as having
            listened. That is the roadmap's wording and a known limit;
            refining it to a coverage fraction changes this return type and
            belongs with the reliability layer that consumes it (D-056).

            Timing is judged on the platform's ``received_at``, never the
            station's ``sent_at``. A station with a broken clock could
            otherwise assert coverage of any window it chose, and this method
            decides what counts as a miss.
        """
        ...
