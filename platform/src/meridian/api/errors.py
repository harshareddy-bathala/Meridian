"""The one shape every error response takes, on both surfaces.

MSP §6 defines exactly one error body and no other, and `docs/DECISIONS.md` D-004
records why it is two flat string fields: a microcontroller client extracts both
with a substring scan and never needs a JSON tree walker. This module owns that
shape, MSP's eight stable codes, and the exception handlers that make FastAPI
produce it instead of its own.

The handlers are installed on the *application*, so they govern the public read
API as well. Its vocabulary is a separate table in
`meridian.api.public.envelope` — same body, different codes (D-084) — and the two
handlers that catch failures nobody raised deliberately pick their words from
whichever table the request path belongs to.

It sits under `meridian.api` and is imported by every MSP route. It holds **no
business logic and touches no database** — it maps a failure onto a code and a
status, nothing more.

**No response built here carries anything but a stable code and a fixed
message.** That matters most in the unhandled-exception handler: ``str(exc)`` on
a psycopg failure carries the failing SQL and often the connection string, and on
a validation failure it carries the submitted value — which for ``register`` is
an invite token. The detail goes to the log instead, where the operator can read
it and a station cannot.

Reference: docs/MSP-SPEC.md §6, docs/DECISIONS.md D-004.
"""

from __future__ import annotations

import logging
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from meridian.api.public.envelope import (
    INVALID_QUERY,
    METHOD_NOT_ALLOWED,
    NOT_FOUND,
    STATUS_FOR_PUBLIC_CODE,
    PublicError,
    is_public_surface,
)
from meridian.api.public.envelope import SERVER_ERROR as PUBLIC_SERVER_ERROR

__all__ = [
    "INVALID_INVITE",
    "MALFORMED",
    "NOT_OWNER",
    "RATE_LIMITED",
    "SERVER_ERROR",
    "STATUS_FOR_CODE",
    "UNAUTHORIZED",
    "UNKNOWN_ASSIGNMENT",
    "UNSUPPORTED_VERSION",
    "MspError",
    "error_body",
    "error_response",
    "install_error_handlers",
    "no_such_endpoint_response",
    "public_error_response",
]

_log = logging.getLogger(__name__)

# The eight stable codes of MSP §6, with the status each is served at. Named
# constants rather than bare strings because these are the only field a client may
# branch on: a typo in one is a client that never matches, and it is silent.
INVALID_INVITE = "invalid_invite"
UNAUTHORIZED = "unauthorized"
NOT_OWNER = "not_owner"
UNKNOWN_ASSIGNMENT = "unknown_assignment"
MALFORMED = "malformed"
UNSUPPORTED_VERSION = "unsupported_version"
# Defined because MSP §6 defines it, and deliberately never raised: no limiter
# exists, and D-051 records why building one before the deployment exists would
# produce a control that fails open for the attacker and closed for the operator.
RATE_LIMITED = "rate_limited"
SERVER_ERROR = "server_error"

STATUS_FOR_CODE = {
    INVALID_INVITE: HTTPStatus.FORBIDDEN,
    UNAUTHORIZED: HTTPStatus.UNAUTHORIZED,
    NOT_OWNER: HTTPStatus.FORBIDDEN,
    UNKNOWN_ASSIGNMENT: HTTPStatus.NOT_FOUND,
    MALFORMED: HTTPStatus.BAD_REQUEST,
    # 400, not 426 Upgrade Required or 505. MSP §6's table says 400, and a
    # microcontroller matching on the code does not need a second status to learn.
    UNSUPPORTED_VERSION: HTTPStatus.BAD_REQUEST,
    RATE_LIMITED: HTTPStatus.TOO_MANY_REQUESTS,
    SERVER_ERROR: HTTPStatus.INTERNAL_SERVER_ERROR,
}
"""Every code MSP §6 defines, and the status it is served at.

The mapping is one-way and total. A code absent from this table cannot be sent,
which is what stops an endpoint inventing a ninth code that no published client
knows how to treat.
"""

