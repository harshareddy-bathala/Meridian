"""What a virtual station decides it heard on one pass.

Takes a seed and a pass's maximum elevation, and returns an outcome with the
measurements that go with it. Numbers in, numbers out: no clock, no assignment
type, no network — so the whole model is a table a reader can check, and two runs
at one seed can be compared without anything being started.

**Elevation drives the outcome, and that is a deliberate hazard.** A pass that
climbs higher clears more atmosphere and less local obstruction, so it decodes
more often; a simulator that ignored that would produce a network whose passes
succeed at random, against which a scheduler ranking by elevation performs
exactly as well as one ranking by coin flip — and the scheduler is one of the two
things this simulator exists to exercise.

The hazard is that elevation is also the prediction model's strongest feature. A
model trained or evaluated on these observations would rediscover this file and
score well for a reason that means nothing. **Simulated observations are
therefore excluded from every training set and every evaluation set** (D-078) —
not merely from reported aggregates, because no reported aggregate would have
mixed them and the number would still have been meaningless.

The same applies twice over to ``detection_offset_s``. The gap between predicted
acquisition and first detection, read against element-set age, is this project's
primary measurement of orbital data quality. Here it is *drawn from a seed* and
has no relationship to any element set at all. It exists so the field is
populated and its path is exercised, and it is the clearest single reason a
simulated observation can never appear in an analysis.

Reference: docs/MSP-SPEC.md §4.4; docs/DECISIONS.md D-077, D-078.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

__all__ = ["SimulatedOutcome", "decide_outcome"]

SIGNAL_FLOOR_DEG = 5.0
SIGNAL_CEILING_DEG = 40.0
"""Between these, the chance of hearing anything rises from none to certain.

A pass grazing the horizon traverses the most atmosphere and the most of
whatever the operator built next to the antenna. By 40° the path is clear enough
that a working station hears the transmitter essentially always.
"""

DECODE_FLOOR_DEG = 10.0
DECODE_CEILING_DEG = 60.0
"""Between these, the chance of decoding a heard signal rises from none to certain.

Higher than the detection ramp on both ends, because hearing a carrier and
recovering frames from it are different bars: LRPT needs sustained signal-to-noise
across the whole frame, and a low pass that is audible throughout can still yield
nothing. The lower bound sits at the 10° where decode success is known to
collapse.
"""

ABORT_PROBABILITY = 0.02
"""How often a station gives up part-way through a pass it had started.

A rotator that stalls, a receiver that resets, an operator's laptop that sleeps.
Independent of elevation, because none of those care how high the satellite got.
Two per hundred is a guess — it is the one number in this file with no reasoning
behind it beyond "rare but not negligible", and it is here rather than at zero
because a fleet that never aborted would leave MSP §4.4's ``aborted`` outcome
untested by anything.
"""

PEAK_SNR_FLOOR_DB = 3.0
PEAK_SNR_CEILING_DB = 18.0
"""The signal-to-noise a detected pass reports, from a graze to a zenith pass."""

DETECTION_OFFSET_FLOOR_S = 5.0
DETECTION_OFFSET_CEILING_S = 90.0
"""How long after the window opens a station first hears the transmitter.

Wider at low elevation, where the satellite spends longer in the noise before it
is distinguishable. Drawn, not computed — see this module's header.
"""

MAX_DOPPLER_OFFSET_HZ = 3000
"""Peak Doppler excursion at 137 MHz in low Earth orbit, in whole hertz.

Roughly ±3 kHz, which is wider than the receiver's passband and is why a station
retunes continuously through a pass rather than sitting on the nominal frequency.
"""

DOPPLER_JITTER_HZ = 40
"""How far a reported sample strays from the smooth curve.

A real measurement is a frequency estimate from a noisy signal, so a series that
sat exactly on a curve would be the one thing a residual analysis could never
see. The value is illustrative rather than measured.
"""

DOPPLER_SAMPLE_COUNT = 24
"""Samples reported across one pass — roughly one every thirty seconds.

Far below MSP §6's cap of 512, because that cap is a limit rather than a target
and a station reporting at the limit would tell an analysis nothing a tenth as
many samples did not.
"""


@dataclass(frozen=True, slots=True)
class SimulatedOutcome:
    """What one virtual station concluded about one pass.

    Carries no instants. The offsets here are relative to the start of the pass
    window, and only the executor knows when that was — which is what keeps this
    module free of a clock and therefore checkable in a table.
    """

    outcome: str
    """One of MSP §4.4's five values."""

    detection_offset_s: float | None
    """Seconds after the window opened that the signal was first heard.

    Present exactly when the outcome asserts a detection. Drawn from the seed
    and unrelated to any element set — see this module's header.
    """

    peak_snr_db: float | None
    """Best signal-to-noise seen, present exactly when something was heard."""

    doppler_offsets_hz: tuple[int, ...] | None
    """Observed-minus-nominal offsets, evenly spaced across the window.

    ``None`` when nothing was heard, which is a different claim from an empty
    series: one says the station had nothing to measure, the other that it
    measured and found nothing worth reporting.
    """


