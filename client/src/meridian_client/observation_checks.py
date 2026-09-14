"""Refusing, before it is queued, an observation the platform would refuse.

A body the platform refuses as ``malformed`` is refused permanently: the station
sets it aside and the pass is gone. So every rule the platform applies to a body
on its own — MSP §4.4's enum, D-072's agreement between outcome and signal,
D-117's rules for the reception evidence, the caps in §6 — is applied here too,
where a pipeline bug surfaces as an exception at build time rather than as a
lost observation discovered a day later.

Split from :mod:`meridian_client.observation_message`, which builds the body
these rules guard, once 0.3's rules would have taken that module past a length
anyone reads in one sitting. The constants live here because the checks need
them; the message module re-exports them under their existing names.

The rule that needs the platform's clock — D-013's window on ``started_at`` — is
the platform's alone, and the queue's thirty-day set-aside mirrors it (D-074).

Reference: docs/MSP-SPEC.md §4.4, §6; docs/DECISIONS.md D-032, D-072, D-117.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import TYPE_CHECKING

from meridian_client.clock import require_utc

if TYPE_CHECKING:
    from meridian_client.observation_message import (
        Decode,
        ObservationResult,
        Signal,
    )

__all__ = [
    "DETECTION_FORBIDDEN",
    "DETECTION_REQUIRED",
    "MAX_DOPPLER_SAMPLES",
    "MAX_SNR_SAMPLES",
    "OUTCOMES",
    "check_the_result_is_sendable",
]

OUTCOMES = (
    "decoded",
    "signal_no_decode",
    "no_signal",
    "aborted",
    "not_attempted",
)
"""MSP §4.4's five values for ``outcome``, in the specification's order.

Written out rather than passed through, so a value the platform would reject
cannot leave this client — and cannot sit in the upload queue being retried
against an endpoint that will refuse it every time.
"""

DETECTION_REQUIRED = ("decoded", "signal_no_decode")
"""Outcomes that assert a signal was there, and so need ``signal.detected`` (D-072)."""

DETECTION_FORBIDDEN = ("no_signal", "not_attempted")
"""Outcomes that assert none was, and so cannot carry a detection (D-072)."""

ZERO_FRAME_OUTCOMES = ("signal_no_decode", "no_signal")
"""Outcomes that assert nothing was decoded, so a counted frame contradicts them."""

MAX_DOPPLER_SAMPLES = 512
"""MSP §6's cap (D-032).

Checked here as well as at the platform because a body over the cap is refused
permanently: queueing one would mean a station holding a payload it can never
deliver, and discovering that only after the pass is long gone.
"""

MAX_SNR_SAMPLES = 512
"""MSP §6's cap on ``snr_samples``, the same as Doppler's and for the same reason."""


def check_the_result_is_sendable(result: ObservationResult) -> None:
    """Raise if the platform would refuse the body built from ``result``.

    Args:
        result: What an executor produced for one assignment.

    Raises:
        ValueError: With the rule that failed. The window is checked *after*
            both instants are known to be aware, because comparing a naive
            datetime with an aware one raises ``TypeError`` — which would reach a
            caller as "this program has a bug" rather than as "that timestamp
            cannot be sent", and those are answered differently.
    """
    if result.outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}, not {result.outcome!r}")

    require_utc(result.started_at, "started_at")
    require_utc(result.ended_at, "ended_at")
    if result.started_at > result.ended_at:
        raise ValueError("started_at is after ended_at")

    if result.signal is not None:
        _check_the_signal(result.signal)
    if result.decode is not None:
        _check_the_decode(result.decode)
    _check_the_outcome_agrees(result)
    _check_the_products_are_strict_json(result.products)


def _check_the_signal(signal: Signal) -> None:
    """The ``signal`` block on its own: detection, caps, finiteness, floor and gain."""
    if signal.detected != (signal.first_detection_at is not None):
        raise ValueError("signal.detected and signal.first_detection_at must agree")
    for name, value in (
        ("peak_snr_db", signal.peak_snr_db),
        ("noise_floor_dbfs", signal.noise_floor_dbfs),
        ("receiver_gain_db", signal.receiver_gain_db),
    ):
        _require_finite(value, name)
    if signal.noise_floor_dbfs is not None and signal.receiver_gain_db is None:
        raise ValueError("signal.noise_floor_dbfs requires signal.receiver_gain_db")

    if signal.doppler_samples is not None and (
        len(signal.doppler_samples) > MAX_DOPPLER_SAMPLES
    ):
        raise ValueError(f"more than {MAX_DOPPLER_SAMPLES} doppler samples")
    if signal.snr_samples is not None:
        if len(signal.snr_samples) > MAX_SNR_SAMPLES:
            raise ValueError(f"more than {MAX_SNR_SAMPLES} snr samples")
        for sample in signal.snr_samples:
            _require_finite(sample.snr_db, "snr sample snr_db")


def _check_the_decode(decode: Decode) -> None:
    """The ``decode`` block alone: a named decoder, and whole non-negative counts."""
    if not decode.decoder:
        raise ValueError("decode.decoder must name the decoder")
    for name, count in (
        ("frames_decoded", decode.frames_decoded),
        ("frames_failed", decode.frames_failed),
    ):
        if count is None:
            continue
        # bool is a subclass of int, and True would otherwise count as one frame.
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError(f"decode.{name} must be a whole number of frames")


def _check_the_outcome_agrees(result: ObservationResult) -> None:
    """D-072's outcome against detection, and D-117's outcome against evidence."""
    signal = result.signal
    detected = signal is not None and signal.detected
    if result.outcome in DETECTION_REQUIRED and not detected:
        raise ValueError(f"outcome {result.outcome} requires signal.detected")
    if result.outcome in DETECTION_FORBIDDEN and detected:
        raise ValueError(f"outcome {result.outcome} cannot carry a detection")

    if result.outcome == "not_attempted" and (
        result.decode is not None or (signal is not None and _carries_evidence(signal))
    ):
        raise ValueError("outcome not_attempted carries no reception evidence")

    frames = None if result.decode is None else result.decode.frames_decoded
    if frames is None:
        return
    if result.outcome == "decoded" and frames < 1:
        raise ValueError("outcome decoded requires decode.frames_decoded >= 1")
    if result.outcome in ZERO_FRAME_OUTCOMES and frames != 0:
        raise ValueError(
            f"outcome {result.outcome} requires decode.frames_decoded == 0"
        )


def _carries_evidence(signal: Signal) -> bool:
    """Whether any of MSP 0.3's three signal fields is present."""
    return (
        signal.noise_floor_dbfs is not None
        or signal.receiver_gain_db is not None
        or signal.snr_samples is not None
    )


def _require_finite(value: float | None, name: str) -> None:
    """JSON has no literal for ``NaN`` or infinity, and the platform refuses both."""
    if value is not None and not math.isfinite(value):
        raise ValueError(f"{name} is not a finite number: {value!r}")


def _check_the_products_are_strict_json(
    products: tuple[Mapping[str, object], ...],
) -> None:
    """Refuse a ``products`` array the platform could not store.

    ``products`` is deliberately unvalidated in shape, but a non-finite float
    inside it has no JSON literal — and one arrives without anyone writing it,
    since a decoder metric of ``inf`` renders as the token ``Infinity``. The
    platform refuses it, so queueing it would mean a station holding a payload
    it can never deliver and retrying it against every tick.
    """
    try:
        json.dumps([dict(one) for one in products], allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"products is not strict JSON: {exc}") from exc
