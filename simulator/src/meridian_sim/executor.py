"""A receiver that decides rather than listens — the simulator's `PassExecutor`.

Implements the seam the reference client already has
(:class:`meridian_client.execution.PassExecutor`): the loop calls ``begin`` when
a window opens and ``end`` when it closes, and drains finished work with
``take_completed``. Its capture window and its status are the assignment's own,
as they were before D-121 gave the seam a way to say otherwise: there is no
recording to widen and no receiver to die. Everything above this — heartbeats,
the held record, the upload queue, the transport — is the real client,
unchanged. This is the only piece of a virtual station that a real station
replaces with a radio.

It is also the first thing in this project that ever produced an observation.
``NullExecutor`` returns nothing on purpose, because a station with no receiver
has nothing to report, so until now every observation in every test was written
by the test.

**No clock.** The reported window is the assignment's own, and every other
instant is derived from it, which is what makes two runs at one seed produce
byte-identical bodies (D-077). A real station's window drifts by seconds against
its assignment; a virtual one has no rotator to be slow and no reason to invent
the difference. The one exception is Stage 27's stepped clock (D-277): the
reported window is still the assignment's, as the station believes, while what
it recorded is moved by the step (:mod:`~meridian_sim.clock_effects`).

What is decided here is only *when*: :mod:`~meridian_sim.outcomes` decides what
was heard, from the seed and the pass geometry, and this places those numbers on
the timeline the assignment gave it.

Reference: docs/MSP-SPEC.md §4.3, §4.4; docs/DECISIONS.md D-073, D-077, D-078.
"""

from __future__ import annotations

from dataclasses import replace

from meridian_client.assignment_message import Assignment
from meridian_client.execution import (
    CaptureWindow,
    ExecutionStatus,
    assignment_capture_window,
    assignment_status,
)
from meridian_client.observation_message import ObservationResult
from meridian_sim.clock_effects import names_pass, samples_moved, shift_recording
from meridian_sim.config import seed_for_pass
from meridian_sim.evidence import (
    SNR_SAMPLE_COUNT,
    evidence_for,
    station_noise_floor_dbfs,
)
from meridian_sim.faults import (
    CLOCK_STEP,
    DECODER_DEGRADED,
    RECEIVER_DOWN,
    SATELLITE_SILENT,
    FaultState,
)
from meridian_sim.outcomes import decide_outcome
from meridian_sim.report_blocks import decode_for, sample_instants, signal_for
from meridian_sim.sky_effects import PassContext, apply_sky_faults
from meridian_sim.sky_faults import ActiveSkyFault, Silence
from meridian_sim.sky_track import Site, SkyPoint, track

