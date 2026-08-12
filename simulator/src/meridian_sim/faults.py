"""What goes wrong, and when — drawn from the seed like everything else.

Stage 10 injects four faults: the network drops, the process restarts, the token
stops being accepted, and observation uploads stall while heartbeats keep
working. Each is scheduled from the station's own seed, so a run with faults is
exactly as reproducible as a clean one — which is the whole point of injecting
them here rather than by unplugging something.

**Three of the four are injected beneath the client, at the HTTP layer.** A
:class:`FaultInjectingTransport` wraps whatever the station would really have
talked to, so the real :class:`~meridian_client.transport.MspTransport` sees a
real connection failure and runs its real retry policy. Faking the failure any
higher — a stubbed ``heartbeat()``, a patched method — would test the loop
against a rehearsal of an outage instead of one.

The fourth, a restart, cannot be injected from below: it is the supervisor
discarding a station and rebuilding it from what is on disk. This module says
*when*; :mod:`~meridian_sim.supervisor` does it.

Pure apart from the transport: a schedule is arithmetic over a tick number, so
what a run will do can be printed before it does any of it.

Reference: docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md Stage 10; docs/DECISIONS.md
D-024, D-074, D-075.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import httpx

__all__ = [
    "NETWORK_DOWN",
    "RECEIVER_DOWN",
    "RESTART",
    "SCENARIOS",
    "TOKEN_REVOKED",
    "UPLOAD_BLOCKED",
    "FaultInjectingTransport",
    "FaultSchedule",
    "FaultState",
    "schedule_for",
]

NETWORK_DOWN = "network_down"
"""Every request fails as if the platform were unreachable."""

UPLOAD_BLOCKED = "upload_blocked"
"""Only ``POST /observations`` fails; heartbeats still get through.

The nastiest of the four and the reason it is here. A station in this state
looks perfectly healthy — it reports, it holds work, it says it is listening —
while its finished observations pile up on disk. It is the shape of the failure
D-074 exists to bound, and it is the one a dashboard would not show.
"""

TOKEN_REVOKED = "token_revoked"
"""The station's credential stops being accepted, and stays that way.

Injected by presenting a token the platform will reject, so the ``401`` the loop
receives is a real one from the real endpoint. It exercises the *station's*
response to revocation (D-024: stop, do not retry), not the platform's
revocation path — an operator running ``meridian station revoke`` is what does
that, and the simulator must not reach into the database to do it itself.
"""

RESTART = "restart"
"""The process dies and comes back, keeping only what reached disk."""

RECEIVER_DOWN = "receiver_down"
"""The client is fine and the radio is not: work is held and never begun.

The only fault of the five that is injected above the transport rather than
below it, because nothing about the network is wrong. It is what produces MSP
§4.4's ``not_attempted`` — a station that took the work and failed to start,
which the specification is emphatic is an operational failure and not the same
thing as listening and hearing nothing.
"""

SCENARIOS: dict[str, tuple[str, ...]] = {
    "clean": (),
    "network": (NETWORK_DOWN,),
    "upload": (UPLOAD_BLOCKED,),
    "restart": (RESTART,),
    "receiver": (RECEIVER_DOWN,),
    "revoked": (TOKEN_REVOKED,),
    "faulty": (NETWORK_DOWN, UPLOAD_BLOCKED, RESTART, RECEIVER_DOWN),
}
"""Which faults each named scenario may inject.

``faulty`` deliberately omits ``token_revoked``. A revoked token stops the loop
permanently and by design (D-024), so including it in the general scenario would
mean every station in a long run eventually stopping — a fleet that dies of old
age tests nothing after the first hour. It gets its own scenario, where a
station stopping is the observation being made.
"""

_CYCLES = {
    NETWORK_DOWN: ((20, 60), (2, 6)),
    UPLOAD_BLOCKED: ((30, 80), (3, 10)),
    RESTART: ((40, 120), (1, 1)),
    RECEIVER_DOWN: ((50, 150), (4, 20)),
}
"""Per fault: the range its period is drawn from, and the range its duration is.

Both in ticks, so at a thirty-second cadence a network outage arrives every ten
to thirty minutes and lasts one to three. Frequent enough that a short run sees
several, rare enough that a station spends most of its life working — a fleet
that is broken more often than not measures the fault injector rather than the
platform.

Restarts have a duration of one because they are an instant, not a window.
``token_revoked`` is absent because it does not recur: it happens once and
stays.
"""

REVOCATION_TICK_RANGE = (5, 40)
"""When a revoked token stops working, in ticks from the run's start.

