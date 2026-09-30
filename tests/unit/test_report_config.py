"""``meridian.reports.config`` — one strict file, each table checked by its owner.

Reference: docs/DECISIONS.md D-236.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from meridian.datasets.label_config import LabelConfig, load_label_config
from meridian.reports.config import (
    ReportConfig,
    ReportConfigError,
    parse_report_config,
    report_config_sha256,
)

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "analysis/configs/evaluation.toml.example"


def test_an_empty_file_is_every_default() -> None:
    assert parse_report_config(b"").config == ReportConfig()


def test_the_example_states_the_labelling_defaults_the_labeller_documents() -> None:
    """The example's [labels] and deploy/snapshot.toml.example cannot drift apart."""
    labels = parse_report_config(EXAMPLE.read_bytes()).config.labels

    assert labels == load_label_config(REPO / "deploy/snapshot.toml.example")
    assert labels == LabelConfig()


def test_the_bytes_are_kept_as_given() -> None:
    text = b"# a comment\n[labels]\nsettle_margin_s = 3600"

    assert parse_report_config(text).text == text


def test_the_hash_is_over_values_not_spelling() -> None:
    plain = parse_report_config(b"[labels]\nsettle_margin_s = 3600\n").config
    commented = parse_report_config(
        b"# why an hour\n[labels]\n\nsettle_margin_s = 3600 # really\n"
    ).config
    other = parse_report_config(b"[labels]\nsettle_margin_s = 7200\n").config

    assert report_config_sha256(plain) == report_config_sha256(commented)
    assert report_config_sha256(plain) != report_config_sha256(other)


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (b"seed = 1\n", "--seed"),
        (b"[labels]\nseed = 1\n", "unknown labelling settings"),
        (b"[model]\n", "unknown tables"),
        (b"labels = 3\n", "must be a table"),
        (b"[labels]\nsilent_min_attempts = 0\n", "[labels]"),
        (b"[labels\n", "not UTF-8 TOML"),
        (b'[prediction]\nconfiguration = "D"\n', "fits every configuration"),
        (b"[prediction]\nseed = 3\n", "derives every seed"),
        (b'[prediction]\nwithout = ["conditions"]\n', "leaves out each group"),
        (b"[prediction]\nfolds = 99\n", "[prediction]"),
        (b"[prediction]\nlearning_rate = 1\n", "unknown settings"),
        (b"[prediction]\nresamples = 5\n", "outside 100..100000"),
        (b"[prediction]\nmin_disturbed = true\n", "must be a number"),
        (b'[prediction]\npopulation = "archive"\nweighting = "x"\n', "weighting"),
        (b"[scheduling]\nseed = 1\n", "the seed is derived"),
        (b"[scheduling]\nmodels = {}\n", "unknown settings"),
        (b'[scheduling]\nframes = "all"\n', "[scheduling]"),
        (b"[scheduling]\ntime_limit_s = 0\n", "[scheduling]"),
        (b"[scheduling]\nthreshold = 0\n", "above 0"),
        (b"[scheduling]\nthreshold = 1.5\n", "outside 0..1"),
        (b"[scheduling]\nresamples = 10\n", "outside 100..100000"),
        (b"[orbit]\nseed = 2\n", "the seed is derived"),
        (b"[orbit]\nmin_young = 1\n", "outside 2..100000"),
        (b"[orbit]\nresamples = 1.5\n", "must be a number"),
        (b"\xff\xfe", "not UTF-8 TOML"),
    ],
)
def test_what_it_cannot_use_is_refused(text: bytes, reason: str) -> None:
    with pytest.raises(ReportConfigError, match=reason.replace("[", r"\[")):
        parse_report_config(text)


def test_prediction_settings_are_the_models_own_and_the_sections() -> None:
    config = parse_report_config(
        b"[prediction]\nmin_station_history = 5\n"
        b"train_until = 2026-09-11T00:00:00Z\n"
        b"validate_until = 2026-09-16T00:00:00Z\n"
        b"resamples = 500\nmin_disturbed = 12\n"
    ).config.prediction

    assert config.model.min_station_history == 5
    assert config.resamples == 500
    assert config.min_disturbed == 12
    shared = config.parameters()["model"]
    assert isinstance(shared, dict)
    assert "seed" not in shared
    assert "configuration" not in shared


def test_scheduling_settings_reach_the_solver_but_never_its_seed() -> None:
    config = parse_report_config(
        b"[scheduling]\ntime_limit_s = 3.0\nturnaround_s = 60.0\nthreshold = 0.7\n"
    ).config.scheduling

    assert config.schedule.time_limit_s == 3.0
    assert config.threshold == 0.7
    assert "seed" not in config.parameters()
    assert config.parameters()["turnaround_s"] == 60.0
