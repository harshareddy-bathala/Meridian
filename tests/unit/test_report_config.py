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
        (b"\xff\xfe", "not UTF-8 TOML"),
    ],
)
def test_what_it_cannot_use_is_refused(text: bytes, reason: str) -> None:
    with pytest.raises(ReportConfigError, match=reason.replace("[", r"\[")):
        parse_report_config(text)
