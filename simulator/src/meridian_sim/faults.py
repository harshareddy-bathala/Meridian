"""What goes wrong, and when — drawn from the seed like everything else.

Stage 10 injects five faults: the network drops, the process restarts, the token
stops being accepted, observation uploads stall while heartbeats keep working,
and the receiver dies. Stage 21 adds six more — heartbeats that do not arrive, a
partition that cuts off part of the fleet at once, responses lost after the
platform committed, a drifting clock, a decoder that hears and cannot decode,
and a station that lets go of work it held (D-188). Each is scheduled from a
seed, so a run with faults is exactly as reproducible as a clean one — which is
the whole point of injecting them here rather than by unplugging something.

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

What was injected is written to the run's fault ledger
(:mod:`~meridian_sim.ledger`) and nowhere else: never on MSP, never in the
platform's database (D-189).

Reference: docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md Stages 10 and 21;
docs/DECISIONS.md D-024, D-074, D-075, D-188, D-189.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field

import httpx

__all__ = [
    "CLOCK_DRIFT",
    "DECLINES",
    "DECODER_DEGRADED",
    "HEARTBEAT_DELAYED",
    "NETWORK_DOWN",
    "PARTITION",
    "RECEIVER_DOWN",
    "RESTART",
    "SCENARIOS",
    "SLOW_API",
    "TOKEN_REVOKED",
    "UPLOAD_BLOCKED",
    "FaultInjectingTransport",
    "FaultSchedule",
    "FaultState",
    "FaultWindow",
    "FleetPartition",
    "partition_for",
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

HEARTBEAT_DELAYED = "heartbeat_delayed"
"""Only ``POST /heartbeat`` fails; everything else gets through.

What the platform sees of a station whose heartbeats are late: a gap in them and
nothing else. A short gap must read ``stale`` and never ``offline``, and cost the
station nothing it holds; a long one must read ``offline`` within SC-5's ninety
seconds. Which one a window is follows from its length, and the verifier decides
that from the ledger rather than this module predicting it (D-190).
"""

PARTITION = "partition"
"""Part of the fleet loses the platform at once, and the rest does not.

Injected like :data:`NETWORK_DOWN` beneath each member's client, but scheduled
for the fleet rather than per station (:func:`partition_for`): the point is a
correlated loss, where the scheduler has to move work onto the survivors, and
independent outages that happened to coincide would test that only by accident.
"""

SLOW_API = "slow_api"
"""The platform answers too late: the request lands, the response never does.

Forwarded to the platform, which commits it, and then failed with a read
timeout — so the station believes a request was lost that was in fact stored.
That is the case a slow platform produces that an unreachable one does not, and
the one idempotency exists for: the station resends, and the platform must not
store the observation twice (D-015, D-027, D-191).
"""

CLOCK_DRIFT = "clock_drift"
"""The station's clock runs away from true time, and is corrected when it ends.

The supervisor hands the station's loop a ``now`` that is wrong by an amount
growing each tick (:meth:`FaultSchedule.clock_error_s`). The station then
stamps its heartbeats and places its captures by that clock, as a real one with
a failed time source would. The window closing is a resync, not a slow return.
"""

DECODER_DEGRADED = "decoder_degraded"
"""The receiver hears the satellite and the decoder cannot make frames of it.

``decoded`` becomes ``signal_no_decode``; nothing else changes. It has no
diagnosis category of its own, which is why Stage 27 uses it as a negative
control: the right answer to it is *undetermined*.
"""

DECLINES = "declines"
"""The station lets go of some work it held, before its window opens.

A decline under MSP §4.2 — held, then absent, window still ahead — so the
platform revokes it and may give the time to another pass (D-003, D-171). Which
assignments go is drawn from the station's seed and the assignment's id
(:func:`declines_assignment`), so a restart declines the same ones.
"""

SCENARIOS: dict[str, tuple[str, ...]] = {
    "clean": (),
    "network": (NETWORK_DOWN,),
    "upload": (UPLOAD_BLOCKED,),
    "restart": (RESTART,),
    "receiver": (RECEIVER_DOWN,),
    "revoked": (TOKEN_REVOKED,),
    "faulty": (NETWORK_DOWN, UPLOAD_BLOCKED, RESTART, RECEIVER_DOWN),
    "heartbeat": (HEARTBEAT_DELAYED,),
    "partition": (PARTITION,),
    "slow": (SLOW_API,),
    "drift": (CLOCK_DRIFT,),
    "decoder": (DECODER_DEGRADED,),
    "declines": (DECLINES,),
    "chaos": (
        NETWORK_DOWN,
        UPLOAD_BLOCKED,
        RESTART,
        RECEIVER_DOWN,
        HEARTBEAT_DELAYED,
        PARTITION,
        SLOW_API,
        CLOCK_DRIFT,
        DECODER_DEGRADED,
        DECLINES,
    ),
}
"""Which faults each named scenario may inject.

