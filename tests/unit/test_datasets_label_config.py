"""``meridian.datasets.label_config`` and ``snapshot_rows`` — the labeller's inputs.

The configuration is half of what an evaluation dataset's hash depends on, so
what it refuses matters as much as what it reads. The rows are the other half,
and a row with the wrong shape is refused by name rather than labelled.

Reference: docs/DECISIONS.md D-144, D-146, D-147.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from meridian.datasets.canonical import canonical_line
from meridian.datasets.label_config import (
    LabelConfig,
    LabelConfigError,
    config_sha256,
    load_label_config,
    parse_label_config,
)
from meridian.datasets.snapshot_rows import MalformedSnapshotError, parse_rows

# --- the configuration --------------------------------------------------------


def test_the_defaults_are_the_decisions() -> None:
    """24 hours to settle (D-146); 12 hours either side and two attempts (D-147)."""
    assert LabelConfig().parameters() == {
        "settle_margin_s": 86_400,
        "silent_window_s": 43_200,
        "silent_min_attempts": 2,
    }


def test_every_key_is_optional_and_each_overrides_its_default() -> None:
    config = parse_label_config("settle_margin_s = 3600\n")

    assert config == LabelConfig(settle_margin_s=3600)


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("settle_margin = 3600\n", "unknown labelling settings"),
        ("settle_margin_s = '3600'\n", "whole number"),
        ("settle_margin_s = true\n", "whole number"),
        ("silent_window_s = 0\n", "outside"),
        ("silent_min_attempts = 0\n", "outside"),
        ("settle_margin_s = -1\n", "outside"),
        ("settle_margin_s = \n", "not TOML"),
    ],
    ids=["misspelt", "text", "bool", "no-window", "no-attempts", "negative", "broken"],
)
def test_a_configuration_that_cannot_be_obeyed_is_refused(
    text: str, match: str
) -> None:
    with pytest.raises(LabelConfigError, match=match):
        parse_label_config(text)


def test_no_file_is_the_defaults(tmp_path: Path) -> None:
    assert load_label_config(None) == LabelConfig()
    with pytest.raises(LabelConfigError, match="cannot read"):
        load_label_config(tmp_path / "absent.toml")


def test_the_hash_is_of_the_values_not_the_file() -> None:
    """A comment changes no label or hash; an empty file is the defaults."""
    explicit = parse_label_config(
        "# the decisions' defaults, spelled out\n"
        "silent_min_attempts = 2\nsettle_margin_s = 86400\n"
    )

    assert config_sha256(explicit) == config_sha256(parse_label_config(""))


def test_a_changed_value_changes_the_hash() -> None:
    assert config_sha256(LabelConfig(settle_margin_s=3600)) != config_sha256(
        LabelConfig()
    )


# --- the rows -----------------------------------------------------------------

AOS = datetime(2026, 9, 20, tzinfo=UTC)

PASS = {
    "id": 1,
    "station_id": "st_a",
    "satellite_id": "norad:57166",
    "aos": AOS,
    "los": AOS,
    "simulated": False,
    "max_elevation_deg": 61.4,
}


def files(**overrides: bytes) -> dict[str, bytes]:
    """Every file the labeller needs, empty but for one pass."""
    empty = {
        f"{name}.jsonl": b""
        for name in (
            "assignments",
            "observations",
            "heartbeats",
            "listening",
            "archive_observations",
        )
    }
    return empty | {"passes.jsonl": canonical_line(PASS)} | overrides


def test_rows_are_read_back_typed() -> None:
    (read,) = parse_rows(files()).passes

    assert read.pass_id == 1
    assert read.aos == AOS
    assert read.simulated is False


def test_a_file_the_labeller_needs_must_be_there() -> None:
    held = files()
    del held["listening.jsonl"]

    with pytest.raises(MalformedSnapshotError, match=r"no listening\.jsonl"):
        parse_rows(held)


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"simulated": "false"}, "not true or false"),
        ({"id": True}, "not an integer"),
        ({"aos": "2026-09-20T05:30:00+05:30"}, "not a UTC timestamp"),
    ],
    ids=["text-bool", "bool-id", "offset"],
)
def test_a_row_with_the_wrong_shape_is_refused_by_name(
    change: dict[str, object], match: str
) -> None:
    with pytest.raises(MalformedSnapshotError, match=match):
        parse_rows(files(**{"passes.jsonl": canonical_line(PASS | change)}))


def test_a_row_missing_a_field_is_refused_by_name() -> None:
    without = {key: value for key, value in PASS.items() if key != "los"}

    with pytest.raises(MalformedSnapshotError, match="'los'"):
        parse_rows(files(**{"passes.jsonl": canonical_line(without)}))