def decide_outcome(seed: int, expected_max_elevation_deg: float) -> SimulatedOutcome:
    """Decide what a station heard, from its seed and the pass's geometry.

    Args:
        seed: This station-and-pass pair's seed, from
            :func:`~meridian_sim.config.seed_for_pass`.
        expected_max_elevation_deg: Culmination of the pass, as the platform
            predicted it and delivered it in the assignment (MSP §4.3).

    Returns:
        The outcome and the measurements consistent with it. A result that
        asserts a detection always carries a detection offset, an SNR and a
        Doppler series; one that does not carries none of them.

    Note:
        ``not_attempted`` is never returned. It means a station took the work
        and then failed to start — an operational failure, not something the sky
        does — so it belongs to the fault schedule
        (:mod:`~meridian_sim.faults`) and would be a lie coming from here.
    """
    stream = _draw_stream(seed)

    if stream.random() < ABORT_PROBABILITY:
        return SimulatedOutcome(
            outcome="aborted",
            detection_offset_s=None,
            peak_snr_db=None,
            doppler_offsets_hz=None,
        )

    if stream.random() >= _ramp(
        expected_max_elevation_deg, SIGNAL_FLOOR_DEG, SIGNAL_CEILING_DEG
    ):
        return SimulatedOutcome(
            outcome="no_signal",
            detection_offset_s=None,
            peak_snr_db=None,
            doppler_offsets_hz=None,
        )

    return _heard_something(stream, expected_max_elevation_deg)


def _heard_something(
    stream: random.Random, expected_max_elevation_deg: float
) -> SimulatedOutcome:
    """Fill in a pass the station did hear, decoded or not.

    The draws happen in a fixed order whether or not the decode succeeds, so
    adding a measurement later cannot silently shift every earlier one — a
    stream consumed conditionally is a stream whose history depends on its own
    results.
    """
    decodes = stream.random() < _ramp(
        expected_max_elevation_deg, DECODE_FLOOR_DEG, DECODE_CEILING_DEG
    )
    clarity = _ramp(expected_max_elevation_deg, SIGNAL_FLOOR_DEG, SIGNAL_CEILING_DEG)

    return SimulatedOutcome(
        outcome="decoded" if decodes else "signal_no_decode",
        detection_offset_s=round(
            stream.uniform(
                DETECTION_OFFSET_FLOOR_S,
                _lerp(DETECTION_OFFSET_CEILING_S, DETECTION_OFFSET_FLOOR_S, clarity),
            ),
            1,
        ),
        peak_snr_db=round(
            _lerp(PEAK_SNR_FLOOR_DB, PEAK_SNR_CEILING_DB, clarity)
            + stream.uniform(-1.0, 1.0),
            1,
        ),
        doppler_offsets_hz=_doppler_offsets_hz(stream),
    )


def _doppler_offsets_hz(stream: random.Random) -> tuple[int, ...]:
    """A Doppler series across one pass: approaching, overhead, receding.

    A cosine from ``+MAX`` through zero at culmination to ``−MAX``, which is the
    right shape for a satellite closing and then opening range. **It is a shape,
    not a computation** — nothing here knows the orbit, and the platform's own
    ``orbit.doppler`` is what computes the real thing from a range rate.
    """
    span = DOPPLER_SAMPLE_COUNT - 1
    return tuple(
        round(
            MAX_DOPPLER_OFFSET_HZ * math.cos(math.pi * index / span)
            + stream.uniform(-DOPPLER_JITTER_HZ, DOPPLER_JITTER_HZ)
        )
        for index in range(DOPPLER_SAMPLE_COUNT)
    )


def _ramp(value: float, floor: float, ceiling: float) -> float:
    """``value`` mapped onto 0 below ``floor`` and 1 above ``ceiling``.

    Linear between them. A straight line rather than a fitted curve because
    nothing has been measured to fit one to, and a sigmoid here would look like
    a model of something.
    """
    if value <= floor:
        return 0.0
    if value >= ceiling:
        return 1.0
    return (value - floor) / (ceiling - floor)


def _lerp(low: float, high: float, fraction: float) -> float:
    """``fraction`` of the way from ``low`` to ``high``."""
    return low + (high - low) * fraction


def _draw_stream(seed: int) -> random.Random:
    """The sequence of draws this pass gets.

    Mersenne Twister, not ``secrets``: this decides a fixture, not a credential,
    and the property wanted is exactly the one a cryptographic source refuses to
    give — the same seed reproducing the same pass on every run.
    """
    return random.Random(seed)