_CODE_FOR_ROUTING_STATUS: dict[int, str] = {
    HTTPStatus.NOT_FOUND: NOT_FOUND,
    HTTPStatus.METHOD_NOT_ALLOWED: METHOD_NOT_ALLOWED,
}
"""The two ways a request fails before any route sees it (D-090).

Keyed by plain ``int`` because that is what Starlette puts on the exception.
``HTTPStatus`` is an ``IntEnum``, so the members above are usable as keys and
read better than bare numbers at the point where the mapping is written.

Starlette's router raises these two and nothing else, and no module in this
platform raises ``HTTPException`` itself — so these are the whole domain of the
routing handler rather than the part of it worth naming.
"""

_MESSAGE_FOR_ROUTING_CODE = {
    NOT_FOUND: "No such endpoint.",
    METHOD_NOT_ALLOWED: "That method is not allowed on this endpoint.",
}
"""Fixed text, saying nothing about what was asked for.

Echoing the path back would reflect an arbitrary caller-supplied string into a
response body, which is a habit worth not having even where — as here — the
response is JSON and nothing renders it as markup.
"""

SERVER_ERROR_MESSAGE = "Internal error."
"""The only text an unhandled exception may produce.

Fixed, and deliberately uninformative. `str(exc)` on a psycopg failure carries the
failing SQL and often the connection string; on a validation failure it carries the
submitted value, which for `register` is an invite token. The detail belongs in the
platform's log, where the operator can see it and a station cannot.
"""


class MspError(Exception):
    """A failure that maps onto one of MSP §6's stable codes.

    Raised by route handlers and services; converted to a response by
    :func:`install_error_handlers`. Carrying the code rather than the status means
    a caller states what went wrong and never has to remember which HTTP number
    the specification pairs it with.
    """

    def __init__(self, code: str, message: str) -> None:
        """Build an error for one of MSP §6's codes.

        Args:
            code: A stable code from ``STATUS_FOR_CODE``. Anything else is a
                programming error and raises immediately, because a code the
                table does not know would otherwise reach a station as a 500.
            message: Human-readable text for the station's log. Never parsed by
                a client, and must never contain a token, SQL or a stack trace.
        """
        if code not in STATUS_FOR_CODE:
            raise ValueError(f"{code!r} is not an MSP §6 error code")
        super().__init__(message)
        self.code = code
        self.message = message

    @property
    def status(self) -> HTTPStatus:
        """The HTTP status MSP §6 pairs with this code."""
        return STATUS_FOR_CODE[self.code]


def error_body(code: str, message: str) -> dict[str, str]:
    """The two-field body, and nothing else.

    Args:
        code: A stable code from MSP §6.
        message: Human text for logs.

    Returns:
        Exactly ``{"error": ..., "message": ...}``. Two flat strings, no nesting
        and no optional members — the shape a substring scan can read (D-004).
    """
    return {"error": code, "message": message}


def error_response(code: str, message: str) -> JSONResponse:
    """An MSP error as a response, at the status §6 pairs with ``code``."""
    return JSONResponse(error_body(code, message), status_code=STATUS_FOR_CODE[code])


def public_error_response(code: str, message: str) -> JSONResponse:
    """A public API error, at the status its own table pairs with ``code``.

    The same body shape as :func:`error_response` and a different table — which
    is the whole of D-084 in two functions.
    """
    return JSONResponse(
        error_body(code, message), status_code=STATUS_FOR_PUBLIC_CODE[code]
    )


def no_such_endpoint_response() -> JSONResponse:
    """The 404 this platform serves for any URL it does not route.

    One function rather than two literals, because two callers depend on being
    indistinguishable: the routing handler, and ``/metrics`` refusing a scrape
    that presented no valid token (D-087). That refusal's entire protection is
    that it cannot be told apart from a path that was never there, and a message
    that drifted in one place and not the other would give it away.
    """
    return public_error_response(NOT_FOUND, _MESSAGE_FOR_ROUTING_CODE[NOT_FOUND])


