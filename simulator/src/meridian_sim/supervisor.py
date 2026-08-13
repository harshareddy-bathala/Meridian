"""Running a fleet: one thread, one tick each, in turn.

Holds N virtual stations and ticks them round by round, applying each station's
fault schedule before its tick and rebuilding it when the schedule says its
process died. Sleeps once per round rather than once per station, so the whole
fleet keeps one cadence.

**One thread, deliberately** (D-076). ``StationLoop.run()`` blocks between ticks
and D-069 forbids that, so the supervisor never calls it — it calls ``tick``.
Fifty HTTP requests of a few milliseconds each do not need concurrency, and
concurrency would cost the property this stage exists to establish: with threads,
a determinism failure depends on scheduling order and stops being reproducible.
It also means a test can drive a fleet through an exact number of rounds with no
synchronisation, no timeouts and nothing flaky.

A station that stops — today only a revoked token (D-024) — is retired from the
round and the others carry on. The run ends when every station has stopped, or
when the caller's round budget runs out.

Reference: docs/DECISIONS.md D-024, D-069, D-074, D-076, D-080.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from meridian_sim.config import RunConfig, seed_for_station
from meridian_sim.faults import (
    FaultInjectingTransport,
    FaultSchedule,
    FaultState,
    schedule_for,
)
from meridian_sim.virtual_station import VirtualStation, register_or_resume

__all__ = ["RoundOutcome", "Supervisor"]

_log = logging.getLogger(__name__)

DEFAULT_INTERVAL_S = 30.0
"""Cadence used until a station has told us the platform's own.

