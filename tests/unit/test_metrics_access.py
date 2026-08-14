"""Who may scrape ``/metrics``, and whether a refusal gives the endpoint away.

Two things are pinned. The token comparison is a pure function, so it is tested
as a table. The refusal's *shape* is tested against a real application, because
D-087's protection is not that the token is checked — it is that a refused scrape
is indistinguishable from a URL that was never routed, and that property lives in
the response rather than in the comparison.

Marked as a unit test by living in ``tests/unit``: no network, no filesystem.

Reference: docs/DECISIONS.md D-087, D-090.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Header, Response
from fastapi.testclient import TestClient

from meridian.api.errors import install_error_handlers, no_such_endpoint_response
from meridian.api.metrics_access import is_metrics_scrape_authorised

TOKEN = "a-real-metrics-token"


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "Bearer",
        "Bearer ",
        "Bearer wrong-token",
        "bearer " + TOKEN,
        "Basic " + TOKEN,
        TOKEN,
        "Bearer " + TOKEN + " ",
        "Bearer " + TOKEN.upper(),
    ],
)
def test_a_scrape_without_the_exact_token_is_refused(header: str | None) -> None:
    """Everything that is not the token, including things that nearly are.

    `bearer ` lowercase and a trailing space are here because both are the kind
    of thing a hand-written curl produces, and a check that accepted either would
    be looser than the one D-087 describes without anybody noticing.
    """
    assert is_metrics_scrape_authorised(header, TOKEN) is False


def test_a_scrape_presenting_the_token_is_allowed() -> None:
    """The one header that works."""
    assert is_metrics_scrape_authorised(f"Bearer {TOKEN}", TOKEN) is True


@pytest.fixture
def client() -> TestClient:
    """A `/metrics` guarded exactly as `create_app` guards the real one.

    Rebuilt here rather than imported so the test needs no settings and no
    connection pool. That is also this fixture's limitation: a `create_app` that
    stopped guarding the route would leave every test in this file passing.
    `tests/msp_conformance/test_metrics_endpoint.py` is what closes that gap, by
    asking the real application the same questions.
    """
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/metrics")
    def _metrics(authorization: str | None = Header(default=None)) -> Response:
        if not is_metrics_scrape_authorised(authorization, TOKEN):
            return no_such_endpoint_response()
        return Response("meridian_up 1", media_type="text/plain")

    return TestClient(app, raise_server_exceptions=False)


def test_a_refused_scrape_is_indistinguishable_from_a_path_that_never_existed(
    client: TestClient,
) -> None:
    """D-087's actual protection, asserted as the equality it rests on.

    A 401 would confirm the endpoint is there and invite a second guess. This
    compares the two responses field by field — status, body and content type —
    because any one of them differing is enough to tell a prober that `/metrics`
    exists and that everything else does not.
    """
    refused = client.get("/metrics")
    never_existed = client.get("/no-such-path-at-all")

    assert refused.status_code == never_existed.status_code == 404
    assert refused.json() == never_existed.json()
    assert refused.headers["content-type"] == never_existed.headers["content-type"]
    # The scrape body must not leak either — a refusal that still carried one
    # metric would defeat the point of refusing it.
    assert "meridian_up" not in refused.text


def test_the_token_holder_still_gets_the_metrics(client: TestClient) -> None:
    """The endpoint has to keep working, or Prometheus silently stops scraping.

    Worth its own test because every other assertion here is about refusal, and
    a change that refused *everything* would satisfy all of them.
    """
    allowed = client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})

    assert allowed.status_code == 200
    assert "meridian_up" in allowed.text
