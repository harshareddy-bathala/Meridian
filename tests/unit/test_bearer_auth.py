"""``parse_bearer_token`` — no database needed.

``get_authenticated_station_id`` itself needs a real connection to call
``PsycopgRegistry.authenticate()`` through, and is covered once the
heartbeat endpoint session builds something that exercises it end to end;
this file covers the pure half CLAUDE.local.md §3 asks to be separable.
"""

from __future__ import annotations

import pytest

from meridian.api.dependencies import parse_bearer_token
from meridian.api.errors import UNAUTHORIZED, MspError


def test_a_well_formed_header_yields_the_token() -> None:
    assert parse_bearer_token("Bearer abc123") == "abc123"


def test_a_missing_header_is_unauthorized() -> None:
    with pytest.raises(MspError) as excinfo:
        parse_bearer_token(None)
    assert excinfo.value.code == UNAUTHORIZED


def test_an_empty_header_is_unauthorized() -> None:
    with pytest.raises(MspError) as excinfo:
        parse_bearer_token("")
    assert excinfo.value.code == UNAUTHORIZED


@pytest.mark.parametrize(
    "header",
    [
        "abc123",  # no scheme at all
        "Basic abc123",  # the wrong scheme
        "bearer abc123",  # MSP is case-sensitive on the scheme name
    ],
)
def test_a_header_without_the_bearer_prefix_is_unauthorized(header: str) -> None:
    with pytest.raises(MspError) as excinfo:
        parse_bearer_token(header)
    assert excinfo.value.code == UNAUTHORIZED
