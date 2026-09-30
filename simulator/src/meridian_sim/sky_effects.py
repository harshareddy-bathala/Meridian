"""What Stage 25's four faults do to one pass's evidence, and so to its outcome.

The effects specified in ``docs/SCALE-AND-FAULTS.md`` § Ground-truth faults, as
code. A fault acts on the evidence :mod:`~meridian_sim.evidence` drew for the
pass — the SNR series and the noise floor — and the outcome is then derived
again from what is left, by the same frame count the evidence uses. So a fault
can only lose a pass by taking away the signal it was decoded from, which is the
shape of evidence Stage 27's diagnosis will have to recognise.

Per sample, in this order:

``signal_degradation``
    Every sample of a heard pass loses the fault's loss at the pass's start —
    its rate times the days since onset. The floor does not move.
``obstruction``
    A sample whose direction is inside the sector and below the elevation hears
    nothing: its SNR is replaced by noise. The floor does not move.
``interference``
    A sample inside the sector and the hours has its floor raised by the rise,
    and a heard sample loses that much SNR. The pass's floor is the mean power
    of its samples' floors, so it rises with the share of the pass affected.
``satellite_silent``
    For the named satellite, every sample is noise. The floor does not move.

**A pass is touched only if a fault changed something about it.** Ground truth
is what a fault *did*, and a degradation that had accrued no loss yet, or an
obstruction the pass never crossed, did nothing to that pass.

Reference: docs/DECISIONS.md D-105, D-251, D-253; docs/SCALE-AND-FAULTS.md
§ Ground-truth faults.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from meridian_sim.evidence import (
    DETECT_SNR_DB,
    NOISE_ONLY_SNR_DB,
    PassEvidence,
    count_frames,
    decodable_frames,
)
from meridian_sim.faults import SKY_FAULTS
from meridian_sim.outcomes import SimulatedOutcome
from meridian_sim.sky_faults import (
    ActiveSkyFault,
    Degradation,
    Interference,
    Obstruction,
    Silence,
)
from meridian_sim.sky_track import SkyPoint

__all__ = ["Affected", "PassContext", "apply_sky_faults"]

HEARD = frozenset({"decoded", "signal_no_decode"})


@dataclass(frozen=True, slots=True)
class PassContext:
    """What a fault needs to know about the pass it may act on."""

    pass_seed: int
    satellite_id: str
    instants: tuple[datetime, ...]
    """One per SNR sample, in order."""

    window_s: float
    track: Callable[[], tuple[SkyPoint, ...]]
    """The satellite's direction at each instant. Called only when a fault in a
    sector is in force, because it is the one expensive step here."""


@dataclass(frozen=True, slots=True)
class Affected:
    """A pass after its faults: its outcome, its evidence, and which acted."""

    outcome: SimulatedOutcome
    evidence: PassEvidence | None
    kinds: tuple[str, ...] = ()


@dataclass
class _Working:
    """The evidence while the faults are applied, one after another."""

    context: PassContext
    snr: list[float]
    floors: list[float]
    noise: list[float]
    """What each sample reads with nothing there: drawn once, in a fixed order."""

    heard: bool
    silenced: bool = False
    _points: tuple[SkyPoint, ...] | None = None

    def points(self) -> tuple[SkyPoint, ...]:
        """The pass's directions, computed the first time a fault asks."""
        if self._points is None:
            self._points = self.context.track()
        return self._points


def apply_sky_faults(
    outcome: SimulatedOutcome,
    evidence: PassEvidence | None,
    active: Sequence[ActiveSkyFault],
    context: PassContext,
) -> Affected:
    """Apply every sky fault in force to one pass.

    Args:
        outcome: What the pass would have been with nothing wrong.
        evidence: What it would have measured; ``None`` for an aborted pass,
            which measured nothing a fault could change.
        active: The sky faults in force when the pass began.
        context: The pass.

    Returns:
        The pass as the faults left it, and which of them changed it.
    """
    if evidence is None or not active:
        return Affected(outcome, evidence)
    count = len(evidence.snr_db)
    noise_stream = random.Random(f"{context.pass_seed}:sky-noise")
    work = _Working(
        context=context,
        snr=list(evidence.snr_db),
        floors=[evidence.noise_floor_dbfs] * count,
        noise=[
            round(noise_stream.uniform(-NOISE_ONLY_SNR_DB, NOISE_ONLY_SNR_DB), 1)
            for _ in range(count)
        ],
        heard=outcome.outcome in HEARD,
    )
    touched = [
        one.fault.kind
        for one in sorted(active, key=lambda a: SKY_FAULTS.index(a.fault.kind))
        if _act(one, work)
    ]
    if not touched:
        return Affected(outcome, evidence)
    changed, measured = _rederive(outcome, evidence, work)
    return Affected(changed, measured, tuple(touched))


