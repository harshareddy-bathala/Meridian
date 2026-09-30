"""When each fault happens: a schedule drawn from a seed, and a fleet's partition.

The vocabulary — what each fault is and which scenario injects it — is
:mod:`~meridian_sim.faults`'. This is the arithmetic: per station, a schedule
that answers "what is broken on tick N" without advancing anything; per fleet,
which stations a partition cuts off and when; and which held work a declining
station lets go of. Pure, so a run's whole fault history can be printed before
it runs, and two runs at one seed break in the same places.

Reference: docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md Stages 10 and 21;
docs/DECISIONS.md D-074, D-188.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass

from meridian_sim.faults import (
    CLOCK_DRIFT,
    DECLINES,
    DECODER_DEGRADED,
    HEARTBEAT_DELAYED,
    NETWORK_DOWN,
    PARTITION,
    RECEIVER_DOWN,
    RESTART,
    SCENARIOS,
    SLOW_API,
    TOKEN_REVOKED,
    UPLOAD_BLOCKED,
)
from meridian_sim.sky_faults import SkyFault, sky_faults_for

__all__ = [
    "DECLINE_PERCENT",
    "DRIFT_RATE_S_PER_TICK",
    "REVOCATION_TICK_RANGE",
    "FaultSchedule",
    "FaultWindow",
    "FleetPartition",
    "declines_assignment",
    "partition_for",
    "schedule_for",
]

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

    ``last_tick`` is ``None`` for a fault that never ends: a revoked token, and
    Stage 25's degradation, obstruction and interference.
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
    sky: tuple[SkyFault, ...] = ()
    """Stage 25's faults on this station's own sky and chain (D-253). A silent
    satellite is the fleet's, and the supervisor adds it."""

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
        active |= {one.kind for one in self.sky if one.active_at(tick)}
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
        found.extend(
            FaultWindow(one.kind, one.first_tick, one.last_tick)
            for one in self.sky
            if one.first_tick < until_tick
        )
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
        sky=sky_faults_for(station_seed, scenario),
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