``faulty`` and ``chaos`` deliberately omit ``token_revoked``. A revoked token
stops the loop permanently and by design (D-024), so including it in a general
scenario would mean every station in a long run eventually stopping — a fleet
that dies of old age tests nothing after the first hour. It gets its own
scenario, where a station stopping is the observation being made.

``faulty`` is Stage 10's set and keeps its exact schedules; ``chaos`` is every
recurring fault, and is what Stage 21's long run injects.
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

_STAGE_21_CYCLES = {
    HEARTBEAT_DELAYED: ((30, 90), (1, 5)),
    SLOW_API: ((30, 90), (1, 3)),
    CLOCK_DRIFT: ((60, 180), (5, 20)),
    DECODER_DEGRADED: ((50, 150), (4, 20)),
    DECLINES: ((40, 120), (2, 6)),
}
"""Stage 21's recurring faults, in the same units as :data:`_CYCLES`.

Kept apart because each is drawn from a stream of its own
(:func:`schedule_for`). Stage 10's four share one stream in a fixed order, so
adding a kind to that stream would move every schedule already drawn from it —
and a seed that stopped meaning the run it used to mean would make every earlier
result unrepeatable.

A delayed heartbeat lasts one to five ticks so that a long run sees both sides
of the thresholds: one missed heartbeat is ``stale`` at most, and three are
``offline``.
"""

_PARTITION_CYCLE = ((60, 180), (3, 8))
"""When the fleet partitions, and for how long, in ticks."""

_PARTITION_SHARE = (0.2, 0.5)
"""The range the partitioned share of the fleet is drawn from.

Never the whole fleet, which is an outage of the platform rather than a
partition, and never so small a share that the survivors have nothing to take.
"""

DRIFT_RATE_S_PER_TICK = (0.5, 3.0)
"""How fast a drifting clock runs away, in seconds per tick.

At a thirty-second cadence, one to ten percent fast — a failed time source, not
a crystal's few parts per million. Big enough that a drift window's error
reaches tens of seconds, which is what a timing fault has to be for anything
downstream to see it (D-188).
"""

DECLINE_PERCENT = 30
"""What share of held, not yet begun work a declining station lets go of."""

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
        return self.ticks_into(tick) is not None

    def ticks_into(self, tick: int) -> int | None:
        """How many ticks this fault has been in force on ``tick``, from zero.

        ``None`` when it is not in force at all.
        """
        if tick < self.offset:
            return None
        into = (tick - self.offset) % self.period
        return into if into < self.duration else None

    def windows(self, until_tick: int) -> tuple[FaultWindow, ...]:
        """Every window this fault opens before ``until_tick``."""
        return tuple(
            FaultWindow(self.kind, first, first + self.duration - 1)
            for first in range(self.offset, until_tick, self.period)
        )


@dataclass(frozen=True, slots=True)
class FaultWindow:
    """One stretch of ticks over which one fault is in force, both ends included.

    ``last_tick`` is ``None`` for a fault that never ends, which today is only a
    revoked token.
    """

    kind: str
    first_tick: int
    last_tick: int | None


@dataclass(frozen=True, slots=True)
class FaultSchedule:
    """Everything that will go wrong for one station, as a function of the tick.

    Holds no state and advances nothing: ask it about tick 900 before tick 1 and
    it answers the same. That is what lets a run's whole fault history be printed
    up front, and what makes two runs at one seed break in the same places.
    """

    cycles: tuple[_Cycle, ...] = ()
    revoked_from: int | None = None
    drift_s_per_tick: float = 0.0
    """How fast this station's clock runs away while :data:`CLOCK_DRIFT` holds."""

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

    def clock_error_s(self, tick: int) -> float:
        """How far ahead of true time this station's clock is on ``tick``.

        Zero outside a :data:`CLOCK_DRIFT` window. Inside one, the error grows by
        :attr:`drift_s_per_tick` each tick from the first, and returns to zero
        when the window closes — a resync, which is how a station that finds its
        time source again corrects itself.
        """
        for one in self.cycles:
            if one.kind == CLOCK_DRIFT:
                into = one.ticks_into(tick)
                if into is not None:
                    return (into + 1) * self.drift_s_per_tick
        return 0.0

    def windows(self, until_tick: int) -> tuple[FaultWindow, ...]:
        """Every fault window that opens before ``until_tick``, in tick order.

        What makes a run's fault history printable before it runs: the ledger a
        run writes must agree with this, window for window.
        """
        found = [window for one in self.cycles for window in one.windows(until_tick)]
        if self.revoked_from is not None and self.revoked_from < until_tick:
            found.append(FaultWindow(TOKEN_REVOKED, self.revoked_from, None))
        return tuple(sorted(found, key=lambda one: (one.first_tick, one.kind)))


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

    cycles = tuple(
        _draw_cycle(stream, kind, _CYCLES[kind]) for kind in kinds if kind in _CYCLES
    )
    revoked_from = (
        stream.randint(*REVOCATION_TICK_RANGE) if TOKEN_REVOKED in kinds else None
    )

    # Stage 21's kinds each on a stream of their own, after the shared one has
    # been drawn from exactly as Stage 10 drew it — so `faulty` and every other
    # Stage 10 scenario keep the schedules their seeds always gave.
    later = tuple(
        _draw_cycle(
            random.Random(f"{station_seed}:{scenario}:{kind}"),
            kind,
            _STAGE_21_CYCLES[kind],
        )
        for kind in kinds
        if kind in _STAGE_21_CYCLES
    )
    drift = (
        random.Random(f"{station_seed}:{scenario}:{CLOCK_DRIFT}:rate").uniform(
            *DRIFT_RATE_S_PER_TICK
        )
        if CLOCK_DRIFT in kinds
        else 0.0
    )
    return FaultSchedule(
        cycles=cycles + later,
        revoked_from=revoked_from,
        drift_s_per_tick=round(drift, 3),
    )


