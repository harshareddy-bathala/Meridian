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
    CompletenessConfig,
    LabelConfig,
    LabelConfigError,
    PropensityConfig,
    config_sha256,
    load_label_config,
    parse_label_config,
)
from meridian.datasets.snapshot_rows import MalformedSnapshotError, parse_rows

# --- the configuration --------------------------------------------------------


def test_the_defaults_are_the_decisions() -> None:
    """24 hours to settle (D-146); 12 hours either side and two attempts (D-147);
    0.8 and its sensitivity band (D-151); the horizon and two minutes (D-150);
    cells of twenty, three elevation edges and four-hour bands (D-152)."""
    assert LabelConfig().parameters() == {
        "settle_margin_s": 86_400,
        "silent_window_s": 43_200,
        "silent_min_attempts": 2,
        "completeness": {
            "threshold": 0.8,
            "sensitivity": [0.5, 0.6, 0.7, 0.8, 0.9],
            "archive_min_elevation_deg": 0.0,
            "archive_match_tolerance_s": 120,
        },
        "propensity": {
            "min_cell": 20,
            "elevation_bands_deg": [15.0, 30.0, 60.0],
            "hour_band_h": 4,
        },
    }


def test_the_completeness_table_overrides_only_what_it_names() -> None:
    config = parse_label_config(
        "[completeness]\nthreshold = 0.75\nsensitivity = [0.6, 0.75, 1]\n"
    )

    assert config.completeness == CompletenessConfig(
        threshold=0.75, sensitivity=(0.6, 0.75, 1.0)
    )
    assert config.settle_margin_s == LabelConfig().settle_margin_s


def test_a_whole_number_ratio_is_the_same_setting_as_its_float() -> None:
    """``threshold = 1`` and ``threshold = 1.0`` hash alike: one setting."""
    whole = parse_label_config("[completeness]\nthreshold = 1\n")
    decimal = parse_label_config("[completeness]\nthreshold = 1.0\n")

    assert config_sha256(whole) == config_sha256(decimal)


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("completeness = 0.8\n", "must be a table"),
        ("[completeness]\nthresold = 0.8\n", "unknown completeness settings"),
        ("[completeness]\nthreshold = 1.2\n", "outside 0..1"),
        ("[completeness]\nthreshold = '0.8'\n", "must be a number"),
        ("[completeness]\nthreshold = true\n", "must be a number"),
        ("[completeness]\nsensitivity = 0.8\n", "must be a list"),
        ("[completeness]\nsensitivity = []\n", "at least one"),
        ("[completeness]\nsensitivity = [0.9, 0.5]\n", "must rise"),
        ("[completeness]\nsensitivity = [0.5, 0.5]\n", "must rise"),
        ("[completeness]\narchive_min_elevation_deg = 90\n", "outside 0..90"),
        ("[completeness]\narchive_match_tolerance_s = 1.5\n", "whole number"),
        ("[completeness]\narchive_match_tolerance_s = 3601\n", "outside"),
    ],
    ids=[
        "not-a-table",
        "misspelt",
        "above-one",
        "text",
        "bool",
        "not-a-list",
        "empty",
        "falling",
        "repeated",
        "zenith",
        "fractional-seconds",
        "over-an-hour",
    ],
)
def test_a_completeness_table_that_cannot_be_obeyed_is_refused(
    text: str, match: str
) -> None:
    with pytest.raises(LabelConfigError, match=match):
        parse_label_config(text)


def test_the_propensity_table_overrides_only_what_it_names() -> None:
    config = parse_label_config("[propensity]\nelevation_bands_deg = [10, 45]\n")

    assert config.propensity == PropensityConfig(elevation_bands_deg=(10.0, 45.0))


def test_one_elevation_band_is_a_list_with_no_edges() -> None:
    config = parse_label_config("[propensity]\nelevation_bands_deg = []\n")

    assert config.propensity.elevation_bands_deg == ()


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("propensity = 20\n", "must be a table"),
        ("[propensity]\nmin_cells = 20\n", "unknown propensity settings"),
        ("[propensity]\nmin_cell = 0\n", "outside"),
        ("[propensity]\nmin_cell = 2.5\n", "whole number"),
        ("[propensity]\nelevation_bands_deg = [30, 15]\n", "must rise"),
        ("[propensity]\nelevation_bands_deg = [0]\n", "outside 0..90"),
        ("[propensity]\nelevation_bands_deg = [90]\n", "outside 0..90"),
        ("[propensity]\nhour_band_h = 5\n", "does not divide 24"),
        ("[propensity]\nhour_band_h = 0\n", "outside"),
    ],
    ids=[
        "not-a-table",
        "misspelt",
        "empty-cell",
        "fractional-cell",
        "falling",
        "horizon-edge",
        "zenith-edge",
        "ragged-hours",
        "no-hours",
    ],
)
def test_a_propensity_table_that_cannot_be_obeyed_is_refused(
    text: str, match: str
) -> None:
    with pytest.raises(LabelConfigError, match=match):
        parse_label_config(text)


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
    "element_set_id": 7,
    "simulated": False,
    "max_elevation_deg": 61.4,
}

ELEMENT_SET = {"id": 7, "satellite_id": "norad:57166", "epoch": AOS}


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
            "archive_passes",
        )
    }
    return (
        empty
        | {
            "passes.jsonl": canonical_line(PASS),
            "element_sets.jsonl": canonical_line(ELEMENT_SET),
        }
        | overrides
    )


def test_rows_are_read_back_typed() -> None:
    (read,) = parse_rows(files()).passes

    assert read.pass_id == 1
    assert read.aos == AOS
    assert read.element_set_epoch == AOS
    assert read.simulated is False


def test_a_pass_whose_element_set_is_not_held_is_refused() -> None:
    """The export always writes the sets its passes name; a gap is damage."""
    with pytest.raises(MalformedSnapshotError, match="element set 7"):
        parse_rows(files(**{"element_sets.jsonl": b""}))


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


def test_the_example_file_spells_out_exactly_the_defaults() -> None:
    """``deploy/snapshot.toml.example`` documents the defaults; it must not drift."""
    example = Path(__file__).resolve().parents[2] / "deploy" / "snapshot.toml.example"

    assert load_label_config(example) == LabelConfig()
