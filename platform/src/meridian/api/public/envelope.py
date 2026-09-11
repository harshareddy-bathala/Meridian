"""The public API's error vocabulary, and what counts as a public path.

The read API answers failures in the same two-field body MSP §6 uses —
``{"error": ..., "message": ...}``, two flat strings, one error shape in the
repository — but with a different set of codes. MSP has no code for "no such
station", and it should not gain one: its table is a published protocol that
strangers implement, and widening it would put a code in the reference
implementation that the specification does not list.

`meridian.api.errors` installs its handlers on the *application*, not on the MSP
router, so it already governs every path the platform serves. That is why
:func:`is_public_surface` lives here: the shared handlers ask it which vocabulary
a failing request should be answered in.

This module imports nothing from the rest of the platform — not the router it
describes, not the error layer that consumes it. It is a table of codes and a
string comparison, so the endpoint modules can import it without any of them
having to think about import order.

Reference: docs/DECISIONS.md D-084, D-004; docs/MSP-SPEC.md §6.
"""

from __future__ import annotations

from http import HTTPStatus

__all__ = [
    "API_PREFIX",
    "INVALID_QUERY",
    "METHOD_NOT_ALLOWED",
    "NOT_FOUND",
    "SERVER_ERROR",
    "STATUS_FOR_PUBLIC_CODE",
    "PublicError",
    "is_public_surface",
]

API_PREFIX = "/api/v1"
"""Where the whole read surface lives (D-083).

Named here rather than beside the router because both the error layer and the
static dashboard mount need to say which paths belong to the API, and two
spellings of one prefix is how a mount silently swallows an endpoint.
"""

NOT_FOUND = "not_found"
INVALID_QUERY = "invalid_query"
METHOD_NOT_ALLOWED = "method_not_allowed"
SERVER_ERROR = "server_error"

STATUS_FOR_PUBLIC_CODE = {
    NOT_FOUND: HTTPStatus.NOT_FOUND,
    # `invalid_query`, not MSP's `malformed`: on this surface the thing a caller
    # gets wrong is a query parameter or a cursor, not a request body. The name
    # says where to look.
    INVALID_QUERY: HTTPStatus.BAD_REQUEST,
    METHOD_NOT_ALLOWED: HTTPStatus.METHOD_NOT_ALLOWED,
    SERVER_ERROR: HTTPStatus.INTERNAL_SERVER_ERROR,
}
"""Every code the public API may send, and the status it is served at.

Separate from MSP §6's table rather than an extension of it. ``server_error``
appears in both and means the same thing in both, which is a coincidence worth
not relying on: each surface looks its statuses up in its own table, so either
can change without silently moving the other.

Two of these are also the platform's answer on *any* path, MSP included.
``not_found`` and ``method_not_allowed`` describe a request that never reached an
operation — no route matched it — which is a transport-level fact rather than
anything MSP §6 has a code for. D-090 records why one table serves both ideas,
and that the doubling is the part of that decision most worth challenging.
"""


class PublicError(Exception):
    """A failure the public API can name.

    The counterpart to :class:`meridian.api.errors.MspError`, kept separate for
    the same reason the tables are: an endpoint that raised the wrong one would
    answer a dashboard in a protocol vocabulary, or a station in a vocabulary its
    specification never mentions.
    """

    def __init__(self, code: str, message: str) -> None:
        """Build an error for one of the public API's codes.

        Args:
            code: A code from ``STATUS_FOR_PUBLIC_CODE``. Anything else is a
                programming error and raises immediately, rather than reaching a
                reader as an unexplained 500.
            message: Human-readable text. Shown in a dashboard, so it may name
                the parameter at fault — but never a token, SQL or a stack trace.
        """
        if code not in STATUS_FOR_PUBLIC_CODE:
            raise ValueError(f"{code!r} is not a public API error code")
        super().__init__(message)
        self.code = code
        self.message = message

    @property
    def status(self) -> HTTPStatus:
        """The HTTP status this code is served at."""
        return STATUS_FOR_PUBLIC_CODE[self.code]


def is_public_surface(path: str) -> bool:
    """Whether a request path belongs to the public read API.

    Args:
        path: The request's URL path, as ``request.url.path`` gives it.

    Returns:
        True for ``/api/v1`` itself and anything beneath it.

    Note:
        The match is the prefix exactly, or the prefix followed by ``/`` — never
        a bare ``startswith``. ``/api/v1`` is a prefix of ``/api/v10``, and a
        future second version answering in the wrong vocabulary is the kind of
        bug that only shows up once ``v10`` exists to be confused with.
    """
    return path == API_PREFIX or path.startswith(f"{API_PREFIX}/")
