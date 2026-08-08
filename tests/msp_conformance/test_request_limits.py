"""MSP §6's request limits on the wire.

Conformance, not integration: §6 states a cap per endpoint and requires that an
oversized request is rejected as ``malformed`` **before the body is parsed**.
Both halves are asserted here — the status and the exact error body, and that
the rejection happens without the database being reachable, which is the only
observable proof from outside that nothing downstream ran.

``DATABASE_URL`` points nowhere on purpose, exactly as in
``test_time_endpoint.py``. ``POST /msp/v0/register`` needs a database to
succeed, so a 400 from this fixture is a request that never reached one.

Marked ``msp_conformance`` by the directory hook in ``tests/conftest.py``.

Reference: docs/MSP-SPEC.md §6 "Request limits", docs/DECISIONS.md D-028, D-050.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from meridian.api.app import create_app

REGISTER_PATH = "/msp/v0/register"
TIME_PATH = "/msp/v0/time"
CURRENT = {"MSP-Version": "0.1"}

# MSP §6's table, written out rather than imported. A test that computes its
# expectation from the constant under test passes when both are wrong together.
REGISTER_LIMIT_BYTES = 64 * 1024


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A client against the real application, with no database behind it."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://meridian:meridian@127.0.0.1:1/x")
    with TestClient(create_app()) as started:
        yield started


def padded_register_body(size_bytes: int) -> str:
    """A syntactically valid register body padded to roughly ``size_bytes``.

    Valid JSON on purpose. A body that is malformed *and* oversized cannot show
    which check rejected it, and the claim under test is that the size check
    fires first.
    """
    padding = "x" * size_bytes
    return json.dumps({"invite_token": "unused", "name": padding})


def test_a_register_body_over_64_kib_is_rejected(client: TestClient) -> None:
    """MSP §6: the ``register`` body is capped at 64 KiB."""
    body = padded_register_body(REGISTER_LIMIT_BYTES + 1)

    response = client.post(
        REGISTER_PATH,
        content=body,
        headers={**CURRENT, "Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "malformed"


def test_the_rejection_body_is_msp_sixs_two_flat_strings(client: TestClient) -> None:
    """§6 defines one error body and no other: two flat string fields."""
    response = client.post(
        REGISTER_PATH,
        content=padded_register_body(REGISTER_LIMIT_BYTES + 1),
        headers={**CURRENT, "Content-Type": "application/json"},
    )
    body = response.json()

    assert sorted(body) == ["error", "message"]
    assert all(isinstance(value, str) for value in body.values())


def test_the_rejection_never_echoes_the_submitted_body(client: TestClient) -> None:
    """A rejected request is not quoted back at the network.

    The padding here stands in for what a real oversized ``register`` body
    carries: an invite token. ``meridian.api.errors`` already refuses to build a
    message from the submitted value for the same reason, and the size check is
    a second place that could leak it.
    """
    response = client.post(
        REGISTER_PATH,
        content=padded_register_body(REGISTER_LIMIT_BYTES + 1),
        headers={**CURRENT, "Content-Type": "application/json"},
    )

    assert "x" * 64 not in response.text


def test_the_cap_applies_to_the_time_endpoint_too(client: TestClient) -> None:
    """§6's 64 KiB row names ``register``, ``heartbeat`` **and** ``time``.

    ``time`` is a GET, so this is the shape of the attack it is capped against:
    a POST to the path, carrying a body nobody asked for. The size check refuses
    it before the router gets to say the method is wrong.
    """
    response = client.post(
        TIME_PATH,
        content=padded_register_body(REGISTER_LIMIT_BYTES + 1),
        headers={**CURRENT, "Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "malformed"


def test_a_body_under_the_cap_is_forwarded_to_the_router(client: TestClient) -> None:
    """The cap admits what it should, and the proof needs no database.

    The same POST to ``time`` with an under-cap body gets 405 from the router
    instead — a status only reachable by code the middleware has already handed
    the request to. Asserting it here, rather than against ``register``, is what
    keeps this test free of the database ``register`` would need to answer: a
    test that only passes with TimescaleDB running cannot tell "the middleware
    forwarded it" from "the middleware was never involved".
    """
    response = client.post(
        TIME_PATH,
        content=padded_register_body(REGISTER_LIMIT_BYTES - 1024),
        headers={**CURRENT, "Content-Type": "application/json"},
    )

    assert response.status_code == 405


def test_a_body_without_a_declared_length_is_rejected(client: TestClient) -> None:
    """A chunked body declares no size, so §6's check cannot run — see D-050.

    ``httpx`` sends a generator body with ``Transfer-Encoding: chunked`` and no
    ``Content-Length``, which is the only way a client can present a body whose
    size is unknown before parsing.
    """

    response = client.post(
        REGISTER_PATH,
        content=iter([b'{"invite_token":', b' "unused"}']),
        headers={**CURRENT, "Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "malformed"


def test_the_time_endpoint_still_answers_without_a_content_length(
    client: TestClient,
) -> None:
    """The undeclared-length rule must not touch the one endpoint with no body.

    ``GET /msp/v0/time`` is what a station with no credentials and a wrong clock
    calls first (MSP §8). A size check that rejected it for not declaring a body
    it does not have would close the recovery path this endpoint exists to open.
    """
    response = client.get(TIME_PATH, headers=CURRENT)

    assert response.status_code == 200
    assert list(response.json()) == ["server_time"]
