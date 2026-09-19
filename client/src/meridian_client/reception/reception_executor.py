"""The executor that actually receives: a receiver, a rotator and a decoder.

Implements :class:`meridian_client.execution.PassExecutor`, the station loop's
only view of reception (D-120). One assignment moves through its capture folder's
phases like this:

* ``begin`` checks the pass can be worked — a decoder for its mode, room on the
  disk, an antenna, a receiver — and starts capture, or records why it could not.
* ``end`` stops capture and queues the recording for decoding.
* ``take_completed``, on every tick, watches the receiver, starts the next decode
  when none is running, polls the one that is, and hands over finished results.
* ``status`` says what the heartbeat may claim: ``listening`` only while the
  receiver is alive, ``processing`` while a decode waits or runs, ``degraded``
  when a pass is open and not being captured (D-121).

**Nothing here blocks.** The receiver and decoder are other processes or files,
polled; the only waits are the bounded seconds of stopping one.

**A synthetic receiver cannot report for a real station.** The executor refuses
that pairing on construction, before any pass (D-125, hard rule 5).

Reference: docs/DECISIONS.md D-069, D-073, D-120 to D-126.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import timedelta

from meridian_client.assignment_message import Assignment
from meridian_client.execution import CaptureWindow, ExecutionStatus
from meridian_client.heartbeat import Listening
from meridian_client.observation_message import ObservationResult
from meridian_client.reception.capture_folder import (
    CaptureFolders,
    MalformedManifestError,
)
from meridian_client.reception.capture_recovery import (
    discard_recording,
    facts_for,
    recover,
)
from meridian_client.reception.decode_report import DecodeFailure
from meridian_client.reception.disk_guard import DiskGuard
from meridian_client.reception.manifest import Manifest, RecordingStamp, advance
from meridian_client.reception.outcome_rules import OutcomePolicy, derive_result
from meridian_client.reception.protocols import (
    CapturePlan,
    CaptureRefusedError,
    DecodeJob,
    Decoder,
    DecodeRun,
    Receiver,
    RotatorController,
    StationClocks,
    Tuning,
)

__all__ = ["ReceptionExecutor", "ReceptionSetup"]

_log = logging.getLogger(__name__)

NEVER_BEGUN = "the capture window closed before capture began"
RECORDING_CHANGED = "the recording changed or disappeared before its decode"


@dataclass(frozen=True, slots=True)
class ReceptionSetup:
    """The parts one station receives with."""

    receiver: Receiver
    decoder: Decoder
    rotator: RotatorController
    folders: CaptureFolders
    policy: OutcomePolicy = field(default_factory=OutcomePolicy)
    disk: DiskGuard = field(default_factory=DiskGuard)
    keep_recordings: bool = False
    """Leave each recording in place after its result is handed over. Off by
    default: a pass is about a gigabyte, and a Pi's disk is not an archive."""


@dataclass(slots=True)
class _Capture:
    assignment: Assignment
    manifest: Manifest
    receiver_died: bool = False


