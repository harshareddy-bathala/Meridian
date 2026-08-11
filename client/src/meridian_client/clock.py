"""Estimating the station's clock offset against the platform.

A station's timing error is only interpretable against its own clock accuracy:
`docs/EVALUATION.md` §6.1 discards any timing error smaller than the station's
reported clock uncertainty, so a station that cannot say how well it knows its own
clock contributes nothing usable to SC-3. This module produces both numbers from
one ``GET /msp/v0/time`` exchange.

It sits in ``meridian_client``, is pure computation, and performs **no I/O** — the
request belongs to ``meridian_client.transport``, which passes the three instants
in. That split is what lets the sign convention be tested without a server.

**The convention, from docs/DECISIONS.md D-025, written once:**

    clock_offset = platform clock − station clock

A station whose clock runs fast reports a **negative** offset. That sentence is the
test case, and it is worth one because a sign error here is invisible: nothing
crashes, every number keeps a plausible magnitude, and §6.1's timing figure comes
out inverted in a report.

Note that this module deliberately does not import ``meridian.orbit.require_utc``,
which does the same job on the platform side. ``meridian-client`` depends on
``httpx`` and nothing else so that "the station client knows nothing about the
database" is enforced when the package is built (D-012); reaching across that
boundary for a four-line guard would trade the boundary for the duplication it
exists to justify.

Reference: docs/MSP-SPEC.md §4.2, §8; docs/DECISIONS.md D-016, D-025.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime

CLOCK_RESOLUTION_S = time.get_clock_info("time").resolution
"""Smallest interval this machine's wall clock can distinguish.

Read from the Python runtime rather than assumed. It is about 15.6 ms on Windows
and about 1 ns on Linux, and the difference matters: a localhost round trip completes
inside one Windows tick, so ``received_at - sent_at`` measures as **exactly
zero** — and an uncertainty of ``0.0`` is not a small number, it is a claim of
perfect knowledge that MSP §4.2 and D-016 explicitly forbid.
"""

__all__ = [
    "CLOCK_RESOLUTION_S",
    "ClockEstimate",
    "estimate_clock_offset",
    "parse_server_time",
    "parse_wire_time",
    "require_utc",
]


def parse_wire_time(raw: str, field: str) -> datetime:
    """Parse any MSP timestamp, whichever message it arrived in.

    MSP §8 shows one form on the wire, ``2026-08-14T09:31:02Z``, and every
    instant the protocol carries uses it — ``server_time``, an assignment's
    ``start_at``, an element set's ``epoch``.

    Args:
        raw: The string as the platform sent it.
        field: Which field this is, used in the error. A response carrying
            several timestamps should say which one was wrong.

    Returns:
        The instant, as timezone-aware UTC.

    Raises:
        ValueError: If ``raw`` is not ISO-8601, or carries no offset at all. A
            missing offset is refused rather than assumed to be UTC — assuming is
            how a platform in a local time zone silently shifts every station's
            estimated offset by that zone.

    Note:
        ``datetime.fromisoformat`` accepts a literal ``Z`` from Python 3.11, which
        is the floor this project pins to because Raspberry Pi OS bookworm ships
        3.11. No fallback parser is needed, and one would be untested code.
    """
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        raise ValueError(f"{field} carries no UTC offset: {raw!r}")
    return parsed.astimezone(UTC)


def parse_server_time(raw: str) -> datetime:
    """Parse the ``server_time`` field of ``GET /msp/v0/time``."""
    return parse_wire_time(raw, "server_time")


def require_utc(t: datetime, field: str) -> datetime:
    """Return ``t`` unchanged, or raise if it is naive or not UTC.

    Args:
        t: The instant to check.
        field: Name used in the error message, so a failure says which of three
            timestamps was wrong.

    Returns:
        ``t``, unchanged.

    Raises:
        ValueError: If ``t`` is naive or carries a non-zero UTC offset. A naive
            datetime propagates happily and is wrong by whatever the local offset
            happened to be on the machine that ran it — which for a clock-offset
            estimator is an error of exactly the kind being measured.
    """
    if t.tzinfo is None:
        raise ValueError(f"{field} is naive; all timestamps are timezone-aware UTC")
    if t.utcoffset() != UTC.utcoffset(None):
        raise ValueError(f"{field} is not UTC (offset {t.utcoffset()})")
    return t


@dataclass(frozen=True, slots=True)
class ClockEstimate:
    """What one time exchange established about the station's clock."""

    offset_s: float
    """Platform clock minus station clock, in seconds (D-025).

    Negative when the station's clock runs fast. Reported to the platform as
    ``clock_offset_s`` on the next heartbeat.
    """

    uncertainty_s: float
    """1σ magnitude, unsigned, and **never zero**.

    MSP §4.2 and D-016: ``null`` means unknown and must never be conflated with
    ``0.0``. A station claiming perfect clock accuracy and a station that cannot
    measure its own are opposite cases, and only one of them should have its
    timing errors kept by `docs/EVALUATION.md` §6.1.

    Floored at :data:`CLOCK_RESOLUTION_S`, because half a round trip can measure
    as zero on a coarse clock without the true uncertainty being anywhere near it.
    """

    round_trip_s: float
    """The measured round trip, retained because it bounds the uncertainty."""


def estimate_clock_offset(
    sent_at: datetime,
    received_at: datetime,
    server_time: datetime,
) -> ClockEstimate:
    """Estimate the station's clock offset from one ``/msp/v0/time`` exchange.

    MSP §4.2 fixes the estimator so that every implementation produces a
    comparable number:

        offset = server_time − (sent_at + received_at) / 2

    The midpoint of the station's own send and receive instants is its best
    estimate of what its clock read at the moment the platform stamped the
    response, so the difference is the platform's clock minus the station's —
    D-025's convention, arrived at rather than asserted.

    The uncertainty is half the round trip, because the platform's stamp could
    have been taken at either end of it. That is a floor, not a measurement: it
    ignores asymmetric network paths, which bias the midpoint and which a station
    cannot detect from one exchange.

    Args:
        sent_at: Station clock when the request left. Timezone-aware UTC.
        received_at: Station clock when the response arrived. Timezone-aware UTC.
        server_time: The instant the platform reported. Timezone-aware UTC.

    Returns:
        A :class:`ClockEstimate` carrying the offset, its 1σ uncertainty and the
        round trip both were derived from.

    Raises:
        ValueError: If any instant is naive or not UTC, or if ``received_at``
            precedes ``sent_at`` — which means the station's own clock stepped
            mid-request, and an offset computed across a step is meaningless
            rather than merely imprecise.
    """
    require_utc(sent_at, "sent_at")
    require_utc(received_at, "received_at")
    require_utc(server_time, "server_time")

    round_trip_s = (received_at - sent_at).total_seconds()
    if round_trip_s < 0:
        raise ValueError(
            "received_at precedes sent_at; the station clock stepped mid-request"
        )

    midpoint = sent_at + (received_at - sent_at) / 2
    return ClockEstimate(
        offset_s=(server_time - midpoint).total_seconds(),
        # max, not plain division. Half the round trip is the *network* bound on
        # the estimate; the clock's own resolution is the *instrument* bound, and
        # the honest uncertainty is whichever is larger. Without the floor, a
        # localhost round trip on a 15.6 ms Windows tick reports 0.0 — which
        # D-016 says is a claim of perfect accuracy, not a small number, and
        # which would make §6.1 keep timing errors it should have discarded.
        uncertainty_s=max(round_trip_s / 2, CLOCK_RESOLUTION_S),
        round_trip_s=round_trip_s,
    )
