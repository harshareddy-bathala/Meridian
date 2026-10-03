"""The ``[diagnosis]`` table: strict, hashed, and apart from the classification.

Reference: docs/DECISIONS.md D-182, D-273.
"""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import pytest

from meridian.reliability.config import (
    DiagnosisConfig,
    ReliabilityConfig,
    ReliabilityConfigError,
    parse_reliability_config,
)

EXAMPLE = "deploy/reliability.toml.example"

CLASSIFICATION_SHA256 = (
    "7d5aed639e89c9f370d1c1c160f51ab48e74d96d0da08b45ef735299724b304c"
)
"""The default classification's hash, computed before Stage 27 touched the file."""


def test_the_defaults_are_what_an_empty_file_gives() -> None:
    assert parse_reliability_config("").diagnosis == DiagnosisConfig()


def test_the_example_file_spells_out_every_default() -> None:
    text = (Path(__file__).resolve().parents[2] / EXAMPLE).read_text()

    assert parse_reliability_config(text) == ReliabilityConfig()
    for one in fields(DiagnosisConfig):
        assert f"\n{one.name} = " in text, one.name


def test_a_setting_is_read_from_its_table() -> None:
    parsed = parse_reliability_config("[diagnosis]\ninterference_lift_db = 3.5\n")

    assert parsed.diagnosis.interference_lift_db == 3.5


def test_an_unknown_setting_is_refused_by_name() -> None:
    with pytest.raises(ReliabilityConfigError, match="interference_lift"):
        parse_reliability_config("[diagnosis]\ninterference_lift = 3.5\n")


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("conflict_margin", 1.0),
        ("obstruction_min_passes", 0),
        ("silent_window_s", 2.5),
        ("heard_snr_db", "3"),
        ("timing_tolerance_s", -1),
    ],
)
def test_a_value_out_of_range_is_refused_by_name(name: str, value: object) -> None:
    with pytest.raises(ReliabilityConfigError, match=name):
        DiagnosisConfig(**{name: value})  # type: ignore[arg-type]


@pytest.mark.parametrize("one", [one.name for one in fields(DiagnosisConfig)])
def test_every_threshold_moves_the_hash(one: str) -> None:
    base = DiagnosisConfig()
    value = getattr(base, one)
    moved = DiagnosisConfig(
        **{one: (value + 1 if isinstance(value, int) else value * 0.9)}
    )

    assert moved.sha256() != base.sha256()


def test_a_real_threshold_written_whole_is_the_same_configuration() -> None:
    """``2`` and ``2.0`` are one threshold, so they must be one hash (D-272)."""
    whole = parse_reliability_config(
        "[diagnosis]\ninterference_lift_db = 2\nheard_snr_db = 3\n"
    ).diagnosis

    assert whole.parameters() == DiagnosisConfig().parameters()
    assert whole.sha256() == DiagnosisConfig().sha256()
    assert isinstance(whole.parameters()["lookback_s"], int)


def test_the_classification_s_hash_did_not_move() -> None:
    """Its rows are found by this hash; a new table must not change it."""
    assert ReliabilityConfig().classification.sha256().hex() == (CLASSIFICATION_SHA256)
