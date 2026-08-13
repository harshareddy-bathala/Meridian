"""A receiver that decides rather than listens — the simulator's `PassExecutor`.

Implements the seam the reference client already has
(:class:`meridian_client.execution.PassExecutor`): the loop calls ``begin`` when
a window opens and ``end`` when it closes, and drains finished work with
``take_completed``. Everything above this — heartbeats, the held record, the
upload queue, the transport — is the real client, unchanged. This is the only
piece of a virtual station that a real station replaces with a radio.

It is also the first thing in this project that ever produced an observation.
``NullExecutor`` returns nothing on purpose, because a station with no receiver
has nothing to report, so until now every observation in every test was written
by the test.

**No clock.** The reported window is the assignment's own, and every other
instant is derived from it, which is what makes two runs at one seed produce
byte-identical bodies (D-077). A real station's window drifts by seconds against
its assignment; a virtual one has no rotator to be slow and no reason to invent
the difference.

What is decided here is only *when*: :mod:`~meridian_sim.outcomes` decides what
was heard, from the seed and the pass geometry, and this places those numbers on
the timeline the assignment gave it.

Reference: docs/MSP-SPEC.md §4.3, §4.4; docs/DECISIONS.md D-073, D-077, D-078.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from meridian_client.assignment_message import Assignment
from meridian_client.observation_message import (
    DopplerSample,
    ObservationResult,
    Signal,
)
from meridian_sim.config import seed_for_pass
from meridian_sim.faults import RECEIVER_DOWN, FaultState
from meridian_sim.outcomes import SimulatedOutcome, decide_outcome

__all__ = ["SimulatedExecutor"]

DETECTED_OUTCOMES = frozenset({"decoded", "signal_no_decode"})
"""Outcomes that assert something was heard, and so carry a signal block."""


class SimulatedExecutor:
    """One virtual station's receiver.

    Args:
        station_seed: This station's seed, from
            :func:`~meridian_sim.config.seed_for_station`. Every pass this
            executor decides is drawn from it and the assignment's id, so the
            same station reaches the same conclusion about the same pass however
            many times it is restarted.

    Note:
        Satisfies :class:`~meridian_client.execution.PassExecutor` structurally
        rather than by inheritance, which is how the reference client's own
        ``NullExecutor`` does it — mypy checks conformance at every call site
        that expects the protocol.

        **A result is produced only for a pass this executor began.** That is
        not defensiveness about the loop, which always pairs the two calls; it
        is what lets the fault schedule express a station that took the work and
        never started, whose honest report is ``not_attempted`` and whose
        dishonest one would be a `no_signal` nothing listened for.
    """

    def __init__(self, station_seed: int, faults: FaultState | None = None) -> None:
        """Build a receiver for one station. Nothing is decided until a pass ends.

        Args:
            station_seed: This station's seed.
            faults: What is currently broken, written by the supervisor each
                tick. ``None`` is a receiver that always works, which is what a
                clean run and most tests want.
        """
        self._station_seed = station_seed
        self._faults = faults if faults is not None else FaultState()
        self._begun: set[str] = set()
        self._held_but_not_begun: set[str] = set()
        self._ready: list[ObservationResult] = []

    def begin(self, assignment: Assignment) -> None:
        """Start receiving ``assignment``, unless the receiver is down.

        A broken radio does not stop the client: the station goes on
        heartbeating and goes on holding the work, exactly as a real one with a
        dead SDR would. What it cannot do is receive, and the honest report for
        that is ``not_attempted`` when the window closes.
        """
        if RECEIVER_DOWN in self._faults.active:
            self._held_but_not_begun.add(assignment.assignment_id)
            return
        self._begun.add(assignment.assignment_id)

    def end(self, assignment: Assignment) -> None:
        """Stop receiving, and decide what the pass produced.

        The decision happens here rather than in :meth:`take_completed` so the
        result exists the moment the window closes, and the drain that follows
        is a handover rather than a computation. A real executor does the
        opposite — Stage 13's runs a decoder subprocess after this returns —
        and the seam is shaped for that case, not this one.

        **A pass that was never begun still produces a report**, and a different
        one. MSP §4.4 separates *listened and heard nothing*, which is data,
        from *never began*, which is an operational failure, and is emphatic
        that the two must never be conflated. Reporting nothing at all would be
        a third thing again — a decline — which this station did not do, since
        it went on naming the assignment in every heartbeat.
        """
        if assignment.assignment_id in self._held_but_not_begun:
            self._held_but_not_begun.discard(assignment.assignment_id)
            self._ready.append(self._abandoned(assignment))
            return
        if assignment.assignment_id not in self._begun:
            return
        self._begun.discard(assignment.assignment_id)
        self._ready.append(self._observe(assignment))

    def take_completed(self) -> tuple[ObservationResult, ...]:
        """Hand over what is finished, once."""
        completed = tuple(self._ready)
        self._ready.clear()
        return completed

    def _abandoned(self, assignment: Assignment) -> ObservationResult:
        """A pass this station took and never started.

        Unreachable from geometry, and deliberately so: only something that
        knows the station failed can report an operational failure, which is why
        :mod:`~meridian_sim.outcomes` never returns this value and the fault
        schedule is what produces it.
        """
        return ObservationResult(
            assignment_id=assignment.assignment_id,
            started_at=assignment.start_at,
            ended_at=assignment.end_at,
            outcome="not_attempted",
            signal=None,
            client_notes=_notes_for(self._pass_seed(assignment)),
        )

    def _observe(self, assignment: Assignment) -> ObservationResult:
        """Turn one finished window into the observation it produced."""
        seed = self._pass_seed(assignment)
        outcome = decide_outcome(seed, assignment.expected_max_elevation_deg)
        return ObservationResult(
            assignment_id=assignment.assignment_id,
            started_at=assignment.start_at,
            ended_at=assignment.end_at,
            outcome=outcome.outcome,
            signal=_signal_for(outcome, assignment),
            client_notes=_notes_for(seed),
        )

    def _pass_seed(self, assignment: Assignment) -> int:
        """The seed deciding this station's experience of this assignment."""
        return seed_for_pass(self._station_seed, assignment.assignment_id)


