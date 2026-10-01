"""``verdict_inputs`` — routes by evidence, features without zeros, a stable hash.

Reference: docs/DECISIONS.md D-261.
"""

from __future__ import annotations

from dataclasses import fields, replace

import pytest

from meridian.prediction.verdict_inputs import (
    FEATURES,
    FULL,
    OUTCOME,
    SNR,
    ReceptionInputs,
    feature_values,
    inputs_sha256,
    route_of,
)

DECODED = ReceptionInputs(
    outcome="decoded",
    signal_detected=True,
    peak_snr_db=14.5,
    frames_decoded=4800,
    frames_expected=5273,
    decoder="satdump",
    decoder_version="1.2.2",
    listening_confirmed=True,
    mode="lrpt",
)

NOTHING = ReceptionInputs(
    outcome="no_signal",
    signal_detected=False,
    peak_snr_db=None,
    frames_decoded=None,
    frames_expected=5273,
    decoder=None,
    decoder_version=None,
    listening_confirmed=True,
    mode="lrpt",
)


def test_full_evidence_takes_the_full_route() -> None:
    assert route_of(DECODED) == FULL
    assert feature_values(DECODED)["frames_ratio"] == pytest.approx(4800 / 5273)


def test_no_decoder_statistics_takes_the_snr_route_and_no_ratio() -> None:
    msp_02 = replace(DECODED, frames_decoded=None, decoder=None, decoder_version=None)

    assert route_of(msp_02) == SNR
    assert set(feature_values(msp_02)) == set(FEATURES[SNR])


def test_an_unknown_interval_takes_the_snr_route() -> None:
    """D-250: an interval nobody stated leaves the ratio absent, not guessed."""
    unknown = replace(DECODED, frames_expected=None)

    assert unknown.frames_ratio is None
    assert route_of(unknown) == SNR


def test_a_reception_that_heard_nothing_takes_the_outcome_route() -> None:
    values = feature_values(NOTHING)

    assert route_of(NOTHING) == OUTCOME
    assert set(values) == set(FEATURES[OUTCOME])
    assert values["no_signal"] == 1.0
    assert values["decoded"] == 0.0


def test_a_missing_input_is_absent_never_zero() -> None:
    values = feature_values(NOTHING)

    assert "peak_snr_db" not in values
    assert "frames_ratio" not in values


def test_a_ratio_without_an_snr_goes_to_the_outcome_route() -> None:
    odd = replace(DECODED, peak_snr_db=None)

    assert route_of(odd) == OUTCOME
    assert "frames_ratio" not in feature_values(odd)


@pytest.mark.parametrize("route", [FULL, SNR, OUTCOME])
def test_every_route_reads_a_superset_of_the_one_below(route: str) -> None:
    assert set(FEATURES[OUTCOME]) <= set(FEATURES[route]) <= set(FEATURES[FULL])


def test_every_route_has_its_features_when_chosen() -> None:
    for inputs in (DECODED, NOTHING, replace(DECODED, frames_decoded=None)):
        assert set(feature_values(inputs)) == set(FEATURES[route_of(inputs)])


@pytest.mark.parametrize(
    ("mode", "image"), [("lrpt", 1.0), ("apt", 1.0), ("gmsk", 0.0), ("afsk", 0.0)]
)
def test_the_data_type_follows_the_mode(mode: str, image: float) -> None:
    assert feature_values(replace(DECODED, mode=mode))["image"] == image


def test_the_hash_is_stable() -> None:
    assert inputs_sha256(DECODED) == inputs_sha256(replace(DECODED))
    assert len(inputs_sha256(DECODED)) == 32


CHANGED = {
    "outcome": "signal_no_decode",
    "signal_detected": False,
    "peak_snr_db": 14.6,
    "frames_decoded": 4799,
    "frames_expected": 5274,
    "decoder": "other",
    "decoder_version": "1.2.3",
    "listening_confirmed": False,
    "mode": "hrpt",
}


def test_every_input_is_hashed() -> None:
    assert set(CHANGED) == {one.name for one in fields(ReceptionInputs)}


@pytest.mark.parametrize("name", sorted(CHANGED))
def test_changing_any_input_changes_the_hash(name: str) -> None:
    changed = replace(DECODED, **{name: CHANGED[name]})

    assert inputs_sha256(changed) != inputs_sha256(DECODED)


def test_absent_and_zero_hash_apart() -> None:
    assert inputs_sha256(replace(NOTHING, peak_snr_db=0.0)) != inputs_sha256(NOTHING)