Late enough that the station has registered, held work and reported at least
once, so what is under test is a working station losing its credential rather
than one that never had it.
"""


@dataclass(frozen=True, slots=True)
class _Cycle:
    """One recurring fault: when it first fires, how often, and for how long."""

    kind: str
    offset: int
    period: int
    duration: int

    def active_at(self, tick: int) -> bool:
        """Whether this fault is in force on ``tick``."""
        if tick < self.offset:
            return False
        return (tick - self.offset) % self.period < self.duration


@dataclass(frozen=True, slots=True)
class FaultSchedule:
    """Everything that will go wrong for one station, as a function of the tick.

    Holds no state and advances nothing: ask it about tick 900 before tick 1 and
    it answers the same. That is what lets a run's whole fault history be printed
    up front, and what makes two runs at one seed break in the same places.
    """

    cycles: tuple[_Cycle, ...] = ()
    revoked_from: int | None = None

    def active_at(self, tick: int) -> frozenset[str]:
        """Which faults are in force on ``tick``.

        Args:
            tick: Which tick of the run, counting from zero.

        Returns:
            The fault kinds in force, empty on a tick where nothing is wrong.
            ``restart`` appears on the tick it fires, which the supervisor reads
            as an instruction rather than a condition.
        """
        active = {one.kind for one in self.cycles if one.active_at(tick)}
        if self.revoked_from is not None and tick >= self.revoked_from:
            active.add(TOKEN_REVOKED)
        return frozenset(active)

    def restarts_at(self, tick: int) -> bool:
        """Whether this station's process dies and comes back on ``tick``."""
        return RESTART in self.active_at(tick)


def schedule_for(station_seed: int, scenario: str) -> FaultSchedule:
    """Draw the fault schedule one station will follow.

    Args:
        station_seed: The station's seed, from
            :func:`~meridian_sim.config.seed_for_station`.
        scenario: A key of :data:`SCENARIOS`.

    Returns:
        The schedule, empty for the ``clean`` scenario.

    Raises:
        KeyError: No such scenario. Raised rather than defaulted to ``clean``,
            because a misspelled scenario that quietly injected nothing would
            look exactly like a run where nothing happened to go wrong.

    Note:
        Drawn from a stream of its own, seeded on the station's seed and the
        scenario name. Sharing the outcome model's stream would make what a
        station heard depend on what broke, so adding a fault would silently
        change every pass that was not affected by it.
    """
    kinds = SCENARIOS[scenario]
    stream = random.Random(f"{station_seed}:{scenario}")

    cycles = tuple(_draw_cycle(stream, kind) for kind in kinds if kind in _CYCLES)
    revoked_from = (
        stream.randint(*REVOCATION_TICK_RANGE) if TOKEN_REVOKED in kinds else None
    )
    return FaultSchedule(cycles=cycles, revoked_from=revoked_from)


def _draw_cycle(stream: random.Random, kind: str) -> _Cycle:
    """One recurring fault's timing, from the ranges its kind declares."""
    period_range, duration_range = _CYCLES[kind]
    period = stream.randint(*period_range)
    return _Cycle(
        kind=kind,
        offset=stream.randint(0, period - 1),
        period=period,
        duration=stream.randint(*duration_range),
    )


@dataclass
class FaultState:
    """What is broken right now, shared between the supervisor and the transport.

    Mutable, and the only mutable thing in this module. The transport beneath a
    station is built once and lives for the station's whole life, while what is
    wrong changes every tick — so the supervisor writes here before each tick and
    the transport reads it during. A callable would hide that handover inside a
    closure; a field named after what it holds does not.
    """

    active: frozenset[str] = field(default_factory=frozenset)


class FaultInjectingTransport(httpx.BaseTransport):
    """An HTTP transport that breaks on purpose.

    Args:
        inner: Where a request goes when nothing is wrong. A real connection in
            a deployment, and the platform in-process in a test.
        state: What is currently broken, written by the supervisor each tick.

    Note:
        Beneath the client rather than around it. Everything above — the retry
        policy, the backoff, the distinction between a refusal and an
        unreachable platform — is the reference client's own code running for
        real, which is the only arrangement in which a passing fault test says
        anything about a real station.
    """

    def __init__(self, inner: httpx.BaseTransport, state: FaultState) -> None:
        """Wrap ``inner``, consulting ``state`` on every request."""
        self._inner = inner
        self._state = state

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        """Send one request, unless something is currently wrong with it."""
        active = self._state.active

        if NETWORK_DOWN in active:
            raise httpx.ConnectError("simulated network outage", request=request)
        if UPLOAD_BLOCKED in active and request.url.path.endswith("/observations"):
            raise httpx.ConnectError("simulated upload stall", request=request)
        if TOKEN_REVOKED in active:
            request.headers["Authorization"] = "Bearer simulated-revoked-token"

        return self._inner.handle_request(request)

    def close(self) -> None:
        """Close the transport underneath."""
        self._inner.close()