def _signal_for(outcome: SimulatedOutcome, assignment: Assignment) -> Signal | None:
    """The MSP §4.4 signal block, or nothing when nothing was heard.

    Absent rather than present-and-empty for an unheard pass: the platform
    refuses a body whose outcome contradicts its signal block, and ``no_signal``
    carrying a detection is exactly that contradiction.
    """
    if outcome.outcome not in DETECTED_OUTCOMES:
        return None
    return Signal(
        detected=True,
        first_detection_at=_detection_instant(outcome, assignment),
        peak_snr_db=outcome.peak_snr_db,
        doppler_samples=_doppler_samples(outcome, assignment),
    )


def _detection_instant(outcome: SimulatedOutcome, assignment: Assignment) -> datetime:
    """When the station first heard the transmitter.

    Clamped inside the window. The offset is drawn from a range that suits an
    eight-to-fifteen minute pass, and a short window would otherwise place the
    detection after the pass ended — a body nothing downstream validates and
    every reader would have to puzzle over.
    """
    offset_s = outcome.detection_offset_s or 0.0
    detected_at = assignment.start_at + timedelta(seconds=offset_s)
    return min(detected_at, assignment.end_at)


def _doppler_samples(
    outcome: SimulatedOutcome, assignment: Assignment
) -> tuple[DopplerSample, ...] | None:
    """The Doppler series, spread evenly from the window's start to its end.

    Order is preserved and is significant: the series is a time series, and the
    platform's content hash treats two orderings as two different measurements
    (D-070).
    """
    offsets = outcome.doppler_offsets_hz
    if offsets is None:
        return None
    span_s = (assignment.end_at - assignment.start_at).total_seconds()
    steps = max(len(offsets) - 1, 1)
    return tuple(
        DopplerSample(
            sampled_at=assignment.start_at + timedelta(seconds=span_s * index / steps),
            offset_hz=offset,
        )
        for index, offset in enumerate(offsets)
    )


def _notes_for(seed: int) -> str:
    """What this observation says about where it came from.

    The pass seed, so one row can be reproduced on its own without re-running
    the fleet that produced it. Every published number has to be regenerable
    from a snapshot, a config and a seed, and this is the third of those
    travelling with the row rather than in a notebook beside it.
    """
    return f"simulated pass, seed {seed}"
