"""The public endpoints with nothing behind them yet, on the real application.

D-086 pins the body exactly, and the tests spell it out in full rather than
building it from the model: the property that matters is that a chart reading
this response finds *no value to draw*, and a model that grew a ``value: None``
field would still validate against itself.

No database is involved — these endpoints read nothing — so ``DATABASE_URL``
points nowhere, as in the metrics tests. An endpoint that started querying
would have to answer that change here.

Marked ``msp_conformance`` by the directory hook in ``tests/conftest.py``: what is
pinned is the wire behaviour of a running platform.

Reference: docs/DECISIONS.md D-083, D-086.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from meridian.api.app import create_app

ENDPOINTS = {
    "/api/v1/reliability": 20,
    "/api/v1/aggregates": 19,
}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", "postgresql://meridian:meridian@127.0.0.1:1/x")
    with TestClient(create_app(), raise_server_exceptions=False) as started:
        yield started


@pytest.mark.parametrize(("path", "stage"), ENDPOINTS.items())
def test_the_endpoint_answers_200_with_exactly_the_placeholder_fields(
    client: TestClient, path: str, stage: int
) -> None:
    """200, not 501: the normal case must not run the dashboard's error path."""
    response = client.get(path)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"status", "reason", "available_from_stage"}
    assert body["status"] == "not_yet_computed"
    assert body["available_from_stage"] == stage
    assert body["reason"]


@pytest.mark.parametrize("path", ENDPOINTS)
def test_the_placeholder_does_not_claim_provenance_for_data_it_lacks(
    client: TestClient, path: str
) -> None:
    """No data, so no ``simulated`` — its absence is deliberate (D-086)."""
    assert "simulated" not in client.get(path).json()


@pytest.mark.parametrize("path", ENDPOINTS)
def test_the_status_is_a_schema_discriminator(client: TestClient, path: str) -> None:
    """A client can branch on ``status`` from the schema, before any data exists."""
    schema = client.get("/openapi.json").json()
    response = schema["paths"][path]["get"]["responses"]["200"]["content"]
    ref = response["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]
    status = schema["components"]["schemas"][ref]["properties"]["status"]

    assert status.get("const", status.get("enum", [None])[0]) == "not_yet_computed"


@pytest.mark.parametrize("path", ENDPOINTS)
def test_a_write_to_a_read_endpoint_is_method_not_allowed(
    client: TestClient, path: str
) -> None:
    response = client.post(path)

    assert response.status_code == 405
    assert response.json()["error"] == "method_not_allowed"