def install_error_handlers(app: FastAPI) -> None:
    """Make every failure leave the application in the two-field body.

    Five handlers, in three groups: the two exceptions the platform raises
    deliberately, the two that catch a failure nobody raised, and the one for a
    request that never reached a route at all.

    Installed on the application rather than on a router, so the last three
    govern every path the platform serves — which is why two of them ask which
    surface a failing request belongs to (D-084), and why the third deliberately
    does not (D-090).

    Args:
        app: The application to install onto.
    """
    _install_raised_error_handlers(app)
    _install_fallback_handlers(app)
    _install_routing_handlers(app)


def _install_raised_error_handlers(app: FastAPI) -> None:
    """The two exceptions the platform raises on purpose, one per surface."""

    @app.exception_handler(MspError)
    async def _handle_msp_error(_request: Request, exc: Exception) -> JSONResponse:
        # Starlette types every handler's second argument as Exception, so the
        # narrowing is done here rather than with an assert — an assert would
        # vanish under `python -O` and take the type guarantee with it.
        if not isinstance(exc, MspError):  # pragma: no cover — registered for MspError
            raise exc
        return error_response(exc.code, exc.message)

    @app.exception_handler(PublicError)
    async def _handle_public_error(_request: Request, exc: Exception) -> JSONResponse:
        if not isinstance(exc, PublicError):  # pragma: no cover — registered for it
            raise exc
        return public_error_response(exc.code, exc.message)


def _install_routing_handlers(app: FastAPI) -> None:
    """The request that never reached a route: a bad path, or a bad method.

    Replaces FastAPI's built-in handler, which answers ``{"detail": ...}`` — a
    third body shape in a platform that otherwise has one, and the shape a client
    author meets first, because a typo'd URL is the earliest mistake anyone makes.
    """

    @app.exception_handler(StarletteHTTPException)
    async def _handle_routing_failure(
        _request: Request, exc: Exception
    ) -> JSONResponse:
        if not isinstance(exc, StarletteHTTPException):  # pragma: no cover
            raise exc
        # No surface guard here, unlike the two handlers above, and that is
        # D-090's whole argument rather than an oversight: a request that
        # matched no route never became an MSP operation, so MSP §6 has nothing
        # to say about it and stays closed. Both surfaces answer in the same
        # words. The status comes from the code's own table, so the two can
        # never disagree even if Starlette raises a status not listed here.
        code = _CODE_FOR_ROUTING_STATUS.get(exc.status_code, NOT_FOUND)
        return public_error_response(code, _MESSAGE_FOR_ROUTING_CODE[code])


def _install_fallback_handlers(app: FastAPI) -> None:
    """The two nobody raised deliberately, each answering in the right words."""

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(request: Request, exc: Exception) -> JSONResponse:
        # FastAPI's default is a 422 with a nested `detail` array of objects,
        # which is three shapes MSP does not have: a status it does not define, an
        # array, and nesting. Replacing it is why this handler exists.
        #
        # The message is generic rather than derived from `exc`, because the
        # validation detail echoes the submitted value — and on `register` the
        # submitted value includes the invite token.
        _log.info("rejected a malformed request: %s", exc)
        if is_public_surface(request.url.path):
            # A read endpoint has no body to be malformed about; what a caller
            # got wrong is a query parameter or a cursor.
            return public_error_response(
                INVALID_QUERY, "Request query failed validation."
            )
        return error_response(MALFORMED, "Request body failed validation.")

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # Logged with the traceback for the operator, returned as a fixed string
        # to the caller. A 500 that explains itself to the network is a 500 that
        # tells an attacker the schema.
        _log.exception("unhandled exception serving a request", exc_info=exc)
        if is_public_surface(request.url.path):
            # `PUBLIC_SERVER_ERROR`, not MSP's constant. The two tables spell
            # this code the same way today, so the responses are byte-identical
            # — but looking the public status up under the MSP name is what
            # would break the moment one table was respelled, and it would break
            # as a KeyError inside the handler of last resort.
            return public_error_response(PUBLIC_SERVER_ERROR, SERVER_ERROR_MESSAGE)
        return error_response(SERVER_ERROR, SERVER_ERROR_MESSAGE)
