"""The loop that runs a station unattended.

One thread, one heartbeat at a time. Each tick the station stops a capture whose
window has closed, starts one whose window has opened, queues whatever finished,
forgets work it has handed over, states what it holds, takes delivery of what the
platform sends back, and delivers what it owes. Nothing here decides what to
receive — the platform scheduled that — nothing here receives anything, which is
:mod:`meridian_client.execution`'s job, and nothing here decides which capture
comes next or what a refused submission means, which are
:mod:`meridian_client.capture_sequence`'s and
:mod:`meridian_client.observation_submission`'s.

Three properties are worth stating because they are what the loop is *for*:

* **Reception does not depend on the platform being reachable.** Work is started
  from the on-disk record, never from a response, so an outage during a pass
  changes nothing the station does. A heartbeat is how a station reports, not
  how it decides.
* **Reporting does not depend on it either.** A finished observation is written
  to the queue before any request is attempted, so an outage delays delivery and
  never costs the result.
* **A tick that overran is skipped, not queued.** Catching up after an outage
  sends a burst, and fifty stations catching up send it together — which looks
  to the platform exactly like the incident that caused it.

Reference: docs/MSP-SPEC.md §4.2, §4.4, §6; docs/DECISIONS.md D-003, D-024,
D-030, D-068, D-069, D-073, D-121.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime

import httpx

from meridian_client.assignment_message import Assignment
from meridian_client.capture_sequence import CaptureSequence
from meridian_client.credentials import StationCredentials
from meridian_client.execution import PassExecutor
from meridian_client.heartbeat import (
    HeartbeatResponse,
    StationState,
    build_heartbeat_body,
    parse_heartbeat_response,
)
from meridian_client.held_assignments import AssignmentRecord
from meridian_client.observation_message import build_observation_body
from meridian_client.observation_queue import ObservationQueue
from meridian_client.observation_submission import submit_pending
from meridian_client.transport import (
    READ_TIMEOUT_S,
    UNAUTHORIZED,
    MspTransport,
    ProtocolError,
)

__all__ = [
    "StationLoop",
    "TickOutcome",
    "retry_policy_attempts_for",
]

_log = logging.getLogger(__name__)

EDGE_MARGIN_S = 0.5
"""How far past a capture edge the loop wakes, so the tick lands on its far side.

A sleep can return a hair early against the wall clock it is aimed at, and a tick
just before an opening starts nothing and sleeps for the same edge again.
"""


def retry_policy_attempts_for(interval_s: float) -> int:
    """How many heartbeat attempts fit inside one interval.

    Args:
        interval_s: The cadence the platform asked for at registration.

    Returns:
        At least one attempt, and at most as many as can complete before the
        next tick is due.

    Note:
        The transport's default of four attempts is sized for a one-off request,
        not for something on a timer. Four attempts at
        :data:`~meridian_client.transport.READ_TIMEOUT_S` seconds each, plus
        backoff, can exceed forty seconds — against a thirty-second interval that
        is a heartbeat still in flight when the next one is due.

        **A heartbeat that cannot finish inside its own interval has already
        failed.** Retrying it further does not deliver it sooner than the next
        tick would, and the next tick carries the same information anyway,
        because a heartbeat states current holdings rather than announcing a
        change (D-003).
    """
    return max(1, int(interval_s // READ_TIMEOUT_S))


@dataclass(frozen=True, slots=True)
class TickOutcome:
    """What one pass through the loop did, for a caller's log and for tests."""

    heartbeat_sent: bool
    """``False`` when the platform could not be reached. Not a reason to stop:
    the station keeps whatever it holds and tries again on the next tick."""

    accepted: tuple[str, ...]
    began: str | None
    ended: str | None

    submitted: tuple[str, ...] = ()
    """Observations the platform acknowledged on this tick, oldest first.

    Empty is the normal case: a station produces one of these per pass, not one
    per heartbeat.
    """

    stop_reason: str | None = None
    """Set only when the loop must not continue — today, only a revoked token."""

    not_begun: tuple[str, ...] = ()
    """Held assignments whose capture closed without being begun, handed to the
    executor's ``end`` so it can report them ``not_attempted`` (D-121)."""


