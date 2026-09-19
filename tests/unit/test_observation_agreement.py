"""Every observation body the client builds is one the platform accepts.

The client refuses in advance what the platform would refuse, in its own words,
in its own module. That duplication only protects anything if the two sets of
rules agree, so these bodies are built with the client's function and validated
with the platform's request model — the same model the endpoint parses with.
Tests may import both distributions; the distributions themselves never import
each other (tests/unit/test_import_boundaries.py).

Marked as a unit test by living in ``tests/unit``: validation is pure, and no
database or network is involved.

Reference: docs/MSP-SPEC.md §4.4; docs/DECISIONS.md D-072, D-117.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from meridian.api.models.observation import ObservationRequestBody
from meridian_client.observation_message import (
    Decode,
    DopplerSample,
    ObservationResult,
    Signal,
    SnrSample,
    build_observation_body,
)

STARTED_AT = datetime(2026, 8, 14, 9, 41, 18, tzinfo=UTC)
ENDED_AT = datetime(2026, 8, 14, 9, 52, 10, tzinfo=UTC)
DETECTED_AT = datetime(2026, 8, 14, 9, 41, 53, tzinfo=UTC)
LATER = datetime(2026, 8, 14, 9, 46, 44, tzinfo=UTC)


def result(outcome: str, **fields: object) -> ObservationResult:
    return ObservationResult(
        "as_44b2",
        STARTED_AT,
        ENDED_AT,
        outcome,
        **fields,  # type: ignore[arg-type]
    )


DETECTED = Signal(detected=True, first_detection_at=DETECTED_AT, peak_snr_db=11.4)

RESULTS = {
    "0.2 decoded": result(
        "decoded",
        signal=Signal(
            detected=True,
            first_detection_at=DETECTED_AT,
            peak_snr_db=11.4,
            doppler_samples=(DopplerSample(DETECTED_AT, 3140),),
        ),
    ),
    "0.3 decoded with every field": result(
        "decoded",
        signal=Signal(
            detected=True,
            first_detection_at=DETECTED_AT,
            peak_snr_db=11.4,
            noise_floor_dbfs=-52.3,
            receiver_gain_db=32.8,
            snr_samples=(SnrSample(DETECTED_AT, 3.1), SnrSample(LATER, 11.4)),
        ),
        decode=Decode("satdump", "1.2.2", 412, 37),
    ),
    "signal_no_decode with zero frames": result(
        "signal_no_decode", signal=DETECTED, decode=Decode("satdump", frames_decoded=0)
    ),
    "no_signal with its noise floor": result(
        "no_signal",
        signal=Signal(
            detected=False,
            noise_floor_dbfs=-55.0,
            receiver_gain_db=32.8,
            snr_samples=(),
        ),
        decode=Decode("satdump", frames_decoded=0, frames_failed=0),
    ),
    "aborted with frames and no timing": result(
        "aborted", decode=Decode("satdump", frames_decoded=120)
    ),
    "not_attempted, bare": result("not_attempted"),
    "a decoder with no frame structure": result(
        "decoded", signal=DETECTED, decode=Decode("gr-satellites")
    ),
}


@pytest.mark.parametrize("name", list(RESULTS))
def test_the_platform_accepts_what_the_client_builds(name: str) -> None:
    body = build_observation_body(RESULTS[name], "st_7fa3c1")

    parsed = ObservationRequestBody.model_validate(body)

    assert parsed.outcome == RESULTS[name].outcome
    assert (parsed.decode is None) == (RESULTS[name].decode is None)
