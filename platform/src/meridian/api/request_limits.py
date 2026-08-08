"""MSP §6's request size limits, enforced before a body is read.

MSP §6 tabulates a cap per endpoint and requires that an oversized request is
rejected as ``malformed`` **before the body is parsed**; D-028 records that this
is enforced as middleware, ahead of JSON parsing, so an oversized body is never
allocated. This module is that middleware.

It is installed by :func:`meridian.api.app.create_app` ahead of routing and ahead
of the handlers :func:`meridian.api.errors.install_error_handlers` registers, so
it runs before any route, dependency or validator sees the request. Starlette's
own ``ServerErrorMiddleware`` still wraps it, which is why this module answers by
sending a response rather than by raising.

It holds **no business logic, touches no database, and never reads the request
body** — reading the body is the thing it exists to prevent. It decides from the
request line and headers alone, which is the only information available before a
body arrives.

Reference: docs/MSP-SPEC.md §6 "Request limits", docs/DECISIONS.md D-028, D-050.
"""

from __future__ import annotations

from collections.abc import Iterable

from starlette.types import ASGIApp, Receive, Scope, Send

from meridian.api.errors import MALFORMED, error_response

__all__ = [
    "OBSERVATIONS_BODY_LIMIT_BYTES",
    "STANDARD_BODY_LIMIT_BYTES",
    "RequestSizeLimitMiddleware",
    "is_declared_size_acceptable",
    "limit_for_path_bytes",
]

_KIB = 1024

STANDARD_BODY_LIMIT_BYTES = 64 * _KIB
"""MSP §6's cap for the ``register``, ``heartbeat`` and ``time`` bodies.

Taken from the specification's table, not chosen here. It also serves as the
default for any path §6 does not name — a request to ``/healthz`` has no business
carrying more than a registration does, and defaulting to the smaller of the two
caps means a path added later is capped from its first commit rather than from
the commit somebody remembers to add it to the table below.
"""

OBSERVATIONS_BODY_LIMIT_BYTES = 256 * _KIB
"""MSP §6's cap for the ``observations`` body.

Larger because that body carries the Doppler sample array: D-032 caps it at 512
entries, which at roughly 50 bytes per sample is about 25 KiB, and D-029 sizes
the rest of the body against this figure rather than the other way round.
"""

_OBSERVATIONS_PATH_SUFFIX = "/observations"

_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})
"""The methods whose requests are checked.

``GET /msp/v0/time`` is the only MSP endpoint outside this set. A GET carries no
body and sends no ``Content-Length``, so requiring one there would reject every
correct request to the one endpoint a station with no credentials can still call.
"""


def limit_for_path_bytes(path: str) -> int:
    """The largest body MSP §6 permits at ``path``.

    Args:
        path: The request path, e.g. ``/msp/v0/register``.

    Returns:
        The cap in bytes. Matching on a path suffix rather than the full string
        keeps this correct if the router prefix moves with a major version —
        ``/msp/v1/observations`` gets the observations cap without an edit here.
    """
    if path.endswith(_OBSERVATIONS_PATH_SUFFIX):
        return OBSERVATIONS_BODY_LIMIT_BYTES
    return STANDARD_BODY_LIMIT_BYTES


def is_declared_size_acceptable(content_length: str | None, limit_bytes: int) -> bool:
    """Whether a request declaring ``content_length`` may be read at all.

    Args:
        content_length: The raw ``Content-Length`` header value as the client
            sent it, or ``None`` when the request carried no such header.
        limit_bytes: The cap for the endpoint being addressed, from
            :func:`limit_for_path_bytes`.

    Returns:
        True only when the header is present, parses as a non-negative integer,
        and is within ``limit_bytes``.

    Note:
        An absent header is refused rather than waved through. A chunked body
        declares no size, so there would be nothing to check before parsing —
        and MSP §6 requires the check to happen before parsing, which makes
        "allow it and count as we go" a different rule than the one written
        down. MSP §8 binds JSON bodies over HTTP/1.1, where every client sends
        ``Content-Length`` for a JSON body, so refusing the undeclared case
        costs no real station and closes the only route past this check. See
        D-050.
    """
    if content_length is None:
        return False
    try:
        declared_bytes = int(content_length)
    except ValueError:
        # A header that is not a number is not a size we can honour, and libpq
        # -style leniency here would mean `Content-Length: 64K` reads as absent
        # and passes. Reject it as the malformed request it is.
        return False
    return 0 <= declared_bytes <= limit_bytes


def _over_limit_message(limit_bytes: int) -> str:
    """The rejection text: states the limit, and nothing about the request."""
    return f"Request body must declare a Content-Length of at most {limit_bytes} bytes."


def _content_length_header(scope: Scope) -> str | None:
    """The request's ``Content-Length`` as sent, or ``None`` if it sent none."""
    # Annotated rather than read straight out of the scope: Starlette types the
    # ASGI scope as a mapping to Any, and an unannotated `value` would make
    # `value.decode(...)` an Any that mypy has no way to check — which is the
    # case `disallow_any_explicit` exists to stop (D-044). The shape is the ASGI
    # specification's own: header names lowercased, both halves raw bytes.
    headers: Iterable[tuple[bytes, bytes]] = scope["headers"]
    for name, value in headers:
        if name == b"content-length":
            # latin-1, which is the encoding the ASGI specification gives header
            # bytes. The value is digits in every non-hostile case; decoding
            # rather than assuming is what keeps a hostile one from raising here
            # instead of being rejected below.
            return value.decode("latin-1")
    return None


class RequestSizeLimitMiddleware:
    """Rejects a request whose declared body exceeds MSP §6's cap for its path.

    Pure ASGI rather than Starlette's ``BaseHTTPMiddleware``: the base class
    builds a ``Request`` and runs the downstream application in a separate task
    to give it a response object, all of which is machinery for middleware that
    wants to inspect a *response*. This one answers from the request headers and
    either forwards or refuses, so the plain three-argument form is both smaller
    and the one D-028's "ahead of JSON parsing" describes.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Wrap ``app``, which is called only for requests within their limit."""
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Serve one request, refusing it if its declared body is too large."""
        if scope["type"] != "http" or scope["method"] not in _BODY_METHODS:
            await self._app(scope, receive, send)
            return

        limit_bytes = limit_for_path_bytes(scope["path"])
        if is_declared_size_acceptable(_content_length_header(scope), limit_bytes):
            await self._app(scope, receive, send)
            return

        # Sent from here rather than raised, because this middleware sits outside
        # the exception handlers `install_error_handlers` registers — an
        # exception raised at this depth becomes a bare 500 in Starlette's
        # outermost handler, which is neither MSP §6's shape nor its status.
        response = error_response(MALFORMED, _over_limit_message(limit_bytes))
        await response(scope, receive, send)
