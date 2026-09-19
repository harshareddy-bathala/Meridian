"""Building MSP §4.4's observation, and reading the acknowledgement.

What a station says after every attempt — including the attempts that produced
nothing, because a station that listened and heard nothing has measured
something and a station that never began has not. The two are different
``outcome`` values and the platform must never conflate them.

Pure. The instants and the result arrive as arguments, so the body a station
would send is checkable without a receiver, a clock, a file or a network.

The body this builds is also the format the upload queue stores, so a queued
observation and a sent one are the same bytes and the same code path (D-068).

What the platform would refuse is refused first, in
:mod:`meridian_client.observation_checks`, so a pipeline bug surfaces when the
body is built rather than as an observation the platform turns away for good.

MSP 0.3 added optional reception evidence — the noise floor with its gain, SNR
samples, and the decoder's own statistics. Like every optional field here, what
was not measured is omitted from the body, never sent as zero (D-117).

Reference: docs/MSP-SPEC.md §4.4, §6; docs/DECISIONS.md D-027, D-032, D-068,
D-103, D-117.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from meridian_client.clock import require_utc
from meridian_client.observation_checks import (
    MAX_DOPPLER_SAMPLES,
    MAX_SNR_SAMPLES,
    OUTCOMES,
    check_the_result_is_sendable,
)

__all__ = [
    "MAX_DOPPLER_SAMPLES",
    "MAX_SNR_SAMPLES",
    "OUTCOMES",
    "Decode",
    "DopplerSample",
    "MalformedAcknowledgementError",
    "ObservationAck",
    "ObservationResult",
    "Signal",
    "SnrSample",
    "build_observation_body",
    "parse_observation_ack",
]

OBSERVATION_ID = re.compile(r"^ob_[0-9a-f]{12}$")
"""MSP §4.4: ``ob_`` followed by twelve hexadecimal characters.

Checked for shape, never parsed for meaning. The specification calls the id
opaque, so a station that read the assignment or the revision out of it would be
depending on a format the platform is free to change.
"""


@dataclass(frozen=True, slots=True)
class DopplerSample:
    """One measured frequency offset, at the instant it was measured."""

    sampled_at: datetime
    offset_hz: int
    """Observed minus nominal, in whole hertz. Frequencies are never floats."""


@dataclass(frozen=True, slots=True)
class SnrSample:
    """One signal-to-noise measurement, at the instant it was measured (MSP 0.3)."""

    sampled_at: datetime
    snr_db: float


@dataclass(frozen=True, slots=True)
class Decode:
    """MSP 0.3's ``decode`` block — the decoder's own account of its run.

    Only ``decoder`` is required. A decoder with no frame structure has no frames
    to count, and a zero would claim it counted and found nothing.
    """

    decoder: str
    decoder_version: str | None = None
    frames_decoded: int | None = None
    frames_failed: int | None = None


@dataclass(frozen=True, slots=True)
class Signal:
    """MSP §4.4's ``signal`` block — what the receiver heard, if anything."""

    detected: bool

    first_detection_at: datetime | None = None
    """Present exactly when ``detected``, and the reason the block matters.

    Its difference from the predicted acquisition time is what makes pass-timing
    error measurable, which is this project's primary measurement of orbital
    data quality.
    """

    peak_snr_db: float | None = None
    doppler_samples: tuple[DopplerSample, ...] | None = None
    """``None`` is a station with no frequency reference; ``()`` is one that
    measured and found nothing worth reporting. Different claims, sent
    differently."""

    noise_floor_dbfs: float | None = None
    """MSP 0.3: relative to the receiver's full scale, and meaningful only with
    ``receiver_gain_db``, which the checks require whenever this is present."""

    receiver_gain_db: float | None = None
    snr_samples: tuple[SnrSample, ...] | None = None
    """MSP 0.3: raw samples in time order; ``None`` and ``()`` differ as for Doppler."""


@dataclass(frozen=True, slots=True)
class ObservationResult:
    """What an executor produces for one assignment.

    Carries no ``station_id``: an executor drives a radio and has no business
    knowing the station's identity on the network. The loop adds it when it
    builds the body, from the credentials it already holds.
    """

    assignment_id: str
    started_at: datetime
    ended_at: datetime
    outcome: str

    signal: Signal | None = None
    """Absent means nothing was heard — the shape a ``not_attempted`` report
    takes, and the shape a station with no receiver would produce if it produced
    anything at all."""

    products: tuple[Mapping[str, object], ...] = ()
    """Metadata only — MSP 0.x defines no transfer mechanism, and a station with
    nowhere to put an artefact sends nothing here, which stays valid."""

    client_notes: str | None = None

    decode: Decode | None = None
    """MSP 0.3: absent when no decoder ran — and always absent for ``not_attempted``."""


def _wire_time(instant: datetime, field_name: str) -> str:
    """One instant in the form MSP puts on the wire.

    Raises:
        ValueError: The instant is naive or not UTC. A naive timestamp would be
            read by the platform as UTC, misreporting a station in another zone
            by whole hours — and ``started_at`` is the column the observation
            hypertable partitions on.
    """
    require_utc(instant, field_name)
    return instant.isoformat().replace("+00:00", "Z")


