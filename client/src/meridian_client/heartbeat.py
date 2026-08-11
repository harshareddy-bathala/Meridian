"""Building MSP §4.2's heartbeat, and reading what comes back.

A heartbeat is a complete statement of the station's present: what it is doing,
what work it holds, what it is listening to, and how well it knows its own clock.
It announces no transitions — the platform derives those by comparing successive
statements — which is what makes a lost heartbeat harmless.

Pure. The instants and the held set arrive as arguments, so the body a station
would send is checkable without a clock, a file or a network.

Reference: docs/MSP-SPEC.md §4.2, §4.3; docs/DECISIONS.md D-003, D-016, D-025,
D-028.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from meridian_client.assignment_message import (
    Assignment,
    MalformedAssignmentError,
    parse_assignment,
)
from meridian_client.clock import ClockEstimate, parse_wire_time, require_utc

__all__ = [
    "STATES",
    "HeartbeatResponse",
    "Listening",
    "StationState",
    "build_heartbeat_body",
    "parse_heartbeat_response",
]

STATES = (
    "idle",
    "slewing",
    "listening",
    "processing",
    "degraded",
    "maintenance",
)
"""MSP §4.2's six values for ``state``, in the specification's order.

Written out rather than inferred from what the station is doing, so a value the
platform would reject cannot leave this client at all.
"""


@dataclass(frozen=True, slots=True)
class Listening:
    """MSP §4.2's ``listening`` block — **the most important field in MSP**.

    Without it, no observation is ambiguous: the platform cannot tell a station
    that heard nothing from one that was not switched on. With it, the platform
    can assert that a station was tuned to a named frequency for a named target
    at a named time and heard nothing, which is a measurement rather than a gap.

    All four fields together or none at all. A partial block cannot support the
    assertion the block exists to make, so this dataclass has no optional
    members and the station omits the whole thing when it is not receiving.
    """

    assignment_id: str
    satellite_id: str
    centre_freq_hz: int
    mode: str
    """The demodulator actually running. A station tuned to the right frequency
    running the wrong one did not observe the pass, and the platform must be able
    to tell that apart from a miss (D-028)."""


@dataclass(frozen=True, slots=True)
class StationState:
    """Everything about this heartbeat that is not the held set.

    A parameter object rather than six arguments to
    :func:`build_heartbeat_body`, per CLAUDE.local.md §2 — and it groups the
    things a station knows about itself, which is a real grouping rather than a
    way of getting under a limit.
    """

    station_id: str
    sent_at: datetime
    state: str

    listening: Listening | None = None
    clock: ClockEstimate | None = None
    """``None`` until the station has measured its offset, and **not zero**.

    A station claiming a perfect clock and one that cannot measure its own are
    opposite cases; conflating them corrupts every timing figure derived from
    them (D-016, D-025).
    """

    health: Mapping[str, object] | None = None
    """Opaque to the platform, stored verbatim, capped at 4 KiB serialised."""


def _listening_block(listening: Listening) -> dict[str, object]:
    """The four fields of §4.2's listening block."""
    return {
        "assignment_id": listening.assignment_id,
        "satellite_id": listening.satellite_id,
        "centre_freq_hz": listening.centre_freq_hz,
        "mode": listening.mode,
    }


def build_heartbeat_body(
    station: StationState, held_assignment_ids: Sequence[str]
) -> dict[str, object]:
    """MSP §4.2's request body, as a JSON-serialisable object.

    Args:
        station: What this station is and is doing.
        held_assignment_ids: Every assignment it currently holds. **Read from
            the on-disk record, never from the last response** — the point of
            the field is to state what survived, and a list built from what was
            just delivered would claim work a crash could have lost.

    Returns:
        The body. Separate from sending it so the exact wire shape can be
        asserted against the specification with no platform involved.

    Raises:
        ValueError: ``state`` is not one of §4.2's six, or ``sent_at`` is naive
            or not UTC.

    Note:
        ``held_assignments`` is always present, including when it is empty —
        §4.2 requires ``[]`` rather than omission, because "I hold nothing" is a
        statement and there is no other way to decline (D-003). A missing field
        would be indistinguishable from declining everything, which is precisely
        the case it has to be distinguishable from.

        ``clock_offset_s`` and ``clock_uncertainty_s`` are omitted entirely when
        unmeasured, rather than sent as ``null`` or as ``0.0``.
    """
    if station.state not in STATES:
        raise ValueError(f"state must be one of {STATES}, not {station.state!r}")
    require_utc(station.sent_at, "sent_at")

    body: dict[str, object] = {
        "station_id": station.station_id,
        "sent_at": station.sent_at.isoformat().replace("+00:00", "Z"),
        "state": station.state,
        "held_assignments": list(held_assignment_ids),
        "health": dict(station.health) if station.health else {},
    }
    if station.listening is not None:
        body["listening"] = _listening_block(station.listening)
    if station.clock is not None:
        body["clock_offset_s"] = station.clock.offset_s
        body["clock_uncertainty_s"] = station.clock.uncertainty_s
    return body


@dataclass(frozen=True, slots=True)
class HeartbeatResponse:
    """MSP §4.2's response: the work due, and the platform's clock."""

    assignments: tuple[Assignment, ...]
    server_time: datetime
    """Why a station with a working heartbeat never needs ``/msp/v0/time``: the
    offset can be re-estimated from the exchange it was already making."""


def parse_heartbeat_response(body: Mapping[str, object]) -> HeartbeatResponse:
    """Read a §4.2 response into assignments a station can act on.

    Args:
        body: The decoded JSON response.

    Returns:
        The assignments and the platform's reported time.

    Raises:
        MalformedAssignmentError: An assignment is missing a field.
        ValueError: ``server_time`` or an assignment's instants are unparseable,
            or ``assignments`` is not an array.
    """
    delivered = body.get("assignments", [])
    if not isinstance(delivered, Sequence) or isinstance(delivered, str):
        # A protocol error, not a TypeError: a TypeError says this program has
        # a bug, and this says the platform sent something MSP §4.2 does not
        # allow. A station does not respond to those two the same way.
        raise MalformedAssignmentError("assignments is not an array")

    return HeartbeatResponse(
        assignments=tuple(parse_assignment(one) for one in delivered),
        server_time=parse_wire_time(str(body["server_time"]), "server_time"),
    )
