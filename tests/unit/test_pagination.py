"""Keyset pagination: the cursor round trip, and the trim that ends a list.

Both halves are pure, so nothing here builds an application or a database. What
is pinned is that a cursor survives the trip unchanged, that a cursor nobody
issued is refused rather than treated as page one, and that the last page says it
is the last page.

Marked as a unit test by living in ``tests/unit``: no network, no filesystem.

Reference: docs/DECISIONS.md D-085.
"""

from __future__ import annotations

import pytest

from meridian.api.public.envelope import INVALID_QUERY, PublicError
from meridian.api.public.pagination import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    decode_cursor,
    encode_cursor,
    page_request,
    trim_overfetch,
)

SORT_KEYS = [
    ("st_abc123",),
    ("2026-08-13T09:31:02.123456+00:00", "pass_0001"),
    ("st_with-dash_and.dot",),
    ("",),
    ("Meridian simulator — station 001",),
]
"""Keys the real orderings produce, plus two awkward ones.

The empty string is here because a nullable sort column renders as one, and the
non-ASCII name because `operator` is free text a station chose.
"""


@pytest.mark.parametrize("sort_key", SORT_KEYS)
def test_a_sort_key_survives_the_round_trip(sort_key: tuple[str, ...]) -> None:
    """The whole contract of the cursor, in one assertion."""
    assert decode_cursor(encode_cursor(sort_key)) == sort_key


@pytest.mark.parametrize("sort_key", SORT_KEYS)
def test_a_cursor_is_safe_to_put_in_a_url(sort_key: tuple[str, ...]) -> None:
    """No padding and no character a query string would have to escape.

    A cursor that needed escaping would still work, and would produce support
    questions from anyone constructing a request by hand in a terminal.
    """
    cursor = encode_cursor(sort_key)

    assert "=" not in cursor
    assert "+" not in cursor
    assert "/" not in cursor


def test_a_composite_key_does_not_collapse_into_one_part() -> None:
    """`passes` is ordered by `aos asc, id asc`, so both parts must come back.

    Losing the tiebreaker would drop every pass sharing a second with the last
    one on the page — silently, and only when two passes happened to collide.
    """
    assert decode_cursor(encode_cursor(("2026-08-13", "pass_0001"))) == (
        "2026-08-13",
        "pass_0001",
    )


@pytest.mark.parametrize(
    "cursor",
    [
        "not-base64!!",  # characters outside the alphabet
        "\x00",  # a control character, which base64 would otherwise discard
        "e30",  # valid base64 of "{}" — decodes, but carries no format marker
        "c3RfYWJjMTIz",  # valid base64 of a bare "st_abc123", no marker either
        "a",  # too short to be base64 at all
        "",  # the empty string, which a caller may send for "no cursor"
    ],
)
def test_a_cursor_this_platform_did_not_issue_is_refused(cursor: str) -> None:
    """Refused, not quietly treated as page one.

    The last three matter most: each is well-formed base64 that decodes cleanly,
    and only the format marker separates them from a real cursor. Without it a
    caller who pasted the wrong value would get a plausible page rather than an
    error, and a dashboard looping on a cursor it invented would page forever.
    """
    with pytest.raises(PublicError) as excinfo:
        decode_cursor(cursor)

    assert excinfo.value.code == INVALID_QUERY
    assert excinfo.value.status == 400


def test_a_truncated_cursor_yields_a_wrong_position_not_an_error() -> None:
    """A known limitation, pinned so it is a decision rather than a surprise.

    The marker sits at the front, so a cursor cut to a multiple of four
    characters still decodes and still looks like ours — with a shortened key.
    The caller gets a page from the wrong position, which the next request
    corrects. Catching this would need a checksum, and a cursor names a position
    in a public list rather than granting anything, so there is nothing to
    protect. If a checksum is ever added, this test is what should change.
    """
    truncated = encode_cursor(("st_abc123",))[:-3]

    assert decode_cursor(truncated) == ("st_abc1",)


def test_the_page_request_defaults_to_one_screen_and_no_cursor() -> None:
    """What an endpoint sees when a caller passes no query parameters at all."""
    request = page_request()

    assert request.limit == DEFAULT_LIMIT
    assert request.cursor_key is None


def test_the_page_request_decodes_the_cursor_it_was_given() -> None:
    """Decoding happens once, in the dependency, not in every endpoint."""
    request = page_request(limit=10, cursor=encode_cursor(("st_abc123",)))

    assert request.limit == 10
    assert request.cursor_key == ("st_abc123",)


def test_a_full_fetch_reports_that_another_page_follows() -> None:
    """The extra row is the evidence, and it is not served."""
    fetched = [f"row-{index}" for index in range(4)]

    page = trim_overfetch(fetched, limit=3)

    assert page.rows == ["row-0", "row-1", "row-2"]
    assert page.has_more_rows is True


def test_an_exactly_full_page_is_still_the_last_page() -> None:
    """The case that makes the overfetch worth doing.

    A total that is an exact multiple of the limit is where "a full page means
    there is more" guesses wrong, and offers a cursor onto nothing.
    """
    fetched = [f"row-{index}" for index in range(3)]

    page = trim_overfetch(fetched, limit=3)

    assert page.rows == fetched
    assert page.has_more_rows is False


def test_no_rows_is_a_page_rather_than_an_absence() -> None:
    """An empty list is a legitimate answer — a fleet with no stations yet."""
    page = trim_overfetch([], limit=DEFAULT_LIMIT)

    assert page.rows == []
    assert page.has_more_rows is False


def test_the_maximum_limit_is_the_documented_one() -> None:
    """Pinned so that raising it becomes a deliberate edit with a reason.

    200 is a ceiling on what one query costs the Pi, and a number that drifts
    upward one convenience at a time stops being a ceiling.
    """
    assert MAX_LIMIT == 200
    assert DEFAULT_LIMIT == 50
