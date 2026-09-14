"""Count and time every HTTP request the platform answers.

Stage 3 asked for request counts by endpoint and status and for request latency,
and Stage 12 is where they arrive (D-109). This module is the ASGI middleware that
records both, outermost of all, so a request refused by the size limit before
routing is still counted.

**Labels stay bounded whatever a caller sends** (D-111). The endpoint label is a
*template* the application defines — ``/api/v1/stations/{station_id}`` — never the
path the caller typed, so no identifier becomes a label and a scan of random URLs
creates no series. A path that fits no template is labelled ``unrouted``, and a
method outside the standard set is labelled ``other``.

**Why the templates are matched here rather than read from the routing result.**
FastAPI 0.14x records the matched route in the ASGI scope, but for a route on an
included router that is the route as the router declared it — ``/time``, not
``/msp/v0/time`` — and the prefix is kept only in private scope keys. Matching the
path against the application's published templates uses public interfaces only,
and is tested against an included router so an upgrade that changes this is
caught.

It holds no business logic, touches no database and never reads a body.

Reference: docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md Stage 3 "Observability";
docs/DECISIONS.md D-109, D-111.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable

from prometheus_client import Counter, Histogram
from starlette.types import ASGIApp, Message, Receive, Scope, Send

__all__ = [
    "HTTP_REQUESTS",
    "HTTP_REQUEST_DURATION",
    "UNROUTED",
    "RequestMetricsMiddleware",
    "RouteTemplates",
    "method_label",
    "status_class_label",
    "templates_of",
]

UNROUTED = "unrouted"
"""The route label for a path that fits no template the application defines.

An unknown URL and a file served from the dashboard's static mount both land here.
One value rather than the raw path, because the raw path is whatever a caller
chose to type.
"""

_STANDARD_METHODS = frozenset(
    {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
)
"""RFC 9110's methods that any route here could plausibly answer.

Anything else is a probe or a mistake, and is labelled ``other`` rather than
spelled out.
"""

_SERVER_ERROR_STATUS = 500
"""The status recorded when the application raises before starting a response.

Starlette's outer ``ServerErrorMiddleware`` answers such a request with 500, so
that is what the caller receives.
"""

_ESCAPED_PARAMETER = re.compile(r"\\\{[^/]+?\\\}")
"""A ``{name}`` path parameter, as it appears after :func:`re.escape`."""

HTTP_REQUESTS = Counter(
    "meridian_http_requests",
    "HTTP requests answered, by route template, method and status class.",
    ["route", "method", "status_class"],
)

HTTP_REQUEST_DURATION = Histogram(
    "meridian_http_request_duration_seconds",
    "Time from receiving a request to finishing its response, by route template.",
    ["route"],
    # From a cached health check, a few milliseconds, to a heartbeat waiting on
    # the pool's five-second checkout timeout (`store/pool.py`). Wider buckets
    # would only describe requests that have already timed out.
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)


class RouteTemplates:
    """The path templates an application defines, matched most specific first."""

    def __init__(self, templates: Iterable[str]) -> None:
        """Compile each template once.

        Args:
            templates: Path templates such as ``/api/v1/stations/{station_id}``.
                Duplicates are ignored.

        Note:
            A template with fewer parameters is tried first, so a literal path
            such as ``/api/v1/stations/nearby`` would win over
            ``/api/v1/stations/{station_id}`` for the same request, as it does in
            routing.
        """
        ordered = sorted(set(templates), key=lambda one: (one.count("{"), one))
        self._compiled = tuple((one, _compile_template(one)) for one in ordered)

    def label_for(self, path: str) -> str:
        """The template ``path`` fits, or :data:`UNROUTED`."""
        for template, pattern in self._compiled:
            if pattern.fullmatch(path):
                return template
        return UNROUTED


def _compile_template(template: str) -> re.Pattern[str]:
    """A template as a regular expression; a parameter matches one path segment."""
    return re.compile(_ESCAPED_PARAMETER.sub("[^/]+", re.escape(template)))


def templates_of(app: object) -> RouteTemplates:
    """Every path template a FastAPI or Starlette application defines.

    Args:
        app: The application from the ASGI scope's ``app`` key.

    Returns:
        The OpenAPI document's paths, which carry every included router's prefix,
        plus any plain route's own path — the documentation pages and the
        dashboard's index, which are not in the schema.
    """
    templates: list[str] = []
    openapi = getattr(app, "openapi", None)
    if callable(openapi):
        schema = openapi()
        if isinstance(schema, dict):
            templates.extend(str(path) for path in schema.get("paths", {}))
    for route in getattr(app, "routes", ()):
        path = getattr(route, "path", None)
        if isinstance(path, str) and path:
            templates.append(path)
    return RouteTemplates(templates)


def method_label(method: str) -> str:
    """The method as a label: itself when standard, ``other`` otherwise."""
    return method if method in _STANDARD_METHODS else "other"


def status_class_label(status: int) -> str:
    """``2xx``, ``4xx`` and so on — the class, never the exact status."""
    return f"{status // 100}xx"


class RequestMetricsMiddleware:
    """Pure ASGI middleware recording :data:`HTTP_REQUESTS` and its duration."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap ``app``; the templates are read on the first request."""
        self.app = app
        self._templates: RouteTemplates | None = None

    def _route_label(self, scope: Scope) -> str:
        """The template for this request, reading the application's once."""
        if self._templates is None:
            self._templates = templates_of(scope.get("app"))
        return self._templates.label_for(str(scope.get("path", "")))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Pass the request through and record it, even when the app raises."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status = _SERVER_ERROR_STATUS

        async def send_noting_status(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_noting_status)
        finally:
            route = self._route_label(scope)
            HTTP_REQUESTS.labels(
                route=route,
                method=method_label(str(scope.get("method", ""))),
                status_class=status_class_label(status),
            ).inc()
            HTTP_REQUEST_DURATION.labels(route=route).observe(
                time.perf_counter() - started
            )