class StationLoop:
    """Drives one station: heartbeat, hold, execute, repeat.

    Args:
        transport: An authenticated transport. Built with an attempt count from
            :func:`retry_policy_attempts_for` if the caller wants the heartbeat
            to fit inside its interval, which it should.
        credentials: This station's identity, including the cadence the platform
            asked for at registration — §4.2's response does not repeat it.
        record: Where held assignments live between ticks and across reboots.
        executor: What actually receives. :class:`~meridian_client.execution.
            NullExecutor` for a station with no radio attached.
        queue: Where finished observations wait until the platform has them.
    """

    def __init__(
        self,
        transport: MspTransport,
        credentials: StationCredentials,
        record: AssignmentRecord,
        executor: PassExecutor,
        queue: ObservationQueue,
    ) -> None:
        """Wire a station together. Nothing is sent until :meth:`tick`."""
        self._transport = transport
        self._credentials = credentials
        self._record = record
        self._executor = executor
        self._queue = queue
        self._captures = CaptureSequence(record, executor)

    def executing(self) -> Assignment | None:
        """What the station is receiving right now, or ``None``."""
        return self._captures.executing()

    def tick(self, now: datetime) -> TickOutcome:
        """One heartbeat, and the work it implies.

        Args:
            now: Timezone-aware UTC. Passed in rather than read here, so the
                whole of the loop's behaviour can be tested without waiting.

        Returns:
            What happened, including a ``stop_reason`` when the loop must end.

        Note:
            **Execution is decided before the body is built**, so a pass that
            opened between two ticks is reported as ``listening`` on the tick
            that starts it rather than the one after. At a thirty-second cadence
            the difference is thirty seconds of an eight-minute pass, and it is
            the part carrying the rise.

            **Finished work is written to the queue before the heartbeat and
            submitted after it.** Queueing first means an observation is on disk
            even if the request that follows never returns; submitting after
            means the tick has already told the platform the station is alive,
            which is the message that matters most when a backlog is draining.
        """
        ended, not_begun = self._captures.stop_finished(now)
        began = self._captures.start_due(now)
        self._queue_completed_work()
        self._captures.forget_handed_over(now)
        work = TickOutcome(False, (), began, ended, not_begun=not_begun)

        try:
            response = self._send_heartbeat(now)
        except ProtocolError as exc:
            return self._outcome_for_protocol_error(exc, work)
        except httpx.HTTPError as exc:
            # Unreachable, not refused. The station keeps executing and keeps
            # what it holds; the next tick carries the same statement. Nothing is
            # submitted either — the queue is on disk and loses nothing by waiting.
            _log.warning("heartbeat could not reach the platform: %s", exc)
            return work

        accepted = self._record.accept(response.assignments)
        run = submit_pending(self._transport, self._queue, now)
        return replace(
            work,
            heartbeat_sent=True,
            accepted=tuple(one.assignment_id for one in accepted),
            submitted=run.submitted,
            stop_reason=run.stop_reason,
        )

    def run(self, *, stop_after_ticks: int | None = None) -> str | None:
        """Tick on the platform's cadence until something says to stop.

        Args:
            stop_after_ticks: Stop after this many ticks. For tests and for a
                one-shot commissioning run; ``None`` runs until the platform
                revokes the token or the caller interrupts.

        Returns:
            The reason the loop ended, or ``None`` if it ran out of ticks.

        Note:
            Scheduled on :func:`time.monotonic`, not on the wall clock, so an
            NTP step cannot make the loop spin or stall — the one clock a station
            is expected to correct is the one it must not schedule against.

            **It also wakes at every capture edge**, so a capture starts and stops
            on time rather than up to one interval late. The wake is an extra
            tick with its own heartbeat — the one that reports ``listening``
            promptly — and leaves the heartbeat grid where it was (D-121).
        """
        interval_s = float(self._credentials.heartbeat_interval_s)
        due_at = _monotonic()
        ticks = 0

        while stop_after_ticks is None or ticks < stop_after_ticks:
            outcome = self.tick(_wall_clock())
            ticks += 1
            if outcome.stop_reason is not None:
                return outcome.stop_reason

            # An edge wake before the grid's tick is not that tick.
            if _monotonic() >= due_at:
                due_at = _next_due_at(due_at, interval_s)
            _sleep(self._seconds_until_next_tick(due_at))

        return None

    def _seconds_until_next_tick(self, due_at: float) -> float:
        """The wait until the next heartbeat or the next capture edge, if sooner."""
        until_heartbeat = max(0.0, due_at - _monotonic())
        now = _wall_clock()
        edge = self._captures.next_edge(now)
        if edge is None:
            return until_heartbeat
        return min(until_heartbeat, (edge - now).total_seconds() + EDGE_MARGIN_S)

    def _queue_completed_work(self) -> None:
        """Take whatever the executor has finished and write it down.

        Note:
            An observation exists only in memory between the executor producing
            it and this write returning, and a crash in that window loses it.
            Nothing closes the window without making the executor responsible
            for durability, which would hide the handover from the loop
            entirely — so it is logged as a real loss rather than swallowed
            (D-073).
        """
        for result in self._executor.take_completed():
            body = build_observation_body(result, self._credentials.station_id)
            try:
                self._queue.enqueue(body)
            except (OSError, ValueError):
                _log.exception(
                    "observation for %s could not be queued and is lost",
                    result.assignment_id,
                )

    def _send_heartbeat(self, now: datetime) -> HeartbeatResponse:
        """Build and send one §4.2 heartbeat, returning the parsed response.

        The state and ``listening`` block are the executor's, not the
        assignment's: a receiver that died mid-pass must be able to stop
        claiming it is listening (D-121, CLAUDE.md rule 7).
        """
        status = self._captures.status()
        body = build_heartbeat_body(
            StationState(
                station_id=self._credentials.station_id,
                sent_at=now,
                state=status.state,
                listening=status.listening,
            ),
            # From the record, never from the last response: the field states
            # what survived, and a list built from what was just delivered would
            # claim work a crash could have lost.
            self._record.held_ids(),
        )
        return parse_heartbeat_response(self._transport.heartbeat(body))

    def _outcome_for_protocol_error(
        self, exc: ProtocolError, work: TickOutcome
    ) -> TickOutcome:
        """Decide whether an MSP error ends the loop or is merely this tick's."""
        if exc.code == UNAUTHORIZED:
            _log.error(
                "the platform revoked this station's token; stopping. An operator "
                "must issue a replacement invite bound to %s (D-024, D-034)",
                self._credentials.station_id,
            )
            return replace(work, stop_reason=UNAUTHORIZED)
        _log.warning("heartbeat refused: %s", exc)
        return work


def _next_due_at(due_at: float, interval_s: float) -> float:
    """When the next tick should fire, skipping any the last one ran past.

    A tick that overran does not fire the missed ones back to back. The station
    has nothing to say about a moment that has passed — a heartbeat states the
    present — so the burst would carry no information and would arrive exactly
    when the platform was least able to absorb it.
    """
    now = _monotonic()
    advanced = due_at + interval_s
    return advanced if advanced > now else now + interval_s


def _monotonic() -> float:
    """The scheduling clock, named so a test can substitute it.

    Monotonic rather than wall clock: an NTP correction is exactly the event a
    station is expected to have, and scheduling against a clock that can step
    backwards makes the loop stall, while one that steps forwards makes it spin.
    """
    return time.monotonic()


def _wall_clock() -> datetime:
    """The instant a tick is stamped with, named so a test can substitute it."""
    return datetime.now(UTC)


def _sleep(seconds: float) -> None:
    """Indirection so a test can run the loop without waiting."""
    time.sleep(seconds)
