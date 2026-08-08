"""The two pure decisions behind MSP §6's request limits.

``meridian.api.request_limits`` separates the policy — which cap applies, and
whether a declared length meets it — from the ASGI plumbing that applies it, so
both are testable without a server. The wire behaviour is asserted separately in
``tests/msp_conformance/test_request_limits.py``.

Reference: docs/MSP-SPEC.md §6 "Request limits", docs/DECISIONS.md D-028, D-050.
"""

from __future__ import annotations

import pytest

from meridian.api.request_limits import (
    is_declared_size_acceptable,
    limit_for_path_bytes,
)

# MSP §6's table, written out rather than imported from the module under test.
STANDARD = 64 * 1024
OBSERVATIONS = 256 * 1024


@pytest.mark.parametrize(
    ("path", "expected_bytes"),
    [
        ("/msp/v0/register", STANDARD),
        ("/msp/v0/heartbeat", STANDARD),
        ("/msp/v0/time", STANDARD),
        ("/msp/v0/observations", OBSERVATIONS),
    ],
)
def test_each_endpoint_gets_the_cap_the_specification_tabulates(
    path: str, expected_bytes: int
) -> None:
    assert limit_for_path_bytes(path) == expected_bytes


def test_a_future_major_version_keeps_the_observations_cap() -> None:
    """Matching on the suffix is what makes the prefix free to move (§7)."""
    assert limit_for_path_bytes("/msp/v1/observations") == OBSERVATIONS


def test_an_unknown_path_gets_the_smaller_cap() -> None:
    """A path §6 does not name is capped from its first commit, not its second."""
    assert limit_for_path_bytes("/healthz") == STANDARD


@pytest.mark.parametrize("declared", ["0", "1", "65536"])
def test_a_length_within_the_cap_is_accepted(declared: str) -> None:
    assert is_declared_size_acceptable(declared, STANDARD) is True


def test_a_length_one_byte_over_the_cap_is_refused() -> None:
    assert is_declared_size_acceptable("65537", STANDARD) is False


def test_an_absent_length_is_refused() -> None:
    """D-050: a chunked body declares no size, so §6's check cannot run on it."""
    assert is_declared_size_acceptable(None, STANDARD) is False


@pytest.mark.parametrize("declared", ["", "64K", "1.5", "sixty-four", "0x100"])
def test_a_length_that_is_not_a_number_is_refused(declared: str) -> None:
    """Leniency here would be a bypass: `64K` parses as absent and sails through."""
    assert is_declared_size_acceptable(declared, STANDARD) is False


def test_a_negative_length_is_refused() -> None:
    """`int()` accepts a leading minus; a body cannot have a negative size."""
    assert is_declared_size_acceptable("-1", STANDARD) is False
