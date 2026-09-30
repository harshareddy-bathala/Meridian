"""A decided pass, as MSP §4.4's ``signal`` and ``decode`` blocks.

The executor decides what a pass produced; this lays it out on the wire's
shapes, placing every measurement on the timeline the assignment gave it.
Kept apart from the executor so each reads on its own: one is when, the other
is how it is written down.

Reference: docs/MSP-SPEC.md §4.4; docs/DECISIONS.md D-072, D-117, D-122, D-251.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from meridian_client.assignment_message import Assignment
from meridian_client.observation_message import (
    Decode,
    DopplerSample,
    Signal,
    SnrSample,
)
from meridian_sim.evidence import (
    DECODER,
    DECODER_VERSION,
    PassEvidence,
    count_frames,
)
from meridian_sim.outcomes import SimulatedOutcome

__all__ = ["DETECTED_OUTCOMES", "decode_for", "sample_instants", "signal_for"]

DETECTED_OUTCOMES = frozenset({"decoded", "signal_no_decode"})
"""Outcomes that assert something was heard, and so carry a detection."""


def signal_for(
    outcome: SimulatedOutcome,
    evidence: PassEvidence | None,
    assignment: Assignment,
) -> Signal | None:
    """The MSP §4.4 signal block: heard, measured and not heard, or nothing.

    A pass that heard nothing still measured its noise floor and an SNR that
    never cleared the bar, and says so with ``detected: false`` — the shape the
    reference client reports for ``no_signal`` (D-122). It is evidence as much
    as a detection is: a silent satellite and a raised floor look different
    here and nowhere else. Only a pass with no evidence at all, an aborted one,
    carries no block.
    """
    if evidence is None:
        return None
    detected = outcome.outcome in DETECTED_OUTCOMES
    return Signal(
        detected=detected,
        first_detection_at=(
            detection_instant(outcome, assignment) if detected else None
        ),
        peak_snr_db=outcome.peak_snr_db if detected else None,
        doppler_samples=doppler_samples(outcome, assignment) if detected else None,
        noise_floor_dbfs=evidence.noise_floor_dbfs,
        receiver_gain_db=evidence.receiver_gain_db,
        snr_samples=snr_samples(evidence, assignment),
    )


def decode_for(
    outcome: SimulatedOutcome, evidence: PassEvidence | None, window_s: float
) -> Decode | None:
    """The decoder's account, counted for the outcome as reported.

    Recounted here rather than taken from the evidence, because a fault may
    have changed the outcome after the evidence was drawn: a degraded decoder
    turns ``decoded`` into ``signal_no_decode``, and D-117 then requires zero
    frames decoded.
    """
    if evidence is None:
        return None
    decoded, failed = count_frames(evidence.snr_db, window_s, outcome.outcome)
    return Decode(
        decoder=DECODER,
        decoder_version=DECODER_VERSION,
        frames_decoded=decoded,
        frames_failed=failed,
    )


def snr_samples(
    evidence: PassEvidence, assignment: Assignment
) -> tuple[SnrSample, ...]:
    """The SNR series, spread evenly from the window's start to its end."""
    instants = sample_instants(assignment, len(evidence.snr_db))
    return tuple(
        SnrSample(sampled_at=at, snr_db=value)
        for at, value in zip(instants, evidence.snr_db, strict=True)
    )


def sample_instants(assignment: Assignment, count: int) -> tuple[datetime, ...]:
    """``count`` instants spread evenly from the window's start to its end."""
    span_s = (assignment.end_at - assignment.start_at).total_seconds()
    steps = max(count - 1, 1)
    return tuple(
        assignment.start_at + timedelta(seconds=span_s * index / steps)
        for index in range(count)
    )


def detection_instant(outcome: SimulatedOutcome, assignment: Assignment) -> datetime:
    """When the station first heard the transmitter.

    Clamped inside the window. The offset is drawn from a range that suits an
    eight-to-fifteen minute pass, and a short window would otherwise place the
    detection after the pass ended — a body nothing downstream validates and
    every reader would have to puzzle over.
    """
    offset_s = outcome.detection_offset_s or 0.0
    detected_at = assignment.start_at + timedelta(seconds=offset_s)
    return min(detected_at, assignment.end_at)


def doppler_samples(
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