@dataclass(frozen=True, slots=True)
class FleetPartition:
    """Which stations a partition cuts off, and when.

    The one fault scheduled for the fleet rather than for a station. Its members
    are station indices, counting from one.
    """

    members: frozenset[int] = frozenset()
    cycle: _Cycle | None = None

    def active_for(self, index: int, tick: int) -> bool:
        """Whether station ``index`` is cut off on ``tick``."""
        return (
            self.cycle is not None
            and index in self.members
            and self.cycle.active_at(tick)
        )

    def windows(self, until_tick: int) -> tuple[FaultWindow, ...]:
        """Every partition window that opens before ``until_tick``."""
        return self.cycle.windows(until_tick) if self.cycle is not None else ()


def partition_for(
    master_seed: int, scenario: str, station_count: int
) -> FleetPartition:
    """Draw the partition a fleet will suffer, if its scenario has one.

    Args:
        master_seed: The run's seed.
        scenario: A key of :data:`SCENARIOS`.
        station_count: How many stations the fleet has.

    Returns:
        The partition, with no members and no cycle when the scenario does not
        name :data:`PARTITION`, or when a fleet of one has no survivors to cut
        off from.

    Raises:
        KeyError: No such scenario, for the reason :func:`schedule_for` gives.

    Note:
        Unlike everything per station, the members depend on the station count:
        a partition is a share of a fleet. Station 1's own schedule still does
        not, which is the property :mod:`~meridian_sim.config` promises.
    """
    if PARTITION not in SCENARIOS[scenario] or station_count < 2:  # noqa: PLR2004
        return FleetPartition()
    stream = random.Random(f"{master_seed}:{scenario}:{PARTITION}")
    cycle = _draw_cycle(stream, PARTITION, _PARTITION_CYCLE)
    share = stream.uniform(*_PARTITION_SHARE)
    size = min(station_count - 1, max(1, round(station_count * share)))
    members = frozenset(stream.sample(range(1, station_count + 1), size))
    return FleetPartition(members=members, cycle=cycle)


def declines_assignment(station_seed: int, assignment_id: str) -> bool:
    """Whether a declining station lets go of this assignment.

    Drawn from the station and the assignment rather than from a counter, for
    the reason :func:`~meridian_sim.config.seed_for_pass` is: a restarted
    station must decline the same work it declined before.
    """
    digest = hashlib.sha256(f"{station_seed}:{assignment_id}:decline".encode())
    return int.from_bytes(digest.digest()[:2], "big") % 100 < DECLINE_PERCENT


def _draw_cycle(
    stream: random.Random,
    kind: str,
    ranges: tuple[tuple[int, int], tuple[int, int]],
) -> _Cycle:
    """One recurring fault's timing, from the ranges its kind declares."""
    period_range, duration_range = ranges
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
        path = request.url.path

        if NETWORK_DOWN in active:
            raise httpx.ConnectError("simulated network outage", request=request)
        if PARTITION in active:
            raise httpx.ConnectError("simulated network partition", request=request)
        if UPLOAD_BLOCKED in active and path.endswith("/observations"):
            raise httpx.ConnectError("simulated upload stall", request=request)
        if HEARTBEAT_DELAYED in active and path.endswith("/heartbeat"):
            raise httpx.ConnectError("simulated heartbeat delay", request=request)
        if TOKEN_REVOKED in active:
            request.headers["Authorization"] = "Bearer simulated-revoked-token"

        response = self._inner.handle_request(request)
        if SLOW_API in active:
            # After the platform has committed, not before: the request landed
            # and only the answer is lost, which is what a slow platform does.
            response.close()
            raise httpx.ReadTimeout("simulated slow response", request=request)
        return response

    def close(self) -> None:
        """Close the transport underneath."""
        self._inner.close()
