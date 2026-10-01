"""What the reception verdict reads, as numbers, and the hash that names it.

The verdict is a calibrated probability that one observation revision is
usable (Stage 26). Its inputs are what the station reported and what the
platform knew about that reception:
- the outcome and whether a signal was detected;
- the peak SNR;
- frames decoded against frames expected (D-250), where both exist;
- the decoder's name and version;
- whether the registry confirmed the station was listening (D-145);
- the data type, image or telemetry, from the assignment's mode.

**Standard library only.** The writer of Stage 26's verdicts runs in the image,
where the ``fit`` extra is not, and builds these from the database. The
snapshot reader (:mod:`meridian.prediction.verdict_rows`) builds them from a
raw snapshot. Both reach the same values, so the same reception has the same
:func:`inputs_sha256` whichever side computed it.

**A missing input takes a route, never a zero** (D-261). A reception without a
peak SNR, or without decoder statistics, is not one with an SNR of 0 dB or no
frames. It is scored by a model fitted without that input:
- ``full`` reads the SNR and the frames ratio;
- ``snr`` reads the SNR and no decoder statistics;
- ``outcome`` reads neither.

A frames ratio without an SNR goes to ``outcome``. No station reports that
combination, and a fourth model for it would be fitted on nothing.

**What is a flag, not a measurement.** ``listening_confirmed`` is 1 when the
registry confirmed listening and 0 otherwise, including when it was never
asked. That is whether confirmation exists, which is a fact about the record,
not an imputed reading. ``image`` is whether the mode carries an image.

**Decoder name and version are inputs but not features** (D-261). They are in
the hash, and the calibration report segments by them (EVALUATION.md §11.1).
A coefficient per decoder release would be fitted from a handful of rated
receptions, and a new release would have none.

Reference: docs/DECISIONS.md D-070, D-103, D-145, D-250, D-260, D-261.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

__all__ = [
    "FEATURES",
    "FULL",
    "IMAGE_MODES",
    "OUTCOME",
    "OUTCOME_FEATURES",
    "ROUTES",
    "SNR",
    "ReceptionInputs",
    "feature_values",
    "inputs_sha256",
    "route_of",
]

FULL = "full"
SNR = "snr"
OUTCOME = "outcome"
ROUTES = (FULL, SNR, OUTCOME)
"""Every route, most evidence first."""

OUTCOME_FEATURES = ("decoded", "signal_no_decode", "no_signal", "aborted")
"""One indicator per outcome. ``not_attempted`` is the base, with all four 0."""

_BASE = (*OUTCOME_FEATURES, "signal_detected", "listening_confirmed", "image")

FEATURES: dict[str, tuple[str, ...]] = {
    OUTCOME: _BASE,
    SNR: (*_BASE, "peak_snr_db"),
    FULL: (*_BASE, "peak_snr_db", "frames_ratio"),
}
"""The features each route's model reads, in the order it stores them."""

IMAGE_MODES = frozenset({"lrpt", "apt", "hrpt"})
"""Modes whose product is an image; every other mode carries telemetry."""

INPUTS_VERSION = "verdict-inputs-1"
"""Hashed with the inputs, so a change to what they mean changes every hash."""


@dataclass(frozen=True, slots=True)
class ReceptionInputs:
    """Everything one verdict reads, and nothing else."""

    outcome: str
    signal_detected: bool
    peak_snr_db: float | None
    frames_decoded: int | None
    frames_expected: int | None
    decoder: str | None
    decoder_version: str | None
    listening_confirmed: bool
    mode: str

    @property
    def frames_ratio(self) -> float | None:
        """Frames decoded over frames expected, where both exist and expected > 0."""
        if self.frames_decoded is None or not self.frames_expected:
            return None
        return self.frames_decoded / self.frames_expected

    @property
    def data_type(self) -> str:
        """``image`` or ``telemetry``."""
        return "image" if self.mode in IMAGE_MODES else "telemetry"

    @property
    def has_decoder_statistics(self) -> bool:
        """Whether the station reported its decoder's frame counts (MSP 0.3)."""
        return self.frames_decoded is not None


def route_of(inputs: ReceptionInputs) -> str:
    """The route a reception is scored by: the most evidence it has."""
    if inputs.peak_snr_db is None:
        return OUTCOME
    if inputs.frames_ratio is None:
        return SNR
    return FULL


def feature_values(inputs: ReceptionInputs) -> dict[str, float]:
    """The reception's features, by name: every one its route reads.

    Returns:
        The route's features. A feature the route does not read is absent,
        never zero.
    """
    values = {name: float(inputs.outcome == name) for name in OUTCOME_FEATURES}
    values |= {
        "signal_detected": float(inputs.signal_detected),
        "listening_confirmed": float(inputs.listening_confirmed),
        "image": float(inputs.data_type == "image"),
    }
    if inputs.peak_snr_db is not None:
        values["peak_snr_db"] = inputs.peak_snr_db
    ratio = inputs.frames_ratio
    if ratio is not None and inputs.peak_snr_db is not None:
        values["frames_ratio"] = ratio
    return values


def inputs_sha256(inputs: ReceptionInputs) -> bytes:
    """The SHA-256 of the inputs' canonical form, with :data:`INPUTS_VERSION`.

    Sorted keys and no whitespace, as D-070's canonical body. A float keeps
    Python's shortest round-trip form, which a value read back from JSON or
    from a ``double precision`` column reproduces exactly.
    """
    document = {
        "version": INPUTS_VERSION,
        "outcome": inputs.outcome,
        "signal_detected": inputs.signal_detected,
        "peak_snr_db": inputs.peak_snr_db,
        "frames_decoded": inputs.frames_decoded,
        "frames_expected": inputs.frames_expected,
        "decoder": inputs.decoder,
        "decoder_version": inputs.decoder_version,
        "listening_confirmed": inputs.listening_confirmed,
        "mode": inputs.mode,
    }
    canonical = json.dumps(
        document, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).digest()