def _act(one: ActiveSkyFault, work: _Working) -> bool:
    """Apply one fault to the working evidence; whether it changed anything."""
    shape = one.fault.shape
    if isinstance(shape, Degradation):
        return _degrade(shape.loss_db(one.onset_at, work.context.instants[0]), work)
    if isinstance(shape, Obstruction):
        return _obstruct(shape, work)
    if isinstance(shape, Interference):
        return _interfere(shape, work)
    return _silence(shape, work)


def _degrade(loss_db: float, work: _Working) -> bool:
    """Every sample of a heard pass weaker by the loss accrued at its start."""
    if loss_db <= 0 or not work.heard:
        return False
    work.snr = [value - loss_db for value in work.snr]
    return True


def _obstruct(shape: Obstruction, work: _Working) -> bool:
    """Samples from behind the obstruction hear nothing."""
    if not work.heard:
        return False
    blocked = [i for i, point in enumerate(work.points()) if shape.blocks(point)]
    for i in blocked:
        work.snr[i] = min(work.snr[i], work.noise[i])
    return bool(blocked)


def _interfere(shape: Interference, work: _Working) -> bool:
    """Samples in the sector and the hours: a higher floor, and less SNR."""
    instants = work.context.instants
    raised = [
        i for i, point in enumerate(work.points()) if shape.raises(point, instants[i])
    ]
    for i in raised:
        work.floors[i] += shape.rise_db
        if work.heard:
            work.snr[i] -= shape.rise_db
    return bool(raised)


def _silence(shape: Silence, work: _Working) -> bool:
    """Nothing transmitted: every sample is noise, whatever was heard before."""
    if shape.satellite_id != work.context.satellite_id:
        return False
    work.snr = list(work.noise)
    work.silenced = True
    return True


def _rederive(
    outcome: SimulatedOutcome, evidence: PassEvidence, work: _Working
) -> tuple[SimulatedOutcome, PassEvidence]:
    """The outcome and evidence that what is left of the signal supports.

    A decoded pass stays decoded while it still has decodable frames, or lost
    none of the ones it had; a heard pass stays heard while any sample clears
    the detection bar, or its peak lost nothing; otherwise it heard nothing.
    The second half of each rule is for the faintest passes, whose samples sit
    under the bars the outcome model's own draw cleared: a fault that took
    nothing from them does not lose them.

    First detection is re-read from the samples where one clears the bar,
    because the instant the station first heard the satellite is now wherever
    the surviving signal first did.
    """
    context = work.context
    snr = tuple(round(value, 1) for value in work.snr)
    power = sum(10 ** (f / 10) for f in work.floors) / len(work.floors)
    floor = 10 * math.log10(power)
    heard_after = not work.silenced and (
        max(snr) >= DETECT_SNR_DB or max(snr) >= max(evidence.snr_db)
    )
    before = decodable_frames(evidence.snr_db, context.window_s)
    after = decodable_frames(snr, context.window_s)

    if outcome.outcome == "decoded" and heard_after and (after >= 1 or after >= before):
        label = "decoded"
    elif outcome.outcome in HEARD and heard_after:
        label = "signal_no_decode"
    elif outcome.outcome in HEARD:
        label = "no_signal"
    else:
        label = outcome.outcome

    if label in HEARD:
        first = next((i for i, value in enumerate(snr) if value >= DETECT_SNR_DB), None)
        step = context.window_s / max(len(snr) - 1, 1)
        changed = replace(
            outcome,
            outcome=label,
            detection_offset_s=(
                outcome.detection_offset_s if first is None else round(first * step, 1)
            ),
            peak_snr_db=max(snr),
        )
    else:
        changed = SimulatedOutcome(
            outcome=label,
            detection_offset_s=None,
            peak_snr_db=None,
            doppler_offsets_hz=None,
        )
    decoded, failed = count_frames(snr, context.window_s, label)
    return changed, replace(
        evidence,
        snr_db=snr,
        noise_floor_dbfs=round(floor, 1),
        frames_decoded=decoded,
        frames_failed=failed,
    )
