"""Whether a station is still there, derived from how old its last heartbeat is.

Owns the ``Liveness`` vocabulary, the two thresholds that separate its values,
and the function that applies them. ``meridian.registry`` re-exports ``Liveness``
so callers keep importing it from the package they already use.

This module is the **leaf** of the registry package: it imports nothing from
``meridian`` at all. That is deliberate — ``meridian.config`` checks a setting
against ``MAX_HEARTBEAT_INTERVAL_S`` below, and a leaf is what lets it do so
without an import cycle through the store layer.

It performs no I/O, holds no connection and reads no clock. The instant to
compare against is passed in, so a test can state one rather than sleep for it.

Thresholds are **absolute seconds fixed by SC-5**, not multiples of the
configured heartbeat interval. D-013 is explicit that the success criterion sets
the threshold rather than the other way round. See :data:`OFFLINE_AFTER_S`.

Reference: docs/DECISIONS.md D-013, D-030; docs/PROJECT.md §9 SC-5;
docs/DATA-MODEL.md.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

__all__ = [
    "MAX_HEARTBEAT_INTERVAL_S",
    "OFFLINE_AFTER_S",
    "STALE_AFTER_S",
    "Liveness",
    "derive_liveness",
    "is_heartbeat_interval_consistent",
]

Liveness = Literal["never_seen", "online", "stale", "offline"]
"""The platform's derived conclusion about a station.

Distinct from the ``state`` a station reports in its heartbeat and from the
``health`` object it sends alongside — see docs/DECISIONS.md D-013 on why all
three are not called the same thing.
"""

STALE_AFTER_S = 60
"""A station is ``stale`` once its last heartbeat is this old.

Two missed heartbeats at D-030's 30-second interval. The pairing is a
consequence of that interval, not the definition — see
:data:`MAX_HEARTBEAT_INTERVAL_S`.
"""

OFFLINE_AFTER_S = 90
"""A station is ``offline`` once its last heartbeat is this old.

**Ninety is not arbitrary and is not derived from the heartbeat interval.**
SC-5 (docs/PROJECT.md §9) requires an injected node failure to be detected
within 90 s, and D-013 records the direction of the dependency explicitly: the
success criterion sets the threshold rather than the other way round. Phase 3's
SLI in ``meridian.reliability`` aligns with this number for free because of it.

Deriving it from ``Settings.heartbeat_interval_s`` instead would mean a
deployment could raise the interval and quietly stop meeting SC-5 while every
dashboard still read "offline" as though it meant the same thing.
"""

MAX_HEARTBEAT_INTERVAL_S = STALE_AFTER_S // 2
"""The largest heartbeat interval these fixed thresholds still describe.

At D-030's 30-second interval, ``stale`` is two missed heartbeats and
``offline`` is three. Raise the interval past this and the arithmetic inverts:
at 45 s a perfectly healthy station is 60 s past its last heartbeat for a third
of every cycle, so it would flap into ``stale`` while behaving exactly as
specified — and every reliability figure reading liveness would inherit that.

Checked at startup by ``meridian.config`` rather than left as a comment, because
the failure it prevents is a wrong number rather than a crash, and a wrong number
is the kind this project is least able to notice.
"""


def is_heartbeat_interval_consistent(heartbeat_interval_s: int) -> bool:
    """Whether an interval leaves the fixed thresholds meaning what they say.

    Args:
        heartbeat_interval_s: The interval the platform tells stations to use,
            from ``Settings.heartbeat_interval_s`` and returned in MSP §4.1's
            register response.

    Returns:
        True when a station heartbeating on time stays ``online`` between
        heartbeats — that is, when two intervals still fit inside
        :data:`STALE_AFTER_S`.
    """
    return 0 < heartbeat_interval_s <= MAX_HEARTBEAT_INTERVAL_S


def derive_liveness(last_heartbeat_at: datetime | None, *, now: datetime) -> Liveness:
    """Classify a station by the age of its most recent heartbeat.

    Derived on read rather than stored. A stored conclusion is only correct
    until the clock moves past its next threshold, and nothing moves the clock
    on the platform's behalf — so a station that stopped reporting would keep
    showing ``online`` until some other write happened to refresh it, which is
    precisely the case liveness exists to detect (D-054).

    Args:
        last_heartbeat_at: When the station last heartbeat, timezone-aware UTC,
            or ``None`` if it never has. ``None`` is not the same as "very old":
            a station that has registered and never reported is ``never_seen``,
            which is a commissioning problem, while one that reported and
            stopped is ``offline``, which is a fault.
        now: The instant to measure against, timezone-aware UTC. Passed rather
            than read here so this function stays pure and a test can state the
            age it wants instead of sleeping for it.

    Returns:
        One of :data:`Liveness`' four values.

    Raises:
        ValueError: If either instant is naive. A naive datetime would be
            subtracted as though it were UTC and would silently misreport a
            station in another timezone by whole hours — CLAUDE.local.md §6
            makes this a bug rather than a tolerance.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware UTC")
    if last_heartbeat_at is None:
        return "never_seen"
    if last_heartbeat_at.tzinfo is None:
        raise ValueError("last_heartbeat_at must be timezone-aware UTC")

    age_s = (now - last_heartbeat_at).total_seconds()
    if age_s >= OFFLINE_AFTER_S:
        return "offline"
    if age_s >= STALE_AFTER_S:
        return "stale"
    # Includes a negative age. A heartbeat stamped slightly in the future is a
    # station whose clock runs fast, not a station that is not there — MSP §8
    # exists because station clocks are not trusted, and treating skew as a
    # fault would report the one condition the clock endpoint is there to fix.
    return "online"