__all__ = ["SimulatedExecutor"]


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

    def __init__(
        self,
        station_seed: int,
        faults: FaultState | None = None,
        site: Site | None = None,
    ) -> None:
        """Build a receiver for one station. Nothing is decided until a pass ends.

        Args:
            station_seed: This station's seed.
            faults: What is currently broken, written by the supervisor each
                tick. ``None`` is a receiver that always works, which is what a
                clean run and most tests want.
            site: Where the station stands, which an obstruction or an
                interference source needs to know where in its sky a pass was.
                ``None`` for a receiver that is never given one.
        """
        self._station_seed = station_seed
        self._site = site
        self._sky_at_begin: dict[str, tuple[ActiveSkyFault, ...]] = {}
        self._step_at_begin: dict[str, float] = {}
        self._noise_floor_dbfs = station_noise_floor_dbfs(station_seed)
        self._faults = faults if faults is not None else FaultState()
        self._begun: set[str] = set()
        self._held_but_not_begun: set[str] = set()
        self._ready: list[ObservationResult] = []
        self._faulted: list[tuple[str, str]] = []

    def capture_window(self, assignment: Assignment) -> CaptureWindow:
        """The assignment's own window, which is what keeps D-077's bodies fixed."""
        return assignment_capture_window(assignment)

    def status(self, running: Assignment | None) -> ExecutionStatus:
        """``listening`` whenever the loop is running a pass, even a faulted one.

        A virtual station with a downed receiver reports what the reference
        client reported before D-121. Stage 25 gives the simulator faults that
        show up in the heartbeat; this stage keeps its behaviour unchanged.
        """
        return assignment_status(running)

    def begin(self, assignment: Assignment) -> None:
        """Start receiving ``assignment``, unless the receiver is down.

        A broken radio does not stop the client: the station goes on
        heartbeating and goes on holding the work, exactly as a real one with a
        dead SDR would. What it cannot do is receive, and the honest report for
        that is ``not_attempted`` when the window closes.
        """
        if RECEIVER_DOWN in self._faults.active:
            self._held_but_not_begun.add(assignment.assignment_id)
            self._faulted.append((RECEIVER_DOWN, assignment.assignment_id))
            return
        self._begun.add(assignment.assignment_id)
        self._sky_at_begin[assignment.assignment_id] = self._faults.sky
        # A silence is written down as the pass begins, while its window is
        # certainly open: it may close before this pass ends, and the ledger
        # refuses an act on a window that has closed. Only a pass it changes is
        # named (D-253), and a pass that will abort measures nothing a silence
        # could change; whether it aborts is its seed's, known already.
        if any(
            isinstance(one.fault.shape, Silence)
            and one.fault.shape.satellite_id == assignment.satellite_id
            for one in self._faults.sky
        ) and not self._aborts(assignment):
            self._faulted.append((SATELLITE_SILENT, assignment.assignment_id))
        self._note_step(assignment)

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

    def take_faulted(self) -> tuple[tuple[str, str], ...]:
        """Which passes a fault changed since the last call, as (kind, id) pairs.

        For the run's fault ledger, which the supervisor writes: the executor
        knows which pass a dead receiver or a failing decoder touched, and
        nothing on MSP may say so (D-189).
        """
        faulted = tuple(self._faulted)
        self._faulted.clear()
        return faulted

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
        if DECODER_DEGRADED in self._faults.active and outcome.outcome == "decoded":
            # Heard and not decoded: the signal block stays, because the radio
            # did hear it, and only the claim of frames goes (Stage 27's
            # negative control).
            outcome = replace(outcome, outcome="signal_no_decode")
            self._faulted.append((DECODER_DEGRADED, assignment.assignment_id))
        window_s = (assignment.end_at - assignment.start_at).total_seconds()
        evidence = evidence_for(seed, outcome, window_s, self._noise_floor_dbfs)
        affected = apply_sky_faults(
            outcome,
            evidence,
            self._sky_at_begin.pop(assignment.assignment_id, ()),
            self._context(assignment, seed, window_s),
        )
        outcome, evidence = shift_recording(
            affected.outcome,
            affected.evidence,
            self._step_at_begin.pop(assignment.assignment_id, 0.0),
            self._context(assignment, seed, window_s),
        )
        self._faulted.extend(
            (kind, assignment.assignment_id)
            for kind in affected.kinds
            if kind != SATELLITE_SILENT
        )
        return ObservationResult(
            assignment_id=assignment.assignment_id,
            started_at=assignment.start_at,
            ended_at=assignment.end_at,
            outcome=outcome.outcome,
            signal=signal_for(outcome, evidence, assignment),
            client_notes=_notes_for(seed),
            decode=decode_for(outcome, evidence, window_s),
        )

    def _aborts(self, assignment: Assignment) -> bool:
        """Whether this pass will abort on its own, as :meth:`_observe` decides."""
        seed = self._pass_seed(assignment)
        outcome = decide_outcome(seed, assignment.expected_max_elevation_deg)
        return outcome.outcome == "aborted"

    def _note_step(self, assignment: Assignment) -> None:
        """Keep a stepped clock's error for this pass, and name the pass.

        Named as the pass begins, for the reason a silence is: the window may
        close before the pass ends. Whether it moves is decided from the clean
        outcome, which is all that exists yet (D-277).
        """
        step = self._faults.clock_step_s
        if step == 0:
            return
        self._step_at_begin[assignment.assignment_id] = step
        window_s = (assignment.end_at - assignment.start_at).total_seconds()
        clean = decide_outcome(
            self._pass_seed(assignment), assignment.expected_max_elevation_deg
        )
        if names_pass(clean, samples_moved(step, window_s, SNR_SAMPLE_COUNT)):
            self._faulted.append((CLOCK_STEP, assignment.assignment_id))

    def _pass_seed(self, assignment: Assignment) -> int:
        """The seed deciding this station's experience of this assignment."""
        return seed_for_pass(self._station_seed, assignment.assignment_id)

    def _context(
        self, assignment: Assignment, seed: int, window_s: float
    ) -> PassContext:
        """The pass as a sky fault sees it: its samples' instants and directions."""
        instants = sample_instants(assignment, SNR_SAMPLE_COUNT)
        site = self._site

        def directions() -> tuple[SkyPoint, ...]:
            if site is None:
                raise ValueError(
                    "a fault in a sector of the sky needs the station's site"
                )
            elements = assignment.element_set
            return track(elements.line1, elements.line2, site, instants)

        return PassContext(
            pass_seed=seed,
            satellite_id=assignment.satellite_id,
            instants=instants,
            window_s=window_s,
            track=directions,
        )


def _notes_for(seed: int) -> str:
    """What this observation says about where it came from.

    The pass seed, so one row can be reproduced on its own without re-running
    the fleet that produced it. Every published number has to be regenerable
    from a snapshot, a config and a seed, and this is the third of those
    travelling with the row rather than in a notebook beside it.
    """
    return f"simulated pass, seed {seed}"
