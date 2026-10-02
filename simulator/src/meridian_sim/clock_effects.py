"""What a stepped clock does to one pass's recording, and so to its outcome.

The effect specified in ``docs/SCALE-AND-FAULTS.md`` § A stepped clock, as code
(D-277). A station whose clock is ``step`` seconds ahead begins that much early
and stops that much early, so the sample it labels *i* was taken at the instant
of the clean sample ``i − k``, where ``k`` is the step in samples. What falls
outside the satellite's window is noise. The floor does not move, and the
outcome is derived again from what is left by the frame count every pass uses,
exactly as a sky fault's is (:mod:`~meridian_sim.sky_effects`).

**Every pass begun inside the window is named, heard or not**, because the
station recorded the wrong stretch of time whatever was in it. Only a heard
pass's evidence changes: noise moved is noise. An aborted pass measured nothing,
and one never attempted never reaches here.

Reference: docs/DECISIONS.md D-251, D-270, D-277; docs/SCALE-AND-FAULTS.md
§ A stepped clock.
"""

from __future__ import annotations

import random

from meridian_sim.evidence import NOISE_ONLY_SNR_DB, PassEvidence
from meridian_sim.outcomes import SimulatedOutcome
from meridian_sim.sky_effects import (  # the one rule an outcome follows, shared
    HEARD,
    PassContext,
    _rederive,
    _Working,
)

__all__ = ["names_pass", "samples_moved", "shift_recording"]


def samples_moved(step_s: float, window_s: float, count: int) -> int:
    """How many samples a step of ``step_s`` moves a recording of ``count``.

    Signed like the step: positive for a clock ahead.
    """
    spacing = window_s / max(count - 1, 1)
    return round(step_s / spacing)


def names_pass(outcome: SimulatedOutcome, moved: int) -> bool:
    """Whether a pass with this clean outcome is named against a step.

    Decided when the pass begins, from its clean outcome, so the ledger can name
    it while the fault's window is certainly open.
    """
    return moved != 0 and outcome.outcome != "aborted"


def shift_recording(
    outcome: SimulatedOutcome,
    evidence: PassEvidence | None,
    step_s: float,
    context: PassContext,
) -> tuple[SimulatedOutcome, PassEvidence | None]:
    """The pass as a station whose clock was ``step_s`` off recorded it.

    Args:
        outcome: What the pass was, before the clock.
        evidence: What it measured; ``None`` for an aborted pass.
        step_s: The clock's error when the pass began, positive for ahead.
        context: The pass.

    Returns:
        The outcome and evidence the moved recording supports, or both
        unchanged for a pass the step does not touch.
    """
    if evidence is None:
        return outcome, evidence
    count = len(evidence.snr_db)
    moved = samples_moved(step_s, context.window_s, count)
    if moved == 0 or outcome.outcome not in HEARD:
        return outcome, evidence
    stream = random.Random(f"{context.pass_seed}:clock-noise")
    noise = [
        round(stream.uniform(-NOISE_ONLY_SNR_DB, NOISE_ONLY_SNR_DB), 1)
        for _ in range(count)
    ]
    snr = [
        evidence.snr_db[i - moved] if 0 <= i - moved < count else noise[i]
        for i in range(count)
    ]
    work = _Working(
        context=context,
        snr=snr,
        floors=[evidence.noise_floor_dbfs] * count,
        noise=noise,
        heard=True,
    )
    return _rederive(outcome, evidence, work)