class ReceptionExecutor:
    """Receives, decodes and reports each assignment the loop begins.

    Args:
        setup: The receiver, decoder, rotator and capture folders.
        clocks: The wall clock for manifests; the decoder has the monotonic one.
        simulated_station: Whether this station registered as simulated.

    Raises:
        ValueError: A receiver that does not hear the sky, for a station that
            did not register as simulated (D-125).

    Note:
        Construction resumes every reception a previous run left unfinished
        (D-123), so the first tick already hands over what a crash interrupted.
    """

    def __init__(
        self, setup: ReceptionSetup, clocks: StationClocks, *, simulated_station: bool
    ) -> None:
        """Refuse a dishonest pairing, then recover from the capture folders."""
        if not simulated_station and not setup.receiver.hears_the_sky:
            raise ValueError(
                "a receiver that does not hear the sky cannot report for a station "
                "registered as not simulated: its observations would be simulated "
                "results presented as measured (D-125)"
            )
        self._setup = setup
        self._clocks = clocks
        self._capture: _Capture | None = None
        self._decode_queue: deque[Manifest] = deque()
        self._decoding: tuple[Manifest, DecodeRun] | None = None
        self._ready: list[Manifest] = []
        self._handed: list[Manifest] = []

        recovery = recover(
            setup.folders, clocks.wall(), keep_recordings=setup.keep_recordings
        )
        self._decode_queue.extend(recovery.to_decode)
        self._ready.extend(recovery.to_hand_over)

    def capture_window(self, assignment: Assignment) -> CaptureWindow:
        """The assignment's window, widened by its timing uncertainty at each end.

        The platform has already widened by one σ (D-021); this adds a second,
        as MSP §4.3 asks of a station that can afford the recording (D-121).
        """
        margin = timedelta(seconds=assignment.timing_uncertainty_s)
        return CaptureWindow(assignment.start_at - margin, assignment.end_at + margin)

    def begin(self, assignment: Assignment) -> None:
        """Start capturing ``assignment``, or record why it cannot be captured."""
        folders = self._setup.folders
        if self._capture is not None:
            _log.error(
                "asked to begin %s while capturing %s; ignored",
                assignment.assignment_id,
                self._capture.assignment.assignment_id,
            )
            return
        if self._already_worked(assignment.assignment_id):
            _log.info(
                "%s has already been worked; not begun again", assignment.assignment_id
            )
            return
        window = self.capture_window(assignment)
        plan = CapturePlan(
            assignment_id=assignment.assignment_id,
            satellite_id=assignment.satellite_id,
            centre_freq_hz=assignment.centre_freq_hz,
            mode=assignment.mode,
            opens_at=window.opens_at,
            closes_at=window.closes_at,
            folder=folders.folder_for(assignment.assignment_id),
        )
        try:
            tuning = self._start_capture(plan)
        except (CaptureRefusedError, OSError) as exc:
            # An OSError is a refusal too — a capture folder that cannot be made,
            # a device that cannot be opened. Letting it escape would stop the
            # station loop over one pass.
            self._refuse(assignment, str(exc))
            return
        now = self._clocks.wall()
        manifest = Manifest(
            assignment, "capturing", now, capture_started_at=now, tuning=tuning
        )
        folders.write(manifest)
        self._capture = _Capture(assignment, manifest)

    def end(self, assignment: Assignment) -> None:
        """Stop capturing ``assignment``, or account for never having begun it."""
        capture = self._capture
        if (
            capture is not None
            and capture.assignment.assignment_id == assignment.assignment_id
        ):
            self._stop_capture(capture)
            return
        if not self._already_worked(assignment.assignment_id):
            self._refuse(assignment, NEVER_BEGUN)

    def _already_worked(self, assignment_id: str) -> bool:
        """Whether a capture folder already records this assignment.

        An unreadable manifest counts: it is evidence of a reception this station
        started, and writing a fresh one over it would destroy that evidence.
        """
        try:
            return self._setup.folders.read(assignment_id) is not None
        except MalformedManifestError:
            _log.exception("the capture folder for %s is unreadable", assignment_id)
            return True

    def status(self, running: Assignment | None) -> ExecutionStatus:
        """What this tick's heartbeat may claim (D-121)."""
        unfinished = self._unfinished()
        capture = self._capture
        if (
            running is not None
            and capture is not None
            and capture.assignment.assignment_id == running.assignment_id
        ):
            return self._capture_status(capture, unfinished)
        if running is not None:
            return ExecutionStatus("degraded", None, unfinished)
        if self._decoding is not None or self._decode_queue:
            return ExecutionStatus("processing", None, unfinished)
        return ExecutionStatus("idle", None, unfinished)

    def take_completed(self) -> tuple[ObservationResult, ...]:
        """Advance every reception one step, and hand over what has finished.

        The results returned last time are marked ``handed_over`` first: by now
        the loop has written them to its queue. A crash between that write and
        this mark re-emits an identical body after restart, which the platform
        stores once (D-073, D-123).
        """
        self._mark_handed_over()
        self._watch_receiver()
        self._advance_decodes()
        ready, self._ready = self._ready, []
        self._handed = ready
        folders, policy = self._setup.folders, self._setup.policy
        return tuple(
            derive_result(
                facts_for(
                    manifest, folders.folder_for(manifest.assignment.assignment_id)
                ),
                policy,
            )
            for manifest in ready
        )

    def _start_capture(self, plan: CapturePlan) -> Tuning:
        """Check the pass can be worked, then point the antenna and start recording.

        Raises:
            CaptureRefusedError: With the first reason it cannot.
            OSError: The receiver could not reach its device or its folder.
        """
        setup = self._setup
        if not setup.decoder.supports(plan.mode):
            raise CaptureRefusedError(f"no decoder is configured for {plan.mode}")
        setup.disk.require_room_for(plan)
        setup.rotator.prepare(plan)
        try:
            return setup.receiver.start(plan)
        except (CaptureRefusedError, OSError):
            setup.rotator.release()
            raise

    def _stop_capture(self, capture: _Capture) -> None:
        """Stop the receiver, write down the recording, and queue its decode."""
        self._capture = None
        recording = self._setup.receiver.stop()
        self._setup.rotator.release()
        if capture.receiver_died:
            recording = replace(recording, interrupted=True)
        try:
            stamp: RecordingStamp | None = RecordingStamp.of(recording.path)
        except OSError:
            stamp = None
        manifest = advance(
            replace(capture.manifest, recording=recording, recording_stamp=stamp),
            "captured",
            self._clocks.wall(),
        )
        self._setup.folders.write(manifest)
        self._decode_queue.append(manifest)

    def _refuse(self, assignment: Assignment, reason: str) -> None:
        """Record a pass that will not be captured, and ready its ``not_attempted``."""
        _log.warning("%s not attempted: %s", assignment.assignment_id, reason)
        manifest = Manifest(assignment, "refused", self._clocks.wall(), reason=reason)
        self._setup.folders.write(manifest)
        self._ready.append(manifest)

    def _mark_handed_over(self) -> None:
        folders = self._setup.folders
        for manifest in self._handed:
            folders.write(advance(manifest, "handed_over", self._clocks.wall()))
            if not self._setup.keep_recordings:
                folder = folders.folder_for(manifest.assignment.assignment_id)
                discard_recording(manifest, folder)
        self._handed = []

    def _watch_receiver(self) -> None:
        """Notice a receiver that died inside its window. It is stopped at ``end``."""
        capture = self._capture
        if (
            capture is not None
            and not capture.receiver_died
            and not self._setup.receiver.alive()
        ):
            capture.receiver_died = True
            _log.error(
                "the receiver died while capturing %s", capture.assignment.assignment_id
            )

    def _advance_decodes(self) -> None:
        """Poll the running decode; start the next one if none is running."""
        if self._decoding is not None:
            manifest, run = self._decoding
            outcome = run.poll()
            if outcome is None:
                return
            self._decoding = None
            reason = outcome.reason if isinstance(outcome, DecodeFailure) else None
            self._report(manifest, reason)
        if self._decode_queue:
            self._start_decode(self._decode_queue.popleft())

    def _start_decode(self, manifest: Manifest) -> None:
        """Decode one recording, oldest first, refusing one that has changed."""
        recording = manifest.recording
        if recording is None or not manifest.recording_unchanged():
            self._report(manifest, RECORDING_CHANGED)
            return
        decoding = advance(manifest, "decoding", self._clocks.wall())
        self._setup.folders.write(decoding)
        folder = self._setup.folders.folder_for(manifest.assignment.assignment_id)
        run = self._setup.decoder.start(
            DecodeJob(recording=recording, mode=manifest.assignment.mode, folder=folder)
        )
        self._decoding = (decoding, run)

    def _report(self, manifest: Manifest, reason: str | None) -> None:
        """Write down that a reception's result is settled, and ready it."""
        reported = advance(
            replace(manifest, reason=reason), "reported", self._clocks.wall()
        )
        self._setup.folders.write(reported)
        self._ready.append(reported)

    def _capture_status(
        self, capture: _Capture, unfinished: tuple[str, ...]
    ) -> ExecutionStatus:
        """``listening`` to what the receiver tuned, while it is alive."""
        self._watch_receiver()
        tuning = capture.manifest.tuning
        if capture.receiver_died or tuning is None:
            return ExecutionStatus("degraded", None, unfinished)
        listening = Listening(
            assignment_id=capture.assignment.assignment_id,
            satellite_id=capture.assignment.satellite_id,
            centre_freq_hz=tuning.centre_freq_hz,
            mode=capture.assignment.mode,
        )
        return ExecutionStatus("listening", listening, unfinished)

    def _unfinished(self) -> tuple[str, ...]:
        """Every assignment whose result has not yet been marked handed over."""
        manifests = [
            *self._decode_queue,
            *self._ready,
            *self._handed,
            *(() if self._decoding is None else (self._decoding[0],)),
        ]
        ids = [manifest.assignment.assignment_id for manifest in manifests]
        if self._capture is not None:
            ids.append(self._capture.assignment.assignment_id)
        return tuple(dict.fromkeys(ids))
