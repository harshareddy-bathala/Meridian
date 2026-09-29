"""Per-endpoint rate limits in the process (D-202).

MSP's heartbeat and observations are limited by the station's bearer token, so a
station is one caller wherever its requests come from. Every MSP request and
every public API request is also limited by the caller's address, which bounds
anyone rotating tokens. ``/``, ``/assets/``, ``/healthz`` and ``/metrics`` are
not limited.

A refused request is answered before routing, with MSP §6's ``rate_limited`` on
an MSP path or the public API's ``rate_limited`` on ``/api/v1``, and a
``Retry-After`` header. Like :mod:`meridian.api.request_limits`, this reads the
request line and headers only, never the body, and touches no database: a token
is limited whether or not it is valid, which is what spares the lookup.

The limiter is built by the application's lifespan from ``Settings`` and read
from ``app.state`` per request, so a process with ``RATE_LIMITS=off`` — or a
test that never started the lifespan — has none and passes everything through.

Reference: docs/MSP-SPEC.md §6; docs/DECISIONS.md D-051, D-088, D-202.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Iterable
from typing import cast

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from meridian.api.errors import RATE_LIMITED, error_response, public_error_response
from meridian.api.public.envelope import RATE_LIMITED as PUBLIC_RATE_LIMITED
from meridian.api.public.envelope import is_public_surface
from meridian.api.token_bucket import BucketStore, Clock, Rate, retry_after_s

__all__ = [
    "HEARTBEAT_RATE",
    "MSP_CLIENT_RATE",
    "OBSERVATIONS_RATE",
    "PUBLIC_CLIENT_RATE",
    "RateLimitMiddleware",
    "RateLimiter",
]

_log = logging.getLogger(__name__)

HEARTBEAT_RATE = Rate(capacity=6, per_second=1 / 10)
"""Per bearer token. MSP §4.2's 30 s heartbeat is 2 a minute; 6 allows retries."""

OBSERVATIONS_RATE = Rate(capacity=20, per_second=1 / 6)
"""Per bearer token. A queue flushed after an outage drains at 10 a minute."""

MSP_CLIENT_RATE = Rate(capacity=300, per_second=10)
"""Per client. A 50-station simulated fleet on one host fits inside it."""

PUBLIC_CLIENT_RATE = Rate(capacity=50, per_second=5)
"""Per client on ``/api/v1``: the edge rule's own 50 per 10 s (D-088)."""

_MSP_PREFIX = "/msp/"
_TOKEN_ENDPOINTS = {"/heartbeat": "heartbeat", "/observations": "observations"}
_BEARER = b"bearer "
_UNKNOWN_CLIENT = "unknown"


class RateLimiter:
    """The four buckets of D-202, and which of them a request draws on."""

    def __init__(
        self, *, clock: Clock = time.monotonic, client_header: str = ""
    ) -> None:
        """Create empty buckets.

        Args:
            clock: Seconds, monotonic. A test passes a variable.
            client_header: Lower-cased name of the header that carries the
                caller's address, or empty to use the peer address.
        """
        self._client_header = client_header.encode("latin-1")
        self.stores = {
            "heartbeat": BucketStore(HEARTBEAT_RATE, clock=clock),
            "observations": BucketStore(OBSERVATIONS_RATE, clock=clock),
            "msp": BucketStore(MSP_CLIENT_RATE, clock=clock),
            "public": BucketStore(PUBLIC_CLIENT_RATE, clock=clock),
        }

    def admit(self, scope: Scope) -> float:
        """Take a token for this request; return 0, or the seconds to wait.

        A request draws on one to three buckets, and takes from them only when
        every one of them has a token, so a refused request costs nothing.
        """
        charges = self._charges(scope)
        wait_s = max(
            (self.stores[name].wait_s(key) for name, key in charges), default=0.0
        )
        if wait_s > 0:
            return wait_s
        for name, key in charges:
            self.stores[name].take(key)
        return 0.0

    def _charges(self, scope: Scope) -> list[tuple[str, str]]:
        path = cast(str, scope["path"])
        headers = _headers(scope)
        if is_public_surface(path):
            return [("public", self._client(scope, headers))]
        if not path.startswith(_MSP_PREFIX):
            return []
        charges = [("msp", self._client(scope, headers))]
        endpoint = _token_endpoint(path)
        token = _bearer_token(headers)
        if endpoint is not None and token is not None:
            # The hash, never the token: this process then holds no credential
            # it did not already have (D-202).
            charges.append((endpoint, hashlib.sha256(token).hexdigest()))
        return charges

    def _client(self, scope: Scope, headers: Iterable[tuple[bytes, bytes]]) -> str:
        if self._client_header:
            for name, value in headers:
                if name == self._client_header and value.strip():
                    return value.split(b",")[0].strip().decode("latin-1")
        peer = scope.get("client")
        if peer is None:
            return _UNKNOWN_CLIENT
        return cast(str, peer[0])


def _token_endpoint(path: str) -> str | None:
    """Which per-token bucket an MSP path draws on, if any."""
    for suffix, name in _TOKEN_ENDPOINTS.items():
        if path.endswith(suffix):
            return name
    return None


def _headers(scope: Scope) -> list[tuple[bytes, bytes]]:
    return cast(list[tuple[bytes, bytes]], scope["headers"])


def _bearer_token(headers: Iterable[tuple[bytes, bytes]]) -> bytes | None:
    for name, value in headers:
        if name == b"authorization" and value[: len(_BEARER)].lower() == _BEARER:
            return value[len(_BEARER) :].strip()
    return None


def _refusal(path: str, wait_s: float) -> JSONResponse:
    seconds = retry_after_s(wait_s)
    message = f"Too many requests; retry after {seconds} s."
    if is_public_surface(path):
        response = public_error_response(PUBLIC_RATE_LIMITED, message)
    else:
        response = error_response(RATE_LIMITED, message)
    response.headers["Retry-After"] = str(seconds)
    return response


class RateLimitMiddleware:
    """Refuses a request whose buckets are empty, before anything else runs."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap ``app``, which is called only for requests the limiter admits."""
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Serve one request, or refuse it with 429."""
        limiter = _limiter(scope)
        if scope["type"] != "http" or limiter is None:
            await self._app(scope, receive, send)
            return
        wait_s = limiter.admit(scope)
        if wait_s == 0:
            await self._app(scope, receive, send)
            return
        path = cast(str, scope["path"])
        _log.debug("rate limited a request to %s for %.1f s", path, wait_s)
        await _refusal(path, wait_s)(scope, receive, send)


def _limiter(scope: Scope) -> RateLimiter | None:
    """The application's limiter, set by its lifespan, or ``None``."""
    app = scope.get("app")
    state = getattr(app, "state", None)
    limiter = getattr(state, "rate_limiter", None)
    return limiter if isinstance(limiter, RateLimiter) else None
