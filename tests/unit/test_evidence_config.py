"""The evidence configuration: which populations a package holds.

Marked as a unit test by living in ``tests/unit``.

Reference: docs/DECISIONS.md D-104, D-144, D-311.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from meridian.datasets.evidence_config import (
    EvidenceConfig,
    EvidenceConfigError,
    evidence_config_sha256,
    load_evidence_config,
    parse_evidence_config,
)

EXAMPLE = Path(__file__).resolve().parents[2] / "analysis/configs/dataset.toml.example"


def test_the_example_is_the_default_measured_only() -> None:
    held = load_evidence_config(EXAMPLE)

    assert held.config == EvidenceConfig()
    assert held.config.populations == ("measured",)
    assert held.text == EXAMPLE.read_bytes()


def test_both_populations_are_held_in_one_order_however_written() -> None:
    first = parse_evidence_config(
        b'[export]\npopulations = ["simulated", "measured"]\n'
    )
    second = parse_evidence_config(
        b'[export]\npopulations = ["measured", "simulated"]\n'
    )

    assert first.config.populations == ("measured", "simulated")
    assert evidence_config_sha256(first.config) == evidence_config_sha256(second.config)


def test_the_hash_is_of_the_values_not_the_file() -> None:
    commented = parse_evidence_config(b"# a comment\n[export]\n")

    assert evidence_config_sha256(commented.config) == evidence_config_sha256(
        EvidenceConfig()
    )
    assert evidence_config_sha256(
        parse_evidence_config(b'[export]\npopulations = ["simulated"]\n').config
    ) != evidence_config_sha256(EvidenceConfig())


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (b"[export\n", "not TOML"),
        (b"[labels]\n", "unknown tables"),
        (b"[export]\nsimulated = true\n", "unknown keys"),
        (b'[export]\npopulations = ["archive"]\n', "a population is one of"),
        (b"[export]\npopulations = []\n", "non-empty list"),
        (b'[export]\npopulations = ["measured", "measured"]\n', "names one twice"),
    ],
    ids=["not-toml", "table", "key", "population", "empty", "twice"],
)
def test_a_configuration_it_cannot_obey_is_refused(text: bytes, reason: str) -> None:
    with pytest.raises(EvidenceConfigError, match=reason):
        parse_evidence_config(text)
