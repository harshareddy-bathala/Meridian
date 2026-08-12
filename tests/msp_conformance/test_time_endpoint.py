"""``GET /msp/v0/time`` on the wire, asserted against the specification text.

Conformance, not integration: these assert the **bytes** MSP promises — field
names, exact error bodies, status codes and version handling — because MSP is
published for other people to implement, and a reference implementation that
quietly diverges from its own specification is worse than no specification.

These are the fixtures a third-party station implementation would be tested
against, which is why the expected bodies are written out in full rather than
computed from the code under test. A test that builds its expectation with the
same function it is checking passes when both are wrong together.

Marked ``msp_conformance`` by the directory hook in ``tests/conftest.py``.

Reference: docs/MSP-SPEC.md §6, §7, §8.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from meridian.api import platform_clock
from meridian.api.app import create_app

TIME_PATH = "/msp/v0/time"
CURRENT = {"MSP-Version": "0.1"}

# MSP §8: "2026-08-14T09:31:02Z". Millisecond precision, and a literal Z rather
# than +00:00 — the same instant, a different string, and a microcontroller
# comparing suffixes sees the difference.
WIRE_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A client against the real application.

    DATABASE_URL points nowhere on purpose. MSP §8 says this endpoint "touches no
    database", and a test that only passes with a database running cannot tell
    that claim from a coincidence.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://meridian:meridian@127.0.0.1:1/x")
    with TestClient(create_app()) as started:
        yield started


def test_the_response_has_exactly_one_field(client: TestClient) -> None:
    """MSP §8: "takes no body and returns one field"."""
    response = client.get(TIME_PATH, headers=CURRENT)

    assert response.status_code == 200
    assert list(response.json()) == ["server_time"]


def test_server_time_is_iso_8601_utc_with_a_z_suffix(client: TestClient) -> None:
    body = client.get(TIME_PATH, headers=CURRENT).json()

    assert WIRE_TIME.match(body["server_time"]), body["server_time"]


def test_server_time_parses_back_to_the_instant_it_named(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The string is not merely well-shaped; it is the right moment.

    A formatter that dropped the offset would still match the pattern above while
    publishing a clock wrong by the host's local offset — which is exactly the
    failure every station's offset estimate would then inherit.
    """
    fixed = datetime(2026, 8, 14, 9, 31, 2, 123456, tzinfo=UTC)
    monkeypatch.setattr(platform_clock, "utc_now", lambda: fixed)

    body = client.get(TIME_PATH, headers=CURRENT).json()

    assert body["server_time"] == "2026-08-14T09:31:02.123Z"


def test_it_needs_no_authentication(client: TestClient) -> None:
    """MSP §8: unauthenticated, because a station that lost its token still needs
    a clock offset before it can rotate its credential."""
    assert client.get(TIME_PATH, headers=CURRENT).status_code == 200


def test_it_touches_no_database(client: TestClient) -> None:
    """The fixture's DATABASE_URL points at a closed port.

    If this endpoint ever acquires a query, this test fails rather than becoming
    slow — which is the failure mode worth having, since the endpoint's whole
    justification is that it answers without reading a row.
    """
    assert client.get(TIME_PATH, headers=CURRENT).status_code == 200


# --- MSP §7, version handling -------------------------------------------------


def test_a_missing_version_is_unsupported_version(client: TestClient) -> None:
    """MSP §7: "A missing header is `unsupported_version`".

    Not `malformed`. The distinction is in the specification and is easy to get
    wrong — `malformed` is about the body.
    """
    response = client.get(TIME_PATH)

    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_version"


@pytest.mark.parametrize("value", ["", "0", "zero.one", "0.x", "x.1", "0.1.2", "  "])
def test_a_malformed_version_is_rejected(client: TestClient, value: str) -> None:
    response = client.get(TIME_PATH, headers={"MSP-Version": value})

    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_version"


@pytest.mark.parametrize("value", ["1.0", "2.3", "9.9"])
def test_an_unsupported_major_is_rejected(client: TestClient, value: str) -> None:
    response = client.get(TIME_PATH, headers={"MSP-Version": value})

    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_version"


@pytest.mark.parametrize("value", ["0.0", "0.1", "0.2", "0.7", "0.99"])
def test_any_minor_within_a_supported_major_is_accepted(
    client: TestClient, value: str
) -> None:
    """MSP §7: "an unrecognised minor within a supported major is accepted,
    because minor versions are additive by definition".

    This is the rule most likely to be implemented as an equality check against
    "0.1", which would make every future minor a breaking change for every
    already-deployed station.
    """
    assert client.get(TIME_PATH, headers={"MSP-Version": value}).status_code == 200


# --- MSP §6, the error body ---------------------------------------------------


def test_the_error_body_has_exactly_two_flat_string_fields(client: TestClient) -> None:
    """D-004: two flat strings, no nesting, no arrays, no optional members.

    A microcontroller extracts both with a substring scan. FastAPI's default
    validation response is a 422 carrying a nested `detail` array of objects,
    which is three things at once that MSP does not have.
    """
    body = client.get(TIME_PATH).json()

    assert sorted(body) == ["error", "message"]
    assert all(isinstance(value, str) for value in body.values())


def test_no_error_response_leaks_a_header_value_back(client: TestClient) -> None:
    """A rejected version is echoed; a rejected credential must not be.

    This endpoint takes no credential, so the check here is the weaker one that
    nothing resembling a token appears. It is written now so the battery exists
    before `register` and `heartbeat` arrive with real secrets to leak.
    """
    secret = "tok_ThisMustNeverComeBack"
    response = client.get(
        TIME_PATH, headers={"MSP-Version": "9.9", "Authorization": f"Bearer {secret}"}
    )

    assert secret not in response.text


def test_every_endpoint_msp_binds_is_mounted() -> None:
    """MSP §8 binds four endpoints, and the router package mounts all four.

    This test read the other way around for most of the project's life — "what
    is not built stays unserved" — and named `heartbeat`, then `observations`,
    as each one was still missing. With Stage 9 there is nothing left to be
    absent, so it inverts.

    Asserted against the published schema rather than by calling each path:
    three of the four open a database connection, and a routing claim should not
    be answered by whether a connection to a closed port times out.
    """
    paths = create_app().openapi()["paths"]
    mounted = {
        f"{method.upper()} {path}"
        for path, operations in paths.items()
        for method in operations
    }

    assert {
        "GET /msp/v0/time",
        "POST /msp/v0/register",
        "POST /msp/v0/heartbeat",
        "POST /msp/v0/observations",
    } <= mounted
