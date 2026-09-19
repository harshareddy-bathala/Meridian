"""From the facts of one reception to the observation it earns (D-122).

A pure function: the assignment, the recording, the decode outcome and a policy
in; an :class:`~meridian_client.observation_message.ObservationResult` out. No
clock, no disk. The first row that matches wins:

=================================================  ====================  ========
Facts                                              ``outcome``           signal
=================================================  ====================  ========
Never started                                      ``not_attempted``     —
Started; the decode failed or its report was bad   ``aborted``           —
Interrupted, or too little of the window covered   ``aborted``           evidence
Complete; frames ≥ 1 and a detection instant       ``decoded``           detected
Complete; frames ≥ 1 and no instant                ``aborted``           —
Complete; frames 0 or uncounted; SNR ≥ threshold   ``signal_no_decode``  detected
Complete; frames 0; nothing ≥ threshold            ``no_signal``         measured
Anything else                                      ``aborted``           evidence
=================================================  ====================  ========

**``no_signal`` is reached only through a complete capture and a successful
decode that counted zero frames.** Every failure of the station's own chain is
``aborted`` or ``not_attempted``, so absence of signal is never confused with a
broken station (hard rule 7). A decoder that counts no frames at all and measures
nothing above the threshold reaches the last row: it could not establish absence.

**Unknown is absent, never zero**, and no instant is guessed: a pass whose frames
came with no timing is ``aborted``, not ``decoded`` at a made-up time.

Reference: docs/MSP-SPEC.md §4.4; docs/DECISIONS.md D-032, D-072, D-100, D-117,
D-122.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from meridian_client.assignment_message import Assignment
from meridian_client.observation_message import (
    MAX_SNR_SAMPLES,
    Decode,
    ObservationResult,
    Signal,
    SnrSample,
)
from meridian_client.reception.decode_report import (
    DecodeFailure,
    DecodeReport,
    SnrPoint,
)
from meridian_client.reception.protocols import Recording

__all__ = [
    "OutcomePolicy",
    "ReceptionFacts",
    "coverage_of",
    "derive_result",
    "reduce_snr",
]


@dataclass(frozen=True, slots=True)
class OutcomePolicy:
    """The two judgements the table needs that no measurement supplies."""

    snr_threshold_db: float = 3.0
    """The SNR at which a signal counts as detected. Named in ``client_notes``
    with every detection it produces, because first detection depends on the
    detector (D-100)."""

    minimum_coverage: float = 0.8
    """The fraction of the assignment's window a recording must span to count as
    complete. A capture that missed a fifth of the pass cannot say nothing was
    there."""

    def __post_init__(self) -> None:
        """Refuse a policy the table could not apply."""
        if not math.isfinite(self.snr_threshold_db):
            raise ValueError("snr_threshold_db must be finite")
        if not 0.0 < self.minimum_coverage <= 1.0:
            raise ValueError("minimum_coverage must be in (0, 1]")


@dataclass(frozen=True, slots=True)
class ReceptionFacts:
    """Everything known about one reception once it is over."""

    assignment: Assignment
    recording: Recording | None = None
    """``None`` for a reception that never started."""

    decode: DecodeReport | DecodeFailure | None = None
    reason: str | None = None
    """Why it never started, or why nothing could be decoded."""


@dataclass(frozen=True, slots=True)
class _Detection:
    offset_s: float
    method: str
    """How it was detected, for ``client_notes`` (D-100)."""


@dataclass(frozen=True, slots=True)
class _Reception:
    """A reception that recorded something and has a valid report."""

    assignment: Assignment
    recording: Recording
    report: DecodeReport
    policy: OutcomePolicy


def derive_result(facts: ReceptionFacts, policy: OutcomePolicy) -> ObservationResult:
    """The observation one reception earns, by the first row of D-122 it matches.

    Args:
        facts: The reception.
        policy: The threshold and coverage the table applies.

    Returns:
        A result every rule of MSP §4.4 accepts.
    """
    if facts.recording is None:
        return _not_attempted(facts)
    if not isinstance(facts.decode, DecodeReport):
        failure = facts.decode.reason if facts.decode is not None else facts.reason
        return _result(
            facts.assignment,
            facts.recording,
            "aborted",
            reason=failure or "no decode ran",
        )
    reception = _Reception(facts.assignment, facts.recording, facts.decode, policy)
    if (
        facts.recording.interrupted
        or coverage_of(facts.recording, facts.assignment) < policy.minimum_coverage
    ):
        detection = _detection(reception)
        return _reported(
            reception, "aborted", detection, reason=_incomplete_reason(reception)
        )
    return _complete(reception)


def _complete(reception: _Reception) -> ObservationResult:
    """Rows four to eight: a complete capture with a valid report."""
    frames = reception.report.frames_decoded
    if frames is not None and frames >= 1:
        return _with_frames(reception)
    crossing = _snr_crossing(reception)
    if crossing is not None:
        return _reported(reception, "signal_no_decode", crossing)
    if frames == 0:
        return _reported(reception, "no_signal", None)
    return _reported(
        reception,
        "aborted",
        None,
        reason="no frames were counted and no signal reached the threshold",
    )


def _with_frames(reception: _Reception) -> ObservationResult:
    """Rows four and five: frames were decoded, with or without an instant."""
    detection = _detection(reception)
    if detection is not None:
        return _reported(reception, "decoded", detection)
    # MSP §4.4: frames with no detection instant are `aborted`, with the decode
    # block and no signal block. The timing measurement admits no guessed instant.
    return _result(
        reception.assignment,
        reception.recording,
        "aborted",
        decode=_decode_block(reception.report),
        reason="frames were decoded with no timing",
    )


def coverage_of(recording: Recording, assignment: Assignment) -> float:
    """The fraction of the assignment's window the recording spans."""
    window_s = (assignment.end_at - assignment.start_at).total_seconds()
    if window_s <= 0:
        return 1.0
    overlap_s = (
        min(recording.stopped_at, assignment.end_at)
        - max(recording.first_sample_at, assignment.start_at)
    ).total_seconds()
    return max(0.0, overlap_s) / window_s