def _signal_block(signal: Signal) -> dict[str, object]:
    """MSP §4.4's ``signal`` block, omitting what was not measured."""
    block: dict[str, object] = {"detected": signal.detected}
    if signal.first_detection_at is not None:
        block["first_detection_at"] = _wire_time(
            signal.first_detection_at, "first_detection_at"
        )
    if signal.peak_snr_db is not None:
        block["peak_snr_db"] = signal.peak_snr_db
    if signal.doppler_samples is not None:
        block["doppler_samples"] = [
            {
                "t": _wire_time(one.sampled_at, "doppler sample t"),
                "offset_hz": one.offset_hz,
            }
            for one in signal.doppler_samples
        ]
    if signal.noise_floor_dbfs is not None:
        block["noise_floor_dbfs"] = signal.noise_floor_dbfs
    if signal.receiver_gain_db is not None:
        block["receiver_gain_db"] = signal.receiver_gain_db
    if signal.snr_samples is not None:
        block["snr_samples"] = [
            {"t": _wire_time(one.sampled_at, "snr sample t"), "snr_db": one.snr_db}
            for one in signal.snr_samples
        ]
    return block


def _decode_block(decode: Decode) -> dict[str, object]:
    """MSP 0.3's ``decode`` block, omitting what the decoder did not report."""
    block: dict[str, object] = {"decoder": decode.decoder}
    if decode.decoder_version is not None:
        block["decoder_version"] = decode.decoder_version
    if decode.frames_decoded is not None:
        block["frames_decoded"] = decode.frames_decoded
    if decode.frames_failed is not None:
        block["frames_failed"] = decode.frames_failed
    return block


def build_observation_body(
    result: ObservationResult, station_id: str
) -> dict[str, object]:
    """MSP §4.4's request body, as a JSON-serialisable object.

    Args:
        result: What the executor produced for one assignment.
        station_id: This station's identity, from its stored credentials.

    Returns:
        The body. Separate from sending it so the exact wire shape can be
        asserted against the specification with no platform involved — and so
        the upload queue can store it unchanged.

    Raises:
        ValueError: The body breaks a rule the platform would refuse it for —
            see :func:`meridian_client.observation_checks.check_the_result_is_sendable`.
            Made here so a station never queues work it can never deliver.

    Note:
        Optional fields are **omitted rather than sent as ``null``**, matching
        how the heartbeat treats an unmeasured clock. The platform reads the two
        identically, so this is a choice about bytes on a link a microcontroller
        may be sharing rather than about meaning.
    """
    check_the_result_is_sendable(result)

    body: dict[str, object] = {
        "assignment_id": result.assignment_id,
        "station_id": station_id,
        "started_at": _wire_time(result.started_at, "started_at"),
        "ended_at": _wire_time(result.ended_at, "ended_at"),
        "outcome": result.outcome,
    }
    if result.signal is not None:
        body["signal"] = _signal_block(result.signal)
    if result.products:
        body["products"] = [dict(one) for one in result.products]
    if result.client_notes is not None:
        body["client_notes"] = result.client_notes
    if result.decode is not None:
        body["decode"] = _decode_block(result.decode)
    return body


class MalformedAcknowledgementError(Exception):
    """The platform answered with something MSP §4.4 does not define.

    Distinct from a transport failure and from an MSP error code: those say the
    submission did not land, and this says the station cannot tell whether it
    did. The observation stays queued, because the only safe reading of an
    unintelligible answer is that the work is not yet done — and a resubmission
    is free, since the platform is idempotent on the assignment (D-015).
    """


@dataclass(frozen=True, slots=True)
class ObservationAck:
    """MSP §4.4's acknowledgement."""

    observation_id: str
    assignment_id: str

    superseded: bool
    """``True`` when the platform already held an observation for this
    assignment and this submission replaced it as the current one. A station can
    log the difference; a constrained one may ignore the field entirely."""


def parse_observation_ack(
    body: Mapping[str, object], expected_assignment_id: str
) -> ObservationAck:
    """Read an acknowledgement, and prove it answers the submission that was sent.

    Args:
        body: The decoded JSON response.
        expected_assignment_id: The assignment this station just reported on.

    Returns:
        The acknowledgement.

    Raises:
        MalformedAcknowledgementError: A field is missing or the wrong type, the
            ``observation_id`` is not MSP §4.4's shape, or the acknowledgement
            names a different assignment.

    Note:
        **The assignment is checked, not assumed.** An acknowledgement for
        another assignment would otherwise cause the station to drop the wrong
        item from its queue — losing one observation and retrying another
        forever. It costs one comparison, and the alternative failure is silent.
    """
    observation_id = body.get("observation_id")
    assignment_id = body.get("assignment_id")
    superseded = body.get("superseded")

    if not isinstance(observation_id, str) or not OBSERVATION_ID.match(observation_id):
        raise MalformedAcknowledgementError(
            f"observation_id is not ob_ and twelve hex characters: {observation_id!r}"
        )
    if assignment_id != expected_assignment_id:
        raise MalformedAcknowledgementError(
            f"acknowledgement names {assignment_id!r}, not {expected_assignment_id!r}"
        )
    if not isinstance(superseded, bool):
        raise MalformedAcknowledgementError(
            f"superseded is not a boolean: {superseded!r}"
        )

    return ObservationAck(
        observation_id=observation_id,
        assignment_id=expected_assignment_id,
        superseded=superseded,
    )
