"""``/metrics`` on the real application, guarded as D-087 requires.

``tests/unit/test_metrics_access.py`` covers the comparison and the refusal shape
against a hand-built application. This file is the one that matters for the
deployment: it builds the *actual* app through ``create_app`` and asserts the
endpoint is closed, because a guard that exists in a helper and was never wired
into the route would satisfy every unit test in the suite.

The endpoint is a Prometheus scrape rather than an MSP operation, so nothing here
sends an ``MSP-Version`` header. It lives in this directory because what it pins
is the wire behaviour of a running platform.

Marked ``msp_conformance`` by the directory hook in ``tests/conftest.py``.

Reference: docs/DECISIONS.md D-087, D-090.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from meridian.api.app import create_app

TOKEN = "the-configured-metrics-token"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A client against the real application, with a token configured.

    DATABASE_URL points nowhere on purpose, the same way the time endpoint's
    fixture does: whether a scrape is authorised has nothing to do with the
    database, and a test that needed one running could not show that.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://meridian:meridian@127.0.0.1:1/x")
    monkeypatch.setenv("METRICS_TOKEN", TOKEN)
    with TestClient(create_app(), raise_server_exceptions=False) as started:
        yield started


def test_the_real_metrics_endpoint_refuses_an_unauthenticated_scrape(
    client: TestClient,
) -> None:
    """The regression that matters: /metrics was public until this landed.

    Asserted against `create_app` rather than a stand-in, because the failure
    this guards against is the guard being written and never wired in.
    """
    response = client.get("/metrics")

    assert response.status_code == 404
    assert response.json() == {"error": "not_found", "message": "No such endpoint."}


def test_the_real_metrics_endpoint_leaks_no_process_internals_when_refused(
    client: TestClient,
) -> None:
    """A refusal must carry no scrape at all, not merely a truncated one.

    `python_gc_objects_collected_total` and `process_resident_memory_bytes` are
    default collectors that appear in every Prometheus exposition this platform
    produces, so either one showing up in a refused body means the response was
    assembled before the check rather than instead of it.
    """
    body = client.get("/metrics").text

    assert "process_" not in body
    assert "python_" not in body


def test_the_real_metrics_endpoint_serves_a_scrape_that_presents_the_token(
    client: TestClient,
) -> None:
    """Prometheus has to keep working — a guard that refuses everyone is not one.

    Without this, closing the endpoint completely would pass every other
    assertion in both files and the only symptom would be a Grafana dashboard
    that quietly stopped updating.
    """
    response = client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 200
    assert "python_" in response.text
