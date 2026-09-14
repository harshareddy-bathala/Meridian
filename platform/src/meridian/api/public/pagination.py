"""Keyset pagination for the public list endpoints.

Every list endpoint takes the same two query parameters — a `limit` and an opaque
`cursor` — and the cursor carries the sort key of the last row already served.
The next page is "everything ordered after this key", which is a different
question from "everything after the ten-thousandth row" and has a different
answer on a feed that is still being written to.

That difference is the reason for the scheme. `observations` and `heartbeats` are
hypertables under a compression policy: an offset page decompresses and discards
everything it skips, and — worse — a row inserted while a reader pages through
shifts every later row by one, so the reader sees one twice and never sees
another. See `docs/DECISIONS.md` D-085.

This module is pure. It parses two parameters, encodes and decodes a string, and
trims a list; it builds no SQL and touches no database. The store layer applies
the key in its `where` clause, and each endpoint says which columns the key is
made of, because only it knows its own `order by`.

Reference: docs/DECISIONS.md D-085, D-084.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Annotated, Generic, TypeVar

from fastapi import Query

from meridian.api.public.envelope import INVALID_QUERY, PublicError

__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "PageRequest",
    "TrimmedPage",
    "decode_cursor",
    "encode_cursor",
    "page_request",
    "single_cursor_value",
    "trim_overfetch",
]

RowT = TypeVar("RowT")

DEFAULT_LIMIT = 50
"""Rows returned when a caller does not ask for a number.

Fifty because that is the simulated fleet's size, so the station map arrives in
one page at the scale the project demonstrates it at.
"""

MAX_LIMIT = 200
"""The most rows one request may ask for.

A ceiling on what a single query costs the Pi, which is running Postgres and the
platform on the same NVMe as the receiver.
"""

_CURSOR_SEPARATOR = "\x1f"
"""ASCII unit separator, joining the parts of a composite sort key.

A control character rather than a comma or a pipe because it cannot occur in any
value the key is built from — station ids, timestamps and generated ids are all
printable — so no part ever needs escaping and no value can forge a boundary.
"""

_CURSOR_FORMAT = "1"
"""Leading part of every cursor, checked on the way back in.

Without it any string that happens to be valid base64 of valid text decodes to
*some* sort key, and a caller who pasted the wrong value would get a plausible
page instead of an error. It is a format marker, not a signature: a cursor names
a position in a public ordered list and grants nothing, so there is nothing here
worth authenticating. If the packing ever changes, this is what lets the old
shape be recognised rather than misread.
"""


@dataclass(frozen=True, slots=True)
class PageRequest:
    """The two query parameters every public list endpoint accepts."""

    limit: int
    """How many rows to return. Already range-checked, 1 to ``MAX_LIMIT``."""

    cursor_key: tuple[str, ...] | None
    """The sort key of the last row the caller already has, or None for page one.

    A tuple because an ordering may need more than one column: `passes` is
    ordered by `aos asc, id asc`, and paging on `aos` alone would drop every pass
    sharing a second with the last one on the page.
    """


@dataclass(frozen=True, slots=True)
class TrimmedPage(Generic[RowT]):
    """One page, and whether the caller should ask for another."""

    rows: list[RowT]
    has_more_rows: bool


def encode_cursor(sort_key: tuple[str, ...]) -> str:
    """Pack one row's sort key into an opaque cursor.

    Args:
        sort_key: The ordered column values that identify the last row served,
            already rendered as text.

    Returns:
        A URL-safe string. Opaque on purpose: a caller that took it apart would
        be depending on an endpoint's `order by`, which is then no longer free to
        change.

    Note:
        Base64 without padding. ``=`` is legal in a query string but invites the
        belief that something was cut off, and restoring it on decode is one
        line.
    """
    packed = _CURSOR_SEPARATOR.join((_CURSOR_FORMAT, *sort_key)).encode("utf-8")
    return base64.urlsafe_b64encode(packed).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[str, ...]:
    """Unpack a cursor back into the sort key it carries.

    Args:
        cursor: The value a caller passed back from a previous response.

    Returns:
        The column values, in the order :func:`encode_cursor` received them.

    Raises:
        PublicError: ``invalid_query`` when the cursor does not decode, or
            decodes to something without this platform's format marker. Saying so
            beats silently serving page one, which would look like the list had
            reset and would make a dashboard looping on the cursor page forever.

    Note:
        Decoded with ``validate=True``. Python's default discards characters
        outside the base64 alphabet rather than objecting, which would turn a
        cursor with a stray character into a *different, valid* cursor.

        **The marker catches malformed cursors, not corrupted ones.** A cursor
        truncated to a multiple of four characters still decodes, keeps its
        marker, and yields a shorter key — so the caller gets a page from the
        wrong position rather than an error. That is deliberate: catching it
        needs a checksum, and the cost of being wrong is one misplaced page in a
        public list, which the next request corrects. A cursor grants no
        authority, so there is nothing here to protect against a forger.
    """
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        # `b64decode` with `altchars`, not `urlsafe_b64decode`: the urlsafe
        # wrapper takes no `validate` argument, and validation is the whole point
        # of decoding this way.
        raw = base64.b64decode(padded, altchars=b"-_", validate=True)
        unpacked = raw.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise PublicError(INVALID_QUERY, "cursor is not a valid cursor.") from exc

    marker, _, key = unpacked.partition(_CURSOR_SEPARATOR)
    if marker != _CURSOR_FORMAT:
        raise PublicError(INVALID_QUERY, "cursor is not a valid cursor.")
    return tuple(key.split(_CURSOR_SEPARATOR))


def page_request(
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> PageRequest:
    """FastAPI dependency supplying both pagination parameters as one value.

    Bundled rather than taken as two parameters on every endpoint, so a list
    route spends one argument on paging and keeps the rest for its own filters.

    Args:
        limit: Rows to return, injected from the query string. Out-of-range
            values are refused by FastAPI before this runs, and the shared
            handler turns that into ``invalid_query``.
        cursor: An opaque cursor from a previous response, absent on page one.

    Returns:
        The request, with the cursor already decoded.
    """
    return PageRequest(
        limit=limit,
        cursor_key=decode_cursor(cursor) if cursor is not None else None,
    )


def trim_overfetch(fetched: list[RowT], limit: int) -> TrimmedPage[RowT]:
    """Split a ``limit + 1`` fetch into a page and the answer to "any more?".

    Args:
        fetched: What the store returned when asked for one row more than the
            page needs.
        limit: The page size that was requested.

    Returns:
        The first ``limit`` rows, and whether the extra row came back.

    Note:
        Asking for one row too many is how "is there another page" becomes a
        fact rather than a guess. The alternative — offering a next cursor
        whenever a full page came back — advertises an empty page every time the
        total is an exact multiple of the limit.
    """
    return TrimmedPage(rows=fetched[:limit], has_more_rows=len(fetched) > limit)


def single_cursor_value(cursor_key: tuple[str, ...] | None) -> str | None:
    """The one value a single-column cursor carries, or ``None`` on page one.

    Endpoints that page by a row id rather than by the sort key itself (D-093)
    issue one-part cursors. A cursor with any other shape was issued by a
    different endpoint and is refused here, rather than being half-read.

    Raises:
        PublicError: ``invalid_query`` when the key does not have exactly one
            part.
    """
    if cursor_key is None:
        return None
    if len(cursor_key) != 1 or not cursor_key[0]:
        raise PublicError(INVALID_QUERY, "cursor is not a valid cursor.")
    return cursor_key[0]