Only reachable for a fleet of zero, since the interval comes from the
registration response and every station has one by the time a round runs.
"""


@dataclass(frozen=True, slots=True)
class RoundOutcome:
    """What one pass over the fleet did."""

    tick: int
    ticked: tuple[int, ...]
    """Stations that heartbeated, by index."""

    restarted: tuple[int, ...]
    """Stations whose process was torn down and rebuilt from disk.

    A restarting station does not tick that round, which is what a process that
    is not running does.
    """

    stopped: tuple[int, ...]
    """Stations that stopped for good this round, and why is in the log."""

    submitted: tuple[str, ...]
    """Assignments the platform acknowledged across the whole fleet."""


@dataclass
class _Member:
    """One station in the fleet, with what is scheduled to go wrong for it."""

    index: int
    station: VirtualStation
    schedule: FaultSchedule
    faults: FaultState = field(default_factory=FaultState)


class Supervisor:
    """A fleet of virtual stations sharing one thread.

    Args:
        config: The run being executed.
        invite_tokens: One invite per station, in index order, as
            ``meridian invite create --count N`` prints them. Only consumed by a
            station that has never registered, so a restarted fleet needs none
            of them and may be given an empty sequence.
        http_transport: Where the bytes go beneath the fault injector. Defaults
            to a real network connection; a test supplies one reaching the
            platform in-process.
    """

    def __init__(
        self,
        config: RunConfig,
        invite_tokens: Sequence[str] = (),
        http_transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Prepare a fleet. Nothing registers until :meth:`bring_up`."""
        self._config = config
        self._invite_tokens = tuple(invite_tokens)
        self._http_transport = http_transport
        self._members: list[_Member] = []

    def bring_up(self) -> tuple[str, ...]:
        """Register or resume every station, in index order.

        Returns:
            The station ids, in index order.

        Raises:
            meridian_sim.virtual_station.RegistrationNeededError: A station has
                never registered and no invite was offered for it.
            httpx.HTTPError: The platform could not be reached.

        Note:
            In index order and one at a time, so that a fleet which runs out of
            invites part-way through has admitted a known prefix of itself
            rather than an arbitrary subset.
        """
        for index in range(1, self._config.station_count + 1):
            self._members.append(self._member_for(index))
        return tuple(one.station.station_id for one in self._members)

    def tick_round(self, tick: int, now: datetime) -> RoundOutcome:
        """Apply this round's faults and tick every station still running.

        Args:
            tick: Which round, counting from zero. The fault schedules are
                functions of this.
            now: The instant to hand each station's loop.

        Returns:
            What the round did.
        """
        ticked: list[int] = []
        restarted: list[int] = []
        stopped: list[int] = []
        submitted: list[str] = []

        for member in list(self._members):
            member.faults.active = member.schedule.active_at(tick)
            if member.schedule.restarts_at(tick):
                self._restart(member)
                restarted.append(member.index)
                continue
            outcome = member.station.tick(now)
            ticked.append(member.index)
            submitted.extend(outcome.submitted)
            if outcome.stop_reason is not None:
                self._retire(member, outcome.stop_reason)
                stopped.append(member.index)

        return RoundOutcome(
            tick=tick,
            ticked=tuple(ticked),
            restarted=tuple(restarted),
            stopped=tuple(stopped),
            submitted=tuple(submitted),
        )

    def run(self, *, stop_after_rounds: int | None = None) -> int:
        """Tick the fleet on the platform's cadence until it has nothing left to do.

        Args:
            stop_after_rounds: Stop after this many rounds. For tests and for a
                commissioning run; ``None`` runs until every station has stopped.

        Returns:
            How many rounds were run.

        Note:
            Scheduled on :func:`time.monotonic`, not the wall clock, for the
            reason the station loop is: an NTP correction is exactly the event a
            station is expected to have, and a clock that can step backwards
            makes a loop stall while one that steps forwards makes it spin.
        """
        interval_s = self.interval_s()
        due_at = _monotonic()
        tick = 0

        while self._members and (stop_after_rounds is None or tick < stop_after_rounds):
            self.tick_round(tick, datetime.now(UTC))
            tick += 1
            due_at = _next_due_at(due_at, interval_s)
            _sleep(max(0.0, due_at - _monotonic()))

        return tick

    def close(self) -> None:
        """Release every station's connections."""
        for member in self._members:
            member.station.close()
        self._members.clear()

    def __enter__(self) -> Supervisor:
        """Enter a context that closes the whole fleet on the way out."""
        return self

    def __exit__(self, *_exc_info: object) -> None:
        """Close, whether or not the body raised."""
        self.close()

    def _member_for(self, index: int) -> _Member:
        """Bring one station up behind its own fault injector."""
        # One state object, read by two things: the transport beneath the client
        # and the receiver above it. Four of the five faults are network
        # failures and one is a dead radio, so a station given only the first
        # half would report `no_signal` for a pass nothing received.
        faults = FaultState()
        station = register_or_resume(
            self._config,
            index,
            self._invite_for(index),
            http_transport=FaultInjectingTransport(self._inner_transport(), faults),
            faults=faults,
        )
        return _Member(
            index=index,
            station=station,
            schedule=schedule_for(
                seed_for_station(self._config.master_seed, index),
                self._config.scenario,
            ),
            faults=faults,
        )

    def _invite_for(self, index: int) -> str | None:
        """The invite offered to station ``index``, if the run was given one."""
        if index <= len(self._invite_tokens):
            return self._invite_tokens[index - 1]
        return None

    def _inner_transport(self) -> httpx.BaseTransport:
        """What the fault injector wraps: a real connection, or a test's.

        A new one per station rather than one shared: each station owns its
        connection pool, so closing one cannot pull the socket out from under
        another. A test's transport is passed through unchanged, because an
        in-process application has no pool to share.
        """
        return self._http_transport or httpx.HTTPTransport()

    def _restart(self, member: _Member) -> None:
        """Tear one station down and build it again from what reached disk.

        Everything held only in memory is lost — which assignment was executing,
        what the executor had decided and not yet handed over — and everything
        on disk survives. That asymmetry is the point: it is the same asymmetry
        a real power cut produces, and D-073 records the observation lost in the
        window between a decoder finishing and the queue write as a stated
        limit rather than a bug.
        """
        _log.info("station %d restarting", member.index)
        member.station.close()
        member.faults = FaultState(active=member.faults.active)
        member.station = register_or_resume(
            self._config,
            member.index,
            None,
            http_transport=FaultInjectingTransport(
                self._inner_transport(), member.faults
            ),
            faults=member.faults,
        )

    def _retire(self, member: _Member, reason: str) -> None:
        """Take one station out of the round for good."""
        _log.error(
            "station %d stopped: %s. An operator must intervene; the rest of the"
            " fleet continues (D-024)",
            member.index,
            reason,
        )
        member.station.close()
        self._members.remove(member)

    def interval_s(self) -> float:
        """The cadence the whole fleet keeps, in seconds.

        The shortest any station was given, so no station heartbeats later than
        the platform asked it to. A fleet on one cadence also makes a round a
        meaningful unit — every station has ticked exactly once per round, which
        is what lets a fault schedule be written in ticks.
        """
        if not self._members:
            return DEFAULT_INTERVAL_S
        return float(min(one.station.heartbeat_interval_s for one in self._members))


def _next_due_at(due_at: float, interval_s: float) -> float:
    """When the next round should fire, skipping any the last one ran past.

    A round that overran does not fire the missed ones back to back. Catching up
    sends a burst, and fifty stations catching up send it together — which looks
    to the platform exactly like the incident that caused it.
    """
    now = _monotonic()
    advanced = due_at + interval_s
    return advanced if advanced > now else now + interval_s


def _monotonic() -> float:
    """The scheduling clock, named so a test can substitute it."""
    return time.monotonic()


def _sleep(seconds: float) -> None:
    """Indirection so a test can run the fleet without waiting."""
    time.sleep(seconds)
