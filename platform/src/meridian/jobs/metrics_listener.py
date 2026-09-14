"""The jobs process's metrics endpoint, guarded as the API's is.

A small WSGI server on a daemon thread, answering ``GET /metrics`` to a caller
presenting the same bearer token the API requires, and answering everything else
— a wrong token, no token, any other path — with the same 404 body the API sends
for a URL it does not route (D-087). The port is only exposed inside the compose
network, and the token is checked anyway: a tunnel pointed at the wrong service
is a configuration mistake away (D-109).

Request logging is switched off. Prometheus scrapes every fifteen seconds, and a
line per scrape would bury the one line per round that says what was scheduled.

Reference: docs/DECISIONS.md D-087, D-109.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server
from wsgiref.types import StartResponse, WSGIApplication, WSGIEnvironment

from prometheus_client import REGISTRY, make_wsgi_app

# Imported for its effect: the jobs' series register when their module is
# imported, and this endpoint must carry them whether or not a round has run.
from meridian.jobs import job_metrics  # noqa: F401
from meridian.metrics.access import is_metrics_scrape_authorised

__all__ = ["METRICS_PATH", "guarded_metrics_application", "start_metrics_listener"]

METRICS_PATH = "/metrics"

_NOT_FOUND_BODY = b'{"error":"not_found","message":"No such endpoint."}'
"""Byte-identical to ``meridian.api.errors.no_such_endpoint_response()``'s body.

Spelled out rather than imported: only ``meridian.api`` may import
``meridian.api`` (D-082). ``tests/unit/test_jobs_metrics_listener.py`` asserts
the two stay equal.
"""


class _QuietHandler(WSGIRequestHandler):
    """The standard handler, without a stderr line per request."""

    def log_message(self, format: str, *args: object) -> None:
        """Discard the access log line."""


def guarded_metrics_application(token: str) -> WSGIApplication:
    """A WSGI application serving this process's metrics to ``token`` only.

    Args:
        token: ``Settings.metrics_token``, the value Prometheus presents.

    Returns:
        The application: metrics at :data:`METRICS_PATH` for an authorised
        caller, and the API's 404 for every other request.
    """
    metrics = make_wsgi_app(REGISTRY)

    def application(
        environ: WSGIEnvironment, start_response: StartResponse
    ) -> Iterable[bytes]:
        authorised = environ.get("PATH_INFO") == METRICS_PATH and (
            is_metrics_scrape_authorised(environ.get("HTTP_AUTHORIZATION"), token)
        )
        if not authorised:
            start_response("404 Not Found", [("Content-Type", "application/json")])
            return [_NOT_FOUND_BODY]
        # prometheus_client leaves the application's return unannotated.
        body: Iterable[bytes] = metrics(environ, start_response)
        return body

    return application


def start_metrics_listener(host: str, port: int, token: str) -> WSGIServer:
    """Serve :func:`guarded_metrics_application` on a daemon thread.

    Args:
        host: Address to bind; the image binds all interfaces inside its network.
        port: Port to bind; ``0`` picks a free one, which tests use.
        token: The bearer token a scrape must present.

    Returns:
        The running server. A daemon thread serves it, so it ends with the
        process; call ``shutdown()`` to stop it sooner.
    """
    server = make_server(
        host, port, guarded_metrics_application(token), handler_class=_QuietHandler
    )
    threading.Thread(
        target=server.serve_forever, name="metrics-listener", daemon=True
    ).start()
    return server
