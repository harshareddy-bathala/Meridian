"""``verdict_score`` — a strict reader, and a score by the reception's route.

Reference: docs/DECISIONS.md D-261.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from meridian.prediction.score import MalformedModelError, sigmoid
from meridian.prediction.verdict_inputs import (
    FEATURES,
    FULL,
    OUTCOME,
    ROUTES,
    SNR,
    ReceptionInputs,
    inputs_sha256,
)
from meridian.prediction.verdict_score import (
    VERDICT_FORMAT,
    parse_verdict_model,
    score_reception,
)

METHOD = "verdict-1:0123456789ab"


def linear(route: str, *, intercept: float = 0.0) -> dict[str, object]:
    names = list(FEATURES[route])
    return {
        "features": names,
        "mean": [0.0] * len(names),
        "scale": [1.0] * len(names),
        "coefficients": [0.0] * len(names),
        "intercept": intercept,
        "calibration": {"method": "platt", "a": 1.0, "b": 0.0},
    }


def document(**changes: object) -> dict[str, object]:
    held: dict[str, object] = {
        "verdict_format": VERDICT_FORMAT,
        "method": METHOD,
        "rubric": "usable-1",
        "partial_below": 0.5,
        "routes": {
            FULL: linear(FULL, intercept=2.0),
            SNR: linear(SNR, intercept=1.0),
            OUTCOME: linear(OUTCOME, intercept=-1.0),
        },
    }
    return held | changes


def raw(held: dict[str, object]) -> bytes:
    return json.dumps(held).encode()


INPUTS = ReceptionInputs(
    outcome="decoded",
    signal_detected=True,
    peak_snr_db=12.0,
    frames_decoded=5000,
    frames_expected=5273,
    decoder="satdump",
    decoder_version="1.2.2",
    listening_confirmed=True,
    mode="lrpt",
)


@pytest.mark.parametrize(
    ("inputs", "route", "intercept"),
    [
        (INPUTS, FULL, 2.0),
        (replace(INPUTS, frames_decoded=None), SNR, 1.0),
        (replace(INPUTS, peak_snr_db=None, outcome="no_signal"), OUTCOME, -1.0),
    ],
)
def test_each_reception_is_scored_by_its_routes_model(
    inputs: ReceptionInputs, route: str, intercept: float
) -> None:
    model = parse_verdict_model(raw(document()))

    verdict = score_reception(model, inputs)

    assert verdict.route == route
    assert verdict.probability_usable == pytest.approx(sigmoid(intercept))
    assert verdict.method == METHOD
    assert verdict.inputs_sha256 == inputs_sha256(inputs)


def test_a_reception_that_received_nothing_still_gets_a_verdict() -> None:
    model = parse_verdict_model(raw(document()))
    nothing = replace(
        INPUTS,
        outcome="no_signal",
        signal_detected=False,
        peak_snr_db=None,
        frames_decoded=None,
        decoder=None,
        decoder_version=None,
    )

    assert 0.0 < score_reception(model, nothing).probability_usable < 1.0


def test_the_threshold_and_rubric_are_read() -> None:
    model = parse_verdict_model(raw(document(partial_below=0.35)))

    assert model.partial_below == 0.35
    assert model.rubric == "usable-1"
    assert set(model.routes) == set(ROUTES)


def routes_without(route: str) -> dict[str, object]:
    held = document()["routes"]
    assert isinstance(held, dict)
    return {name: value for name, value in held.items() if name != route}


def routes_reading(route: str, features: list[str]) -> dict[str, object]:
    held = dict(routes_without("none"))
    held[route] = linear(route) | {
        "features": features,
        "mean": [0.0] * len(features),
        "scale": [1.0] * len(features),
        "coefficients": [0.0] * len(features),
    }
    return held


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"verdict_format": 2}, "unknown verdict format"),
        ({"method": "verdict-1"}, "method"),
        ({"method": None}, "method"),
        ({"rubric": "Usable 1"}, "rubric"),
        ({"partial_below": 0}, "partial_below"),
        ({"partial_below": 1.0}, "partial_below"),
        ({"partial_below": True}, "partial_below"),
        ({"partial_below": float("nan")}, "partial_below"),
        ({"routes": routes_without(SNR)}, "routes must be exactly"),
        ({"routes": routes_without("none") | {"extra": {}}}, "routes must be exactly"),
        ({"routes": routes_reading(SNR, list(FEATURES[FULL]))}, "routes.snr reads"),
        (
            {"routes": routes_reading(OUTCOME, list(reversed(FEATURES[OUTCOME])))},
            "routes.outcome reads",
        ),
    ],
)
def test_a_malformed_model_is_refused_by_name(
    changes: dict[str, object], reason: str
) -> None:
    with pytest.raises(MalformedModelError, match=reason):
        parse_verdict_model(raw(document(**changes)))


@pytest.mark.parametrize("data", [b"\xff", b"not json", b"[]"])
def test_a_file_that_is_not_a_model_is_refused(data: bytes) -> None:
    with pytest.raises(MalformedModelError):
        parse_verdict_model(data)