def reduce_snr(
    points: Sequence[SnrPoint], limit: int = MAX_SNR_SAMPLES
) -> tuple[SnrPoint, ...]:
    """At most ``limit`` points: the one nearest the centre of each equal time bucket.

    Raw values, never averages (D-032): a mean of a rise and a fade is a number no
    receiver measured. A bucket with no point stays empty, so the result can be
    shorter than ``limit``, and it keeps the series' order. Ties go to the earlier
    point.
    """
    if len(points) <= limit:
        return tuple(points)
    start, end = points[0].offset_s, points[-1].offset_s
    width = (end - start) / limit
    if width == 0:
        return tuple(points[:limit])
    nearest: dict[int, SnrPoint] = {}
    for point in points:
        bucket = min(int((point.offset_s - start) / width), limit - 1)
        centre = start + (bucket + 0.5) * width
        best = nearest.get(bucket)
        if best is None or abs(point.offset_s - centre) < abs(best.offset_s - centre):
            nearest[bucket] = point
    return tuple(nearest[bucket] for bucket in sorted(nearest))


def _not_attempted(facts: ReceptionFacts) -> ObservationResult:
    """The station took the work and never began; it measured nothing (MSP §4.4).

    Reported over the assignment's own window, because there is no recording to
    give it another.
    """
    assignment = facts.assignment
    return ObservationResult(
        assignment_id=assignment.assignment_id,
        started_at=assignment.start_at,
        ended_at=assignment.end_at,
        outcome="not_attempted",
        client_notes=facts.reason or "capture never started",
    )


def _reported(
    reception: _Reception,
    outcome: str,
    detection: _Detection | None,
    *,
    reason: str | None = None,
) -> ObservationResult:
    """A result carrying the report's decode block and its evidence."""
    return _result(
        reception.assignment,
        reception.recording,
        outcome,
        signal=_evidence(reception, detection),
        decode=_decode_block(reception.report),
        reason=reason,
        method=None if detection is None else detection.method,
    )


def _result(  # noqa: PLR0913 — each keyword is one optional part of the body
    assignment: Assignment,
    recording: Recording,
    outcome: str,
    *,
    signal: Signal | None = None,
    decode: Decode | None = None,
    reason: str | None = None,
    method: str | None = None,
) -> ObservationResult:
    """An observation over the recording's own span, with its notes."""
    notes = "; ".join(
        part
        for part in (
            recording.notes,
            reason,
            None if method is None else f"detected by {method}",
        )
        if part
    )
    return ObservationResult(
        assignment_id=assignment.assignment_id,
        started_at=recording.first_sample_at,
        ended_at=recording.stopped_at,
        outcome=outcome,
        signal=signal,
        client_notes=notes or None,
        decode=decode,
    )


def _decode_block(report: DecodeReport) -> Decode:
    return Decode(
        decoder=report.decoder,
        decoder_version=report.decoder_version,
        frames_decoded=report.frames_decoded,
        frames_failed=report.frames_failed,
    )


def _snr_crossing(reception: _Reception) -> _Detection | None:
    """The first SNR point at or above the threshold, as a detection."""
    threshold = reception.policy.snr_threshold_db
    for point in reception.report.snr or ():
        if point.snr_db >= threshold:
            return _Detection(
                point.offset_s, f"first SNR sample at or above {threshold:g} dB"
            )
    return None


def _detection(reception: _Reception) -> _Detection | None:
    """The earliest evidence of a signal: a decoded frame, or an SNR crossing."""
    offset = reception.report.first_frame_offset_s
    frame = None if offset is None else _Detection(offset, "first decoded frame")
    crossing = _snr_crossing(reception)
    candidates = [one for one in (frame, crossing) if one is not None]
    return min(candidates, key=lambda one: one.offset_s, default=None)


def _evidence(reception: _Reception, detection: _Detection | None) -> Signal | None:
    """The signal block: the detection if any, and whatever was measured.

    ``None`` when there is nothing to say. A noise floor travels only with the
    gain it was measured at (D-117), and the peak is taken over the whole series
    before it is reduced to 512 samples (D-122).
    """
    report, recording = reception.report, reception.recording
    floor = report.noise_floor_dbfs if recording.gain_db is not None else None
    series = report.snr
    if detection is None and series is None and floor is None:
        return None
    return Signal(
        detected=detection is not None,
        first_detection_at=(
            None if detection is None else _instant(recording, detection.offset_s)
        ),
        peak_snr_db=max((point.snr_db for point in series), default=None)
        if series
        else None,
        noise_floor_dbfs=floor,
        receiver_gain_db=recording.gain_db,
        snr_samples=(
            None
            if series is None
            else tuple(
                SnrSample(_instant(recording, point.offset_s), point.snr_db)
                for point in reduce_snr(series)
            )
        ),
    )


def _instant(recording: Recording, offset_s: float) -> datetime:
    """An offset into the recording, as an instant (D-122)."""
    return recording.first_sample_at + timedelta(seconds=offset_s)


def _incomplete_reason(reception: _Reception) -> str:
    if reception.recording.interrupted:
        return "capture was interrupted"
    percent = round(100 * coverage_of(reception.recording, reception.assignment))
    return f"capture covered {percent}% of the window"
