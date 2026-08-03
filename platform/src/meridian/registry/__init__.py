"""Station registration, capabilities, tokens, health state, last-heartbeat age.

**The authority on whether a station was listening at a given moment.** Every
reliability metric depends on this being right, which is why
:meth:`Registry.was_listening` is implemented and tested in Phase 1 even though
``meridian.reliability`` does not exist until Phase 3. Left until then, it would be
reconstructed from whatever the heartbeat table happened to contain, and the
distinction between "heard nothing" and "was not listening" would erode exactly as
docs/ARCHITECTURE.md warns.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

__all__ = ["Liveness", "Registry"]

Liveness = Literal["never_seen", "online", "stale", "offline"]
"""The platform's derived conclusion about a station.

Distinct from the ``state`` a station reports in its heartbeat and from the
``health`` object it sends alongside — see docs/DECISIONS.md D-013 on why all
three are not called the same thing.

Thresholds come from SC-5, which requires an injected node failure to be detected
within 90 s: ``stale`` at 60 s (two missed heartbeats), ``offline`` at 90 s
(three). The success criterion sets the threshold rather than the other way round.
"""


class Registry(Protocol):
    """Station identity and liveness."""

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
