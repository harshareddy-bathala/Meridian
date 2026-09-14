"""The jobs process's metrics endpoint answers only the token, as the API's does.

Called as a WSGI application directly, with no socket: what matters is the
decision and the bytes, and a refusal must be indistinguishable from the API's
answer to a URL it does not route (D-087).

Marked as a unit test by living in ``tests/unit``: no network, no database.

Reference: docs/DECISIONS.md D-087, D-109.
"""

from __future__ import annotations

import pytest

from meridian.api.errors import no_such_endpoint_response
from meridian.jobs.metrics_listener import guarded_metrics_application

TOKEN = "a-jobs-metrics-token"


def call(path: str, authorization: str | None) -> tuple[str, bytes]:
    """One request through the application; the status line and the body."""
    statuses: list[str] = []
    environ: dict[str, object] = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": path,
        "QUERY_STRING": "",
    }
    if authorization is not None:
        environ["HTTP_AUTHORIZATION"] = authorization

    def start_response(status: str, _headers: list[tuple[str, str]]) -> None:
        statuses.append(status)

    body = b"".join(
        guarded_metrics_application(TOKEN)(environ, start_response)  # type: ignore[arg-type]
    )
    return statuses[0], body


@pytest.mark.parametrize(
    ("path", "authorization"),
    [
        ("/metrics", None),
        ("/metrics", "Bearer wrong"),
        ("/anything-else", f"Bearer {TOKEN}"),
    ],
)
def test_everything_but_an_authorised_scrape_gets_the_apis_404(
    path: str, authorization: str | None
) -> None:
    """Same status, same body, whichever way the request was wrong."""
    status, body = call(path, authorization)

    assert status.startswith("404")
    assert body == no_such_endpoint_response().body


def test_an_authorised_scrape_gets_the_jobs_metrics() -> None:
    """The scrape carries the jobs' own series, not just process internals."""
    status, body = call("/metrics", f"Bearer {TOKEN}")

    assert status.startswith("200")
    assert b"meridian_job_duration_seconds" in body
