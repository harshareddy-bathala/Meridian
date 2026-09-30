"""What a virtual station measured on a pass: MSP 0.3's reception evidence.

:mod:`~meridian_sim.outcomes` decides *what happened* — heard, decoded, or
neither. This decides *what the receiver and decoder would have measured*, given
that: a noise floor at a stated gain, signal-to-noise across the window, and the
decoder's frame counts (MSP §4.4 at 0.3, D-103). Stage 25 needs it so the
evidence a fault changes exists in the first place — a raised noise floor, a
shortfall in SNR, frames lost in one part of the sky — and so the platform's
`noise_measurements` has simulated rows to be kept apart from measured ones.

**The outcome decides and the evidence follows.** Nothing here can turn a
decoded pass into a lost one; that is a fault's job, which degrades this
evidence and recounts the frames by the same rule. Two models of one pass that
could disagree would be worse than one model that is plainly a shape.

**It is a shape, not a link budget.** SNR rises from the window's edges to its
middle and peaks at the outcome's ``peak_snr_db``; frames are counted where it
clears a decoding bar. Nothing here knows the orbit, the antenna or the
atmosphere, and every number is labelled illustrative. It inherits D-078 from
the outcome model: a simulated observation is never training or evaluation
data, and its evidence is no exception.

**Drawn from a stream of its own**, seeded on the pass seed, and always in the
same order whatever the outcome, so adding this changed no outcome any seed
already gave, and a fault that changes the outcome afterwards does not move a
single draw.

Reference: docs/MSP-SPEC.md §4.4; docs/DECISIONS.md D-078, D-103, D-117, D-122,
D-251.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from meridian_sim.outcomes import SimulatedOutcome

__all__ = [
    "DECODER",
    "DECODER_VERSION",
    "DECODE_SNR_DB",
    "DETECT_SNR_DB",
    "FRAME_INTERVAL_S",
    "NOISE_ONLY_SNR_DB",
    "RECEIVER_GAIN_DB",
    "SNR_SAMPLE_COUNT",
    "PassEvidence",
    "count_frames",
    "decodable_frames",
    "evidence_for",
    "station_noise_floor_dbfs",
]

DECODER = "meridian-sim"
"""The decoder every virtual station names. Obviously not a real one, so a
simulated statistic is never segmented together with SatDump's (D-117)."""

DECODER_VERSION = "evidence-1"
"""The version of this module's model, reported as the decoder's version.

Calibration is segmented by decoder and version (EVALUATION.md §11.1), so a
change to how evidence is shaped here is a new version rather than a silent
shift in every statistic already stored.
"""

RECEIVER_GAIN_DB = 30.0
"""The gain every virtual receiver runs at, fixed.

A noise floor is only comparable with the same station's floors at the same
gain (D-103), so a fleet that changed gain would make every interference test
compare unlike figures. Thirty is an illustrative RTL-SDR setting.
"""

NOISE_FLOOR_RANGE_DBFS = (-62.0, -52.0)
"""The range a station's own noise floor is drawn from, once, from its seed.

A station's floor is a property of its site and its chain, so it is the same
from pass to pass apart from :data:`NOISE_JITTER_DB`; a floor redrawn every pass
would bury the rise an interference fault causes.
"""

NOISE_JITTER_DB = 0.5
"""How far one pass's floor strays from its station's, either way."""

SNR_SAMPLE_COUNT = 25
"""Samples across a window — odd, so one sits exactly at the middle.

Well under MSP §6's cap of 512 for the reason Doppler's count is.
"""

EDGE_DROP_DB = 20.0
"""How far SNR falls from the peak at the middle to the window's edges.

Large enough that a zenith pass's 18 dB peak is below the detection bar at the
edges: the window is widened beyond the pass (D-021), and a station hears
nothing while the satellite is below its horizon.
"""

SNR_JITTER_DB = 0.5
"""How far one sample falls below the smooth curve. Only below, so the
reported peak is the highest sample, as a station computing it would report."""

NOISE_ONLY_SNR_DB = 1.5
"""A sample of nothing reads within this of zero, either way."""

DETECT_SNR_DB = 3.0
"""The bar for "heard", the reference client's own default (D-100, D-122)."""

DECODE_SNR_DB = 5.0
"""The bar for a frame decoding. Illustrative, like every figure here."""

FRAME_INTERVAL_S = 0.113778
"""Meteor LRPT's nominal frame interval: 8192 bits at 72 kbit/s.

The same figure the development catalogue gives the platform (D-250). Repeated
rather than shared, because ``meridian-sim`` and ``meridian`` share no code
(D-138); a station would equally know its own decoder's frame size.
"""


