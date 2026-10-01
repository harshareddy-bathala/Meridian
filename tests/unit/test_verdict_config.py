"""``verdict_config`` — strict, with a hash of the resolved values.

Reference: docs/DECISIONS.md D-162, D-262.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from meridian.prediction.verdict_config import (
    VerdictConfig,
    VerdictConfigError,
    load_verdict_config,
    parse_verdict_config,
    verdict_config_sha256,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

DATES = "train_until = 2027-01-01T00:00:00Z\nvalidate_until = 2027-02-01T00:00:00Z\n"


def test_the_defaults_are_the_documented_ones() -> None:
    config = parse_verdict_config("")

    assert config == VerdictConfig()
    assert config.partial_below == 0.5
    assert config.rubric == "usable-1"
    assert config.train_until is None


def test_dates_are_read() -> None:
    config = parse_verdict_config(DATES)

    assert config.train_until == datetime(2027, 1, 1, tzinfo=UTC)
    assert config.validate_until == datetime(2027, 2, 1, tzinfo=UTC)


def test_the_example_file_parses_to_the_defaults() -> None:
    example = REPO_ROOT / "deploy" / "verdict.toml.example"

    assert load_verdict_config(example) == VerdictConfig()


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("partial = 0.4\n", "unknown verdict settings: partial"),
        ("partial_below = 0\n", "partial_below"),
        ("partial_below = 1.5\n", "partial_below"),
        ("partial_below = true\n", "partial_below"),
        ("inverse_regularisation = -1\n", "inverse_regularisation"),
        ("inverse_regularisation = nan\n", "inverse_regularisation"),
        ("seed = -1\n", "seed"),
        ("seed = 1.5\n", "seed"),
        ('rubric = "Usable 1"\n', "rubric"),
        ("train_until = 2027-01-01\n", "train_until"),
        ("train_until = 2027-01-01T00:00:00\n", "train_until"),
        (
            "train_until = 2027-02-01T00:00:00Z\n"
            "validate_until = 2027-01-01T00:00:00Z\n",
            "before validate_until",
        ),
        ("this is not toml", "not TOML"),
    ],
)
def test_what_cannot_be_obeyed_is_refused_by_name(text: str, reason: str) -> None:
    with pytest.raises(VerdictConfigError, match=reason):
        parse_verdict_config(text)


def test_the_hash_is_of_values_not_bytes() -> None:
    plain = parse_verdict_config(DATES)
    commented = parse_verdict_config("# a comment\n" + DATES + "seed = 0\n")

    assert verdict_config_sha256(plain) == verdict_config_sha256(commented)
    assert verdict_config_sha256(plain) != verdict_config_sha256(
        parse_verdict_config(DATES + "partial_below = 0.4\n")
    )
