"""The seam between deciding to receive a pass and actually receiving one.

The heartbeat loop knows *when* a station should be receiving; it must not know
*how*. This module is the boundary between the two: one protocol with two calls,
and one implementation that occupies a window and receives nothing.

That implementation is not a placeholder for missing work. It is what a station
runs when it has no radio attached — a simulated station, a client under test, a
Raspberry Pi being commissioned before its SDR arrives — and in every one of
those cases the station still holds assignments, still reports ``listening``, and
still produces the evidence ``Registry.was_listening()`` needs. The real receiver
and decoder arrive in Stage 13 and implement the same two calls.

**The station never transmits.** Nothing here opens a transmit path, and no
implementation of this protocol may.

Reference: docs/ARCHITECTURE.md (station client); docs/DECISIONS.md D-069.
"""

from __future__ import annotations

import logging
from typing import Protocol

from meridian_client.assignment_message import Assignment
from meridian_client.observation_message import ObservationResult

__all__ = ["NullExecutor", "PassExecutor"]

_log = logging.getLogger(__name__)


class PassExecutor(Protocol):
    """Starts and stops reception for one assignment at a time.

    Named for what it does rather than ``Receiver``, which Stage 13 uses for the
    narrower thing that owns an SDR — an executor drives a receiver, a decoder
    and possibly a rotator, and the loop above it should depend on the whole job
    rather than on one part of it.

    **Neither call may block for the length of a pass.** The loop that drives
    this is single-threaded and must keep heartbeating every thirty seconds
    throughout an 8-to-15-minute reception; an implementation that blocked would
    stop the station reporting exactly while it had something to report, and the
    platform would read that as an outage.
    """

    def begin(self, assignment: Assignment) -> None:
        """Start receiving ``assignment``. Returns as soon as capture is running."""
        ...

    def end(self, assignment: Assignment) -> None:
        """Stop receiving ``assignment``, whether or not its window has closed."""
        ...

    def take_completed(self) -> tuple[ObservationResult, ...]:
        """Observations that became ready since the last call. Never blocks.

        Drained by the loop on every tick and handed straight to the upload
        queue, so an implementation may return work that finished long after the
        pass it belongs to.

        **Returned once.** A result the loop has taken is a result the loop is
        now responsible for, and returning it again would submit it twice.

        A drain rather than a value returned from :meth:`end` because a real
        executor is not finished when capture stops: Stage 13's runs a decoder
        subprocess afterwards, which takes time this loop must not spend
        waiting. An executor with nothing ready returns ``()`` (D-073).
        """
        ...


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
