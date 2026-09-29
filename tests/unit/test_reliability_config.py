"""``meridian.reliability.config`` — strict, with the labeller's defaults.

Reference: docs/DECISIONS.md D-182, D-184.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from meridian.datasets.label_config import LabelConfig
from meridian.reliability.config import (
    ClassificationConfig,
    ReliabilityConfig,
    ReliabilityConfigError,
    SloConfig,
    load_reliability_config,
    parse_reliability_config,
)

EXAMPLE = Path(__file__).resolve().parents[2] / "deploy" / "reliability.toml.example"


def test_the_example_file_spells_out_the_defaults() -> None:
    assert load_reliability_config(EXAMPLE) == ReliabilityConfig()


def test_no_file_is_the_defaults() -> None:
    assert load_reliability_config(None) == parse_reliability_config("")


def test_the_classification_defaults_are_the_labellers() -> None:
    """A pass classified live and labelled from a snapshot use one margin."""
    labels, live = LabelConfig(), ClassificationConfig()

    assert (labels.settle_margin_s, labels.silent_window_s) == (
        live.settle_margin_s,
        live.silent_window_s,
    )
    assert labels.silent_min_attempts == live.silent_min_attempts


def test_sc4_and_sc5_are_the_default_targets() -> None:
    slo = SloConfig()

    assert (slo.capture_rate_min, slo.window_days) == (0.90, 30)
    assert slo.failure_detection_max_s == 90


@pytest.mark.parametrize(
    ("text", "names"),
    [
        ("[slos]\nwindow_days = 7\n", "unknown tables"),
        ("[slo]\nwindow_day = 7\n", "unknown settings ['window_day']"),
        ("[classification]\nsettle_margin = 1\n", "unknown settings"),
        ("[slo]\ncapture_rate_min = 1.5\n", "between 0 and 1"),
        ("[slo]\nwindow_days = 0\n", "window_days must be in"),
        ("[slo]\nwindow_days = 7.5\n", "whole number"),
        ("[classification]\nsilent_min_attempts = true\n", "whole number"),
        ("slo = 3\n", "must be a table"),
        ("not toml [", "not TOML"),
    ],
)
def test_anything_it_cannot_obey_is_refused_by_name(text: str, names: str) -> None:
    with pytest.raises(ReliabilityConfigError, match=names.replace("[", r"\[")):
        parse_reliability_config(text)


def test_only_the_classification_parameters_are_hashed() -> None:
    """A target changes no classification, so it cannot change the rows' key."""
    base = parse_reliability_config("")
    retargeted = parse_reliability_config("[slo]\ncapture_rate_min = 0.8\n")
    remargined = parse_reliability_config("[classification]\nsilent_window_s = 60\n")

    assert base.classification.sha256() == retargeted.classification.sha256()
    assert base.classification.sha256() != remargined.classification.sha256()
    assert len(base.classification.sha256()) == 32


def test_an_unreadable_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ReliabilityConfigError, match="cannot read"):
        load_reliability_config(tmp_path / "absent.toml")
