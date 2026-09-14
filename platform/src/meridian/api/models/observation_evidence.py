"""MSP 0.3's reception evidence: the SNR sample and ``decode`` blocks, and their rules.

Split from :mod:`meridian.api.models.observation` because the evidence has rules
of its own that read across the body — a frame count against the outcome, a noise
floor against its gain — and the observation model was already the longest
request model in the package. The rules are plain functions over plain values so
the model can call them from its validators and a test can call them directly.

Every rule constrains only a field added in 0.3, so no body a 0.2 station sends
is refused by anything here (D-117).

Reference: docs/MSP-SPEC.md §4.4, §6; docs/DECISIONS.md D-032, D-072, D-103, D-117.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import AwareDatetime, BaseModel, Field, StrictInt

from meridian.store.observations import DecodeStatistics, SnrSample

__all__ = [
    "MAX_SNR_SAMPLES",
    "DecodeBlock",
    "SnrSampleBlock",
    "check_evidence_agrees_with_the_outcome",
]

MAX_SNR_SAMPLES = 512
"""MSP §6's cap, the same as ``doppler_samples`` and for the same reason (D-032).

One sample every 1.75 seconds across a fifteen-minute pass. Two full arrays are
about 50 KiB, well inside the 256 KiB observation body (D-103).
"""

FrameCount = Annotated[StrictInt, Field(ge=0)]
"""Strict, so ``true`` is not read as one frame and ``3.0`` is not read as three."""

ZERO_FRAME_OUTCOMES = ("signal_no_decode", "no_signal")
"""Outcomes asserting that nothing was decoded. A counted frame contradicts both."""


class SnrSampleBlock(BaseModel):
    """One entry of MSP §4.4's ``snr_samples`` array."""

    t: AwareDatetime
    snr_db: float = Field(allow_inf_nan=False)

    def to_snr_sample(self) -> SnrSample:
        """The store layer's shape for this sample."""
        return SnrSample(sampled_at=self.t, snr_db=self.snr_db)


class DecodeBlock(BaseModel):
    """MSP §4.4's ``decode`` block — the decoder's own account of its run."""

    decoder: str = Field(min_length=1)
    """Required whenever the block is present: statistics from an unnamed decoder
    cannot be segmented by decoder or version (EVALUATION.md §11.1)."""

    decoder_version: str | None = None
    frames_decoded: FrameCount | None = None
    """Optional, because a decoder with no frame structure has none to count, and
    a zero would claim that it counted and found nothing."""

    frames_failed: FrameCount | None = None

    def to_statistics(self) -> DecodeStatistics:
        """The store layer's shape for this block."""
        return DecodeStatistics(
            decoder=self.decoder,
            decoder_version=self.decoder_version,
            frames_decoded=self.frames_decoded,
            frames_failed=self.frames_failed,
        )


def check_evidence_agrees_with_the_outcome(
    outcome: str,
    decode: DecodeBlock | None,
    *,
    signal_evidence_present: bool,
) -> None:
    """D-117's two cross-field rules, raising on a body that contradicts itself.

    Args:
        outcome: One of MSP §4.4's five values, already validated.
        decode: The body's ``decode`` block, if any.
        signal_evidence_present: Whether ``signal`` carries any of 0.3's three
            fields — a noise floor, a gain or SNR samples.

    Raises:
        ValueError: A ``not_attempted`` report carries evidence, or a counted
            frame total contradicts the outcome. ``aborted`` accepts any count,
            because a decode can stop part-way.
    """
    if outcome == "not_attempted" and (decode is not None or signal_evidence_present):
        raise ValueError("outcome not_attempted carries no reception evidence")

    frames = None if decode is None else decode.frames_decoded
    if frames is None:
        return
    if outcome == "decoded" and frames < 1:
        raise ValueError("outcome decoded requires decode.frames_decoded >= 1")
    if outcome in ZERO_FRAME_OUTCOMES and frames != 0:
        raise ValueError(f"outcome {outcome} requires decode.frames_decoded == 0")
