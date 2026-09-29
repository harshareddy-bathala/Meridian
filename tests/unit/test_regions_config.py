"""The regional configuration: strict, hashed, and the example as shipped.

Reference: docs/DECISIONS.md D-229, D-231.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from meridian.regions.config import (
    DEFAULT_RULES,
    RegionsConfig,
    RegionsConfigError,
    load_regions_config,
    parse_regions_config,
)

EXAMPLE = Path(__file__).resolve().parents[2] / "deploy" / "regions.toml.example"


def test_the_shipped_example_parses_to_the_defaults() -> None:
    config = load_regions_config(EXAMPLE)
    assert config == RegionsConfig()
    assert dict(config.rules) == dict(DEFAULT_RULES)


def test_periods_come_as_a_pair() -> None:
    with pytest.raises(RegionsConfigError, match="both"):
        parse_regions_config(
            {
                "baseline": {
                    "from": "2026-06-01T00:00:00Z",
                    "until": "2026-08-01T00:00:00Z",
                }
            }
        )


@pytest.mark.parametrize(
    ("table", "said"),
    [
        ({"seeds": 1}, "unknown keys"),
        ({"confidence": 1.5}, "outside"),
        ({"resamples": 10}, "outside"),
        ({"rules": {"ndvi": {"kind": "ratio", "threshold": -0.1}}}, "kind"),
        (
            {"rules": {"ndvi": {"kind": "relative", "threshold": 0}}},
            "watches for nothing",
        ),
        ({"include_simulated": "yes"}, "true or false"),
        (
            {
                "baseline": {
                    "from": "2026-08-01T00:00:00Z",
                    "until": "2026-06-01T00:00:00Z",
                },
                "current": {
                    "from": "2026-09-01T00:00:00Z",
                    "until": "2026-09-29T00:00:00Z",
                },
            },
            "does not follow",
        ),
    ],
)
def test_a_configuration_that_cannot_be_obeyed_is_refused(
    table: dict[str, object], said: str
) -> None:
    with pytest.raises(RegionsConfigError, match=said):
        parse_regions_config(table)


def test_the_hash_is_of_the_values_and_moves_with_them() -> None:
    assert RegionsConfig().sha256 == RegionsConfig().sha256
    assert RegionsConfig(seed=1).sha256 != RegionsConfig().sha256