@dataclass(frozen=True, slots=True)
class PassEvidence:
    """What the receiver and decoder measured over one window."""

    noise_floor_dbfs: float
    receiver_gain_db: float
    snr_db: tuple[float, ...]
    """One per sample, spread evenly from the window's start to its end."""

    frames_decoded: int
    frames_failed: int


def station_noise_floor_dbfs(station_seed: int) -> float:
    """A station's own noise floor, drawn once from its seed."""
    stream = random.Random(f"{station_seed}:noise-floor")
    return round(stream.uniform(*NOISE_FLOOR_RANGE_DBFS), 1)


def evidence_for(
    pass_seed: int,
    outcome: SimulatedOutcome,
    window_s: float,
    station_floor_dbfs: float,
) -> PassEvidence | None:
    """The evidence one pass's outcome implies.

    Args:
        pass_seed: This station-and-pass pair's seed.
        outcome: What :func:`~meridian_sim.outcomes.decide_outcome` decided.
        window_s: The length of the window the samples span.
        station_floor_dbfs: The station's own floor, from
            :func:`station_noise_floor_dbfs`.

    Returns:
        The evidence, or ``None`` for an ``aborted`` pass, which stopped before
        a decoder ran and reports nothing it measured (D-122).
    """
    if outcome.outcome == "aborted":
        return None
    stream = random.Random(f"{pass_seed}:evidence")
    floor_jitter = stream.uniform(-NOISE_JITTER_DB, NOISE_JITTER_DB)
    jitters = tuple(stream.random() for _ in range(SNR_SAMPLE_COUNT))

    if outcome.peak_snr_db is None:
        snr = tuple(round((2 * one - 1) * NOISE_ONLY_SNR_DB, 1) for one in jitters)
    else:
        snr = _pass_shape(
            outcome.peak_snr_db, jitters, outcome.detection_offset_s, window_s
        )

    decoded, failed = count_frames(snr, window_s, outcome.outcome)
    return PassEvidence(
        noise_floor_dbfs=round(station_floor_dbfs + floor_jitter, 1),
        receiver_gain_db=RECEIVER_GAIN_DB,
        snr_db=snr,
        frames_decoded=decoded,
        frames_failed=failed,
    )


def count_frames(
    snr_db: tuple[float, ...], window_s: float, outcome: str
) -> tuple[int, int]:
    """Frames decoded and failed, from where SNR cleared each bar.

    Each sample stands for an equal share of the window. The share above
    :data:`DECODE_SNR_DB` sent frames that decoded; the share between the two
    bars sent frames that were heard and failed. A pass that heard something
    and decoded nothing counts every heard frame as failed, and one that heard
    nothing counts none — the zero MSP §4.4 requires of both (D-117).

    A decoded pass always counts at least one frame, because the outcome said
    it decoded and D-117 refuses ``decoded`` with none: at the lowest peaks the
    samples may sit under the bar the outcome's own draw cleared.
    """
    decodable = decodable_frames(snr_db, window_s)
    slot_s = window_s / len(snr_db) if snr_db else 0.0
    between = sum(1 for one in snr_db if DETECT_SNR_DB <= one < DECODE_SNR_DB) * slot_s
    heard_only = math.floor(between / FRAME_INTERVAL_S)
    if outcome == "decoded":
        return max(1, decodable), heard_only
    if outcome == "signal_no_decode":
        return 0, decodable + heard_only
    return 0, 0


def decodable_frames(snr_db: tuple[float, ...], window_s: float) -> int:
    """Frames sent while SNR was above :data:`DECODE_SNR_DB`, before any floor.

    The raw count :func:`count_frames` builds on, and the one a fault's effect
    compares before and after: a pass that lost none of these lost nothing a
    decoder could have used.
    """
    slot_s = window_s / len(snr_db) if snr_db else 0.0
    above = sum(1 for one in snr_db if one >= DECODE_SNR_DB) * slot_s
    return math.floor(above / FRAME_INTERVAL_S)


def _pass_shape(
    peak_snr_db: float,
    jitters: tuple[float, ...],
    detection_offset_s: float | None,
    window_s: float,
) -> tuple[float, ...]:
    """SNR rising from the edges to ``peak_snr_db`` at the middle.

    Before the drawn detection instant, a sample is held under the detection
    bar, so the evidence never says a station heard the satellite before the
    instant it reports first hearing it.
    """
    span = SNR_SAMPLE_COUNT - 1
    middle = span // 2
    detected_from_s = detection_offset_s or 0.0
    samples: list[float] = []
    for index, jitter in enumerate(jitters):
        fraction = index / span
        value = peak_snr_db - EDGE_DROP_DB * (1.0 - math.sin(math.pi * fraction))
        if index != middle:
            value -= jitter * SNR_JITTER_DB
        if fraction * window_s < detected_from_s:
            value = min(value, DETECT_SNR_DB - 0.1)
        samples.append(round(value, 1))
    return tuple(samples)
