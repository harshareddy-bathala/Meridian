"""Station registration, capabilities, tokens, health state, last-heartbeat age.

**The authority on whether a station was listening at a given moment.** Every
reliability metric depends on this being right, which is why
:meth:`Registry.was_listening` is implemented and tested in Phase 1 even though
``meridian.reliability`` does not exist until Phase 3. Left until then, it would be
reconstructed from whatever the heartbeat table happened to contain, and the
distinction between "heard nothing" and "was not listening" would erode exactly as
docs/ARCHITECTURE.md warns.

**`register` is declared here, not yet implemented.** Stage 4.3's store layer
(`meridian.store.stations`, `meridian.store.invites`) is built and tested; the
concrete `Registry` class that composes those calls into MSP §4.1's decision
table, mints tokens and reads `Settings.token_hash_pepper` is the next
increment, written against this signature — interface before implementation,
per CLAUDE.local.md §8.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from meridian.store.stations import Capability

__all__ = [
    "InvalidInviteError",
    "Liveness",
    "Registration",
    "RegistrationRequest",
    "Registry",
]

Liveness = Literal["never_seen", "online", "stale", "offline"]
"""The platform's derived conclusion about a station.

Distinct from the ``state`` a station reports in its heartbeat and from the
``health`` object it sends alongside — see docs/DECISIONS.md D-013 on why all
three are not called the same thing.

Thresholds come from SC-5, which requires an injected node failure to be detected
within 90 s: ``stale`` at 60 s (two missed heartbeats), ``offline`` at 90 s
(three). The success criterion sets the threshold rather than the other way round.
"""


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
    client_impl: str | None
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

        Tokens are opaque secrets compared against a stored hash in constant time
        (docs/DECISIONS.md D-017), not signed tokens — MSP §6 defines
        ``unauthorized`` as covering *revoked* tokens, and revocation needs the
        lookup this method performs anyway.
        """
        ...

    def liveness(self, station_id: str, *, now: datetime) -> Liveness:
        """Derive liveness from the age of the most recent heartbeat."""
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
