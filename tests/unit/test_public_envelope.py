"""The public API's error vocabulary, and the prefix match that selects it.

Two things are pinned here. The vocabulary is a table, so it is read as one. The
prefix match is a string comparison with one genuine trap in it — `/api/v1` is a
prefix of `/api/v10` — and that trap is the reason the function exists instead of
a `startswith` at each call site.

No application is built and no request is made: `is_public_surface` takes a path
and `PublicError` takes a code, so both are testable as plain functions.

Marked as a unit test by living in ``tests/unit``: no network, no filesystem.

Reference: docs/DECISIONS.md D-084, D-083.
"""

from __future__ import annotations

import pytest

from meridian.api.errors import STATUS_FOR_CODE
from meridian.api.public.envelope import (
    API_PREFIX,
    INVALID_QUERY,
    NOT_FOUND,
    SERVER_ERROR,
    STATUS_FOR_PUBLIC_CODE,
    PublicError,
    is_public_surface,
)


@pytest.mark.parametrize(
    ("code", "expected_status"),
    [(NOT_FOUND, 404), (INVALID_QUERY, 400), (SERVER_ERROR, 500)],
)
def test_each_public_code_is_served_at_its_status(
    code: str, expected_status: int
) -> None:
    """The whole table, one row at a time."""
    assert STATUS_FOR_PUBLIC_CODE[code] == expected_status
    assert PublicError(code, "a message").status == expected_status


def test_msp_gains_no_code_from_the_public_surface_existing() -> None:
    """MSP §6's table stays exactly the eight codes the specification lists.

    Its own docstring promises a code absent from it cannot be sent, which is
    what stops an endpoint inventing a ninth. `not_found` is the code the public
    API needed and the one most likely to be added here by someone who had not
    read D-084 — so it is named explicitly rather than left to a count.
    """
    assert NOT_FOUND not in STATUS_FOR_CODE
    assert INVALID_QUERY not in STATUS_FOR_CODE
    assert len(STATUS_FOR_CODE) == 8


def test_a_code_outside_the_table_cannot_be_raised() -> None:
    """Including one that is valid on the *other* surface.

    `unknown_assignment` is a real MSP §6 code, and raising it from a read
    endpoint would answer a dashboard in a protocol vocabulary it has no reason
    to understand. Refusing at construction is what keeps the two apart.
    """
    with pytest.raises(ValueError, match="not a public API error code"):
        PublicError("unknown_assignment", "a message")


@pytest.mark.parametrize(
    "path",
    [
        API_PREFIX,
        f"{API_PREFIX}/",
        f"{API_PREFIX}/stations",
        f"{API_PREFIX}/stations/st_abc123",
    ],
)
def test_the_api_prefix_and_everything_under_it_is_public(path: str) -> None:
    """The prefix bare, with a trailing slash, and at depth."""
    assert is_public_surface(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "/api/v10/stations",
        "/api/v1x",
        "/api/v2/stations",
        "/msp/v0/register",
        "/healthz",
        "/metrics",
        "/",
        "",
    ],
)
def test_a_path_that_merely_starts_the_same_is_not_public(path: str) -> None:
    """`/api/v10` is the case a bare `startswith` gets wrong.

    It reads as a typo today because no `v10` exists. It will not read as a typo
    on the day `/api/v2` ships and someone reaches for the obvious one-liner —
    and by then the failure is a second API version answering in the first one's
    error vocabulary, which nothing would alert on.

    `/msp/v0/register` is here for the opposite direction: a station must keep
    getting MSP §6's words, whatever the read API calls things.
    """
    assert is_public_surface(path) is False
