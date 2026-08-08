"""The one shape every MSP error response takes.

MSP §6 defines exactly one error body and no other, and `docs/DECISIONS.md` D-004
records why it is two flat string fields: a microcontroller client extracts both
with a substring scan and never needs a JSON tree walker. This module owns that
shape, the eight stable codes behind it, and the exception handlers that make
FastAPI produce it instead of its own.

It sits under `meridian.api` and is imported by every MSP route. It holds **no
business logic and touches no database** — it maps a failure onto a code and a
status, nothing more.

**Nothing here may put a secret in a response.** Bearer tokens, invite tokens, SQL
and stack traces are all things a station must never receive, and the handler for
an unhandled exception is the one most likely to leak them by accident, so it
never reports anything but a fixed string.

Reference: docs/MSP-SPEC.md §6, docs/DECISIONS.md D-004.
"""

from __future__ import annotations

import logging
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

__all__ = [
    "MALFORMED",
    "RATE_LIMITED",
    "SERVER_ERROR",
    "STATUS_FOR_CODE",
    "UNAUTHORIZED",
    "UNSUPPORTED_VERSION",
    "MspError",
    "error_body",
    "error_response",
    "install_error_handlers",
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


def install_error_handlers(app: FastAPI) -> None:
    """Make every failure leave the application in MSP §6's shape.

    Three handlers, covering the three ways a request fails: one we raised, one
    the request body failed, and one nobody expected.

    Args:
        app: The application to install onto.
    """

    @app.exception_handler(MspError)
    async def _handle_msp_error(_request: Request, exc: Exception) -> JSONResponse:
        # Starlette types every handler's second argument as Exception, so the
        # narrowing is done here rather than with an assert — an assert would
        # vanish under `python -O` and take the type guarantee with it.
        if not isinstance(exc, MspError):  # pragma: no cover — registered for MspError
            raise exc
        return error_response(exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(_request: Request, exc: Exception) -> JSONResponse:
        # FastAPI's default is a 422 with a nested `detail` array of objects,
        # which is three shapes MSP does not have: a status it does not define, an
        # array, and nesting. Replacing it is why this handler exists.
        #
        # The message is generic rather than derived from `exc`, because the
        # validation detail echoes the submitted value — and on `register` the
        # submitted value includes the invite token.
        _log.info("rejected a malformed request: %s", exc)
        return error_response(MALFORMED, "Request body failed validation.")

    @app.exception_handler(Exception)
    async def _handle_unexpected(_request: Request, exc: Exception) -> JSONResponse:
        # Logged with the traceback for the operator, returned as a fixed string
        # to the station. A 500 that explains itself to the network is a 500 that
        # tells an attacker the schema.
        _log.exception("unhandled exception serving a request", exc_info=exc)
        return error_response(SERVER_ERROR, SERVER_ERROR_MESSAGE)
