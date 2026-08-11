"""The loop that runs a station unattended.

One thread, one heartbeat at a time. Each tick the station drops work whose
window has closed, starts work whose window has opened, states what it holds,
and takes delivery of whatever the platform sends back. Nothing here decides
what to receive — the platform scheduled that — and nothing here receives
anything, which is :mod:`meridian_client.execution`'s job.

Two properties are worth stating because they are what the loop is *for*:

* **Reception does not depend on the platform being reachable.** Work is started
  from the on-disk record, never from a response, so an outage during a pass
  changes nothing the station does. A heartbeat is how a station reports, not
  how it decides.
* **A tick that overran is skipped, not queued.** Catching up after an outage
  sends a burst, and fifty stations catching up send it together — which looks
  to the platform exactly like the incident that caused it.

Reference: docs/MSP-SPEC.md §4.2; docs/DECISIONS.md D-003, D-024, D-030, D-068,
D-069.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from meridian_client.assignment_message import Assignment
from meridian_client.credentials import StationCredentials
from meridian_client.execution import PassExecutor
from meridian_client.heartbeat import (
    HeartbeatResponse,
    Listening,
    StationState,
    build_heartbeat_body,
    parse_heartbeat_response,
)
from meridian_client.held_assignments import AssignmentRecord, due_now
from meridian_client.transport import READ_TIMEOUT_S, MspTransport, ProtocolError

__all__ = [
    "StationLoop",
    "TickOutcome",
    "retry_policy_attempts_for",
]

_log = logging.getLogger(__name__)

UNAUTHORIZED = "unauthorized"
"""MSP §6's code for a token that is missing, unknown or revoked.

The one error that stops the loop rather than being retried (D-024): a station
looping on a revoked token every thirty seconds against a publicly reachable
endpoint is a denial of service the network inflicts on itself, and with fifty
simulated stations it is one with a multiplier.
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

    stop_reason: str | None = None
    """Set only when the loop must not continue — today, only a revoked token."""


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
    """

    def __init__(
        self,
        transport: MspTransport,
        credentials: StationCredentials,
        record: AssignmentRecord,
        executor: PassExecutor,
    ) -> None:
        """Wire a station together. Nothing is sent until :meth:`tick`."""
        self._transport = transport
        self._credentials = credentials
        self._record = record
        self._executor = executor
        self._executing: Assignment | None = None

    def executing(self) -> Assignment | None:
        """What the station is receiving right now, or ``None``."""
        return self._executing

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
        """
        ended = self._stop_finished_work(now)
        began = self._start_due_work(now)

        try:
            response = self._send_heartbeat(now)
        except ProtocolError as exc:
            return self._outcome_for_protocol_error(exc, began, ended)
        except httpx.HTTPError as exc:
            # Unreachable, not refused. The station keeps executing and keeps
            # what it holds; the next tick carries the same statement.
            _log.warning("heartbeat could not reach the platform: %s", exc)
            return TickOutcome(False, (), began, ended)

        accepted = self._record.accept(response.assignments)
        return TickOutcome(
            True, tuple(one.assignment_id for one in accepted), began, ended
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
        """
        interval_s = float(self._credentials.heartbeat_interval_s)
        due_at = _monotonic()
        ticks = 0

        while stop_after_ticks is None or ticks < stop_after_ticks:
            outcome = self.tick(datetime.now(UTC))
            ticks += 1
            if outcome.stop_reason is not None:
                return outcome.stop_reason

            due_at = _next_due_at(due_at, interval_s)
            _sleep(max(0.0, due_at - _monotonic()))

        return None

    def _stop_finished_work(self, now: datetime) -> str | None:
        """End execution and forget assignments whose windows have closed."""
        self._record.drop_closed(now)
        running = self._executing
        if running is None or running.end_at >= now:
            return None
        self._executor.end(running)
        self._executing = None
        return running.assignment_id

    def _start_due_work(self, now: datetime) -> str | None:
        """Begin the assignment whose window is open, if one is and none is running."""
        if self._executing is not None:
            return None
        candidate = due_now(self._record.held(), now)
        if candidate is None:
            return None
        self._executor.begin(candidate)
        self._executing = candidate
        return candidate.assignment_id

    def _send_heartbeat(self, now: datetime) -> HeartbeatResponse:
        """Build and send one §4.2 heartbeat, returning the parsed response."""
        running = self._executing
        listening = (
            None
            if running is None
            else Listening(
                assignment_id=running.assignment_id,
                satellite_id=running.satellite_id,
                centre_freq_hz=running.centre_freq_hz,
                mode=running.mode,
            )
        )
        body = build_heartbeat_body(
            StationState(
                station_id=self._credentials.station_id,
                sent_at=now,
                state="listening" if running else "idle",
                listening=listening,
            ),
            # From the record, never from the last response: the field states
            # what survived, and a list built from what was just delivered would
            # claim work a crash could have lost.
            self._record.held_ids(),
        )
        return parse_heartbeat_response(self._transport.heartbeat(body))

    def _outcome_for_protocol_error(
        self, exc: ProtocolError, began: str | None, ended: str | None
    ) -> TickOutcome:
        """Decide whether an MSP error ends the loop or is merely this tick's."""
        if exc.code == UNAUTHORIZED:
            _log.error(
                "the platform revoked this station's token; stopping. An operator "
                "must issue a replacement invite bound to %s (D-024, D-034)",
                self._credentials.station_id,
            )
            return TickOutcome(False, (), began, ended, stop_reason=UNAUTHORIZED)
        _log.warning("heartbeat refused: %s", exc)
        return TickOutcome(False, (), began, ended)


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


def _sleep(seconds: float) -> None:
    """Indirection so a test can run the loop without waiting."""
    time.sleep(seconds)
