"""The evidence configuration: which populations a package holds, how precisely.

Marked as a unit test by living in ``tests/unit``.

Reference: docs/DECISIONS.md D-104, D-144, D-311, D-369.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from meridian.datasets.canonical import canonical_bytes
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


def test_full_precision_hashes_as_it_did_before_the_key_existed() -> None:
    """Every package exported before ``precision`` still regenerates to its hash."""
    before = hashlib.sha256(canonical_bytes({"populations": ["measured"]})).digest()
    written = parse_evidence_config(b'[export]\nprecision = "full"\n').config

    assert evidence_config_sha256(EvidenceConfig()) == before
    assert evidence_config_sha256(written) == before
    assert written.parameters() == {"populations": ["measured"]}


def test_the_public_cut_is_a_value_of_its_own() -> None:
    public = parse_evidence_config(b'[export]\nprecision = "public"\n').config

    assert public.precision == "public"
    assert public.parameters() == {"populations": ["measured"], "precision": "public"}
    assert evidence_config_sha256(public) != evidence_config_sha256(EvidenceConfig())


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (b"[export\n", "not TOML"),
        (b"[labels]\n", "unknown tables"),
        (b"[export]\nsimulated = true\n", "unknown keys"),
        (b'[export]\npopulations = ["archive"]\n', "a population is one of"),
        (b"[export]\npopulations = []\n", "non-empty list"),
        (b'[export]\npopulations = ["measured", "measured"]\n', "names one twice"),
        (b'[export]\nprecision = "exact"\n', "precision is one of"),
        (b"[export]\nprecision = true\n", "precision is one of"),
    ],
    ids=[
        "not-toml",
        "table",
        "key",
        "population",
        "empty",
        "twice",
        "precision",
        "flag",
    ],
)
def test_a_configuration_it_cannot_obey_is_refused(text: bytes, reason: str) -> None:
    with pytest.raises(EvidenceConfigError, match=reason):
        parse_evidence_config(text)
