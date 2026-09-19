"""The seam between deciding to receive a pass and actually receiving one.

The heartbeat loop knows *when* a station should be receiving; it must not know
*how*. This module is the boundary between the two: one protocol, and one
implementation that occupies a window and receives nothing.

That implementation is not a placeholder for missing work. It is what a station
runs when it has no radio attached — a simulated station, a client under test, a
Raspberry Pi being commissioned before its SDR arrives — and in every one of
those cases the station still holds assignments, still reports ``listening``, and
still produces the evidence ``Registry.was_listening()`` needs. The reception
layer below this protocol implements the same calls with a receiver, a decoder
and a rotator (D-120).

**The station never transmits.** Nothing here opens a transmit path, and no
implementation of this protocol may.

Reference: docs/ARCHITECTURE.md (station client); docs/DECISIONS.md D-069,
D-073, D-120, D-121.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from meridian_client.assignment_message import Assignment
from meridian_client.heartbeat import Listening
from meridian_client.observation_message import ObservationResult

__all__ = [
    "CaptureWindow",
    "ExecutionStatus",
    "NullExecutor",
    "PassExecutor",
    "assignment_capture_window",
    "assignment_status",
]

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CaptureWindow:
    """When an executor wants to be recording one assignment (D-121).

    Normally wider than the assignment's own window: MSP §4.3 asks a station to
    widen by ``timing_uncertainty_s`` when it can afford the recording. The loop
    never lets it be narrower — a capture that ended before ``end_at`` would let
    the station stop naming work whose window is still open.
    """

    opens_at: datetime
    closes_at: datetime


@dataclass(frozen=True, slots=True)
class ExecutionStatus:
    """What an executor says it is doing, for the heartbeat to report (D-121).

    Asked of the executor rather than inferred from the assignment, because
    ``listening`` is the claim CLAUDE.md rule 7 must be able to trust: a receiver
    that died mid-pass has to be able to stop making it.
    """

    state: str
    """One of MSP §4.2's states."""

    listening: Listening | None = None
    """Present only while the executor is actually tuned and recording."""

    unfinished: tuple[str, ...] = ()
    """Assignments whose results have not yet been handed over by
    :meth:`PassExecutor.take_completed`. The loop keeps naming them in
    ``held_assignments`` until they have been, so a pass still being decoded is
    never mistaken for a decline."""


class PassExecutor(Protocol):
    """Starts and stops reception for one assignment at a time.

    Named for what it does rather than ``Receiver``, which the reception layer
    uses for the narrower thing that owns an SDR — an executor drives a
    receiver, a decoder and possibly a rotator, and the loop above it should
    depend on the whole job rather than on one part of it.

    **No call may block for the length of a pass.** The loop that drives this is
    single-threaded and must keep heartbeating every thirty seconds throughout an
    8-to-15-minute reception; an implementation that blocked would stop the
    station reporting exactly while it had something to report, and the platform
    would read that as an outage.
    """

    def capture_window(self, assignment: Assignment) -> CaptureWindow:
        """When this executor wants ``assignment`` recording. Pure and cheap."""
        ...

    def begin(self, assignment: Assignment) -> None:
        """Start receiving ``assignment``. Returns as soon as capture is running."""
        ...

    def end(self, assignment: Assignment) -> None:
        """Stop receiving ``assignment``, or account for never having begun it.

        The loop also calls this for a held assignment whose capture window
        closed without :meth:`begin` — the station took the work and did not
        start it, and the honest report for that is ``not_attempted`` (MSP
        §4.4), not the silence the platform would read as a decline (D-121).
        """
        ...

    def status(self, running: Assignment | None) -> ExecutionStatus:
        """What to report on this tick's heartbeat. Never blocks.

        Args:
            running: What the loop has begun and not yet ended, or ``None``.
        """
        ...

    def take_completed(self) -> tuple[ObservationResult, ...]:
        """Observations that became ready since the last call. Never blocks.

        Drained by the loop on every tick and handed straight to the upload
        queue, so an implementation may return work that finished long after the
        pass it belongs to.

        **Returned once.** A result the loop has taken is a result the loop is
        now responsible for, and returning it again would submit it twice.

        A drain rather than a value returned from :meth:`end` because a real
        executor is not finished when capture stops: the reception layer runs a
        decoder subprocess afterwards, which takes time this loop must not spend
        waiting. An executor with nothing ready returns ``()`` (D-073).
        """
        ...


def assignment_capture_window(assignment: Assignment) -> CaptureWindow:
    """The assignment's own window, for an executor with nothing to widen for.

    The platform has already widened it by one σ (D-021). An executor with no
    recording to lose — no radio, or a simulated one — gains nothing by starting
    earlier, and keeping to this window is what keeps its behaviour identical to
    the loop's before D-121.
    """
    return CaptureWindow(assignment.start_at, assignment.end_at)


def assignment_status(running: Assignment | None) -> ExecutionStatus:
    """``listening`` to whatever is running, as the assignment describes it.

    For an executor that cannot tell a live receiver from a dead one, because it
    has none. Nothing is unfinished: such an executor produces its result, if
    any, inside :meth:`PassExecutor.end`, and the loop drains it the same tick.
    """
    if running is None:
        return ExecutionStatus("idle")
    return ExecutionStatus(
        "listening",
        Listening(
            assignment_id=running.assignment_id,
            satellite_id=running.satellite_id,
            centre_freq_hz=running.centre_freq_hz,
            mode=running.mode,
        ),
    )


class NullExecutor:
    """Occupies an assignment's window and receives nothing.

    Every state transition a real executor drives still happens — the loop
    reports ``listening``, the platform moves the assignment to ``in_progress``,
    and the heartbeat carries the frequency and mode the station claims to be
    tuned to. What is absent is the radio, so no observation follows.

    Logged rather than silent, because an operator watching a commissioning run
    needs to see that the schedule is being followed before there is a decoder to
    show for it.
    """

    def capture_window(self, assignment: Assignment) -> CaptureWindow:
        """The assignment's own window: there is no recording to widen."""
        return assignment_capture_window(assignment)

    def begin(self, assignment: Assignment) -> None:
        """Note the start of a window this station cannot actually receive."""
        _log.info(
            "would begin receiving %s (%s at %d Hz, %s) until %s",
            assignment.assignment_id,
            assignment.satellite_id,
            assignment.centre_freq_hz,
            assignment.mode,
            assignment.end_at.isoformat(),
        )

    def end(self, assignment: Assignment) -> None:
        """Note the end of that window."""
        _log.info("would stop receiving %s", assignment.assignment_id)

    def status(self, running: Assignment | None) -> ExecutionStatus:
        """What the assignment says, since there is no receiver to ask."""
        return assignment_status(running)

    def take_completed(self) -> tuple[ObservationResult, ...]:
        """Nothing, always.

        A station with no radio attached did not hear anything and did not fail
        to hear anything either — it was never in a position to say. Reporting
        ``no_signal`` here would put a measurement into the system of record
        that no receiver produced, and every reliability figure derived from the
        observation store would inherit it.

        The first executor that returns anything is the simulator's, which
        declares itself simulated at registration so its rows are labelled all
        the way through (MSP §5).
        """
        return ()
