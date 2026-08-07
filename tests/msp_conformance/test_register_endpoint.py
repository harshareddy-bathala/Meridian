"""``POST /msp/v0/register`` on the wire, asserted against the specification text.

Unlike ``/time``, ``register`` needs a real database, so its ``client`` fixture
differs from ``test_time_endpoint.py``'s in exactly that respect —
``DATABASE_URL`` points at the real test database, and ``get_connection`` is
overridden to hand out this test's own ``rollback``-wrapped connection (the
savepoint pattern used everywhere else in this suite), so a station one test
creates is not still there for the next.

This file does **not** re-derive MSP §4.1's six-row recovery table — that is
already proven directly against ``PsycopgRegistry`` in
``tests/integration/test_psycopg_registry.py``, with no HTTP involved, and
re-running the same matrix through this layer would be duplicate coverage of
the same logic rather than new evidence. It proves the HTTP layer wires and
shapes that logic correctly: exact response fields, the error mapping, and
that a rejected body never echoes a secret back.

Marked ``msp_conformance`` by the directory hook in ``tests/conftest.py``.

Reference: docs/MSP-SPEC.md §4.1, §5, §6, §7.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from meridian.api.app import create_app
from meridian.api.dependencies import get_connection
from meridian.store.invites import hash_invite_token

REGISTER_PATH = "/msp/v0/register"
CURRENT = {"MSP-Version": "0.1"}


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes — see test_store_invites.py's twin."""
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def client(
    database_url: str, rollback: Any, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """A client against the real application, its writes rolled back.

    The lifespan's own pool (used only for the health check and the
    startup invite-bootstrap seed) is untouched and points at the real test
    database — only ``get_connection``, the dependency ``register`` itself
    uses, is overridden.
    """
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("TOKEN_HASH_PEPPER", "conformance-test-pepper")
    app = create_app()
    app.dependency_overrides[get_connection] = lambda: rollback
    with TestClient(app) as started:
        yield started
    app.dependency_overrides.clear()


def insert_invite(rollback: Any, *, plaintext: str) -> None:
    with rollback.cursor() as cur:
        cur.execute(
            "insert into invite_tokens (token_sha256, label) values (%s, %s)",
            (hash_invite_token(plaintext), "conformance-test"),
        )


def sample_payload(
    *,
    invite_token: str,
    registration_key: str = "the-registration-key",
    simulated: bool = False,
    simulator_run_id: str | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    """MSP §4.1's example payload, with the fields a test varies exposed."""
    body: dict[str, Any] = {
        "invite_token": invite_token,
        "registration_key": registration_key,
        "name": "nec-rooftop-01",
        "operator": "NTTF NEC",
        "location": {"lat": 12.9716, "lon": 77.5946, "alt_m": 920},
        "simulated": simulated,
        "capabilities": [
            {
                "band": "vhf",
                "freq_min_hz": 136000000,
                "freq_max_hz": 138000000,
                "modes": ["lrpt", "fsk", "afsk"],
                "polarisation": "rhcp",
                "tracking": True,
                "min_elevation_deg": 10,
            }
        ],
        "client": {"impl": "meridian-reference", "version": "0.1.0"},
    }
    if simulated:
        body["simulator_run_id"] = simulator_run_id
        body["seed"] = seed
    return body


def test_a_successful_registration_returns_exactly_three_fields(
    client: TestClient, rollback: Any
) -> None:
    insert_invite(rollback, plaintext="conformance-invite-create")
    response = client.post(
        REGISTER_PATH,
        json=sample_payload(invite_token="conformance-invite-create"),
        headers=CURRENT,
    )

    assert response.status_code == 200
    body = response.json()
    assert sorted(body) == ["heartbeat_interval_s", "station_id", "token"]
    assert body["station_id"].startswith("st_")
    assert isinstance(body["token"], str) and body["token"]
    assert body["heartbeat_interval_s"] == 30


def test_an_unknown_invite_is_rejected_with_a_fixed_generic_message(
    client: TestClient,
) -> None:
    """MSP §3: a client may know an invite was rejected, never why.

    Written out in full rather than imported from the code under test — a
    test that builds its expectation with the same string the handler emits
    passes when both are wrong together.
    """
    response = client.post(
        REGISTER_PATH,
        json=sample_payload(invite_token="never-issued"),
        headers=CURRENT,
    )

    assert response.status_code == 403
    body = response.json()
    assert body["error"] == "invalid_invite"
    assert body["message"] == (
        "Invite token and registration key did not admit a registration."
    )


def test_malformed_json_body_is_rejected(client: TestClient) -> None:
    response = client.post(
        REGISTER_PATH,
        content="{not valid json",
        headers={**CURRENT, "Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "malformed"


def test_a_wrong_field_type_is_rejected(client: TestClient) -> None:
    payload = sample_payload(invite_token="whatever")
    payload["capabilities"][0]["freq_min_hz"] = "not-a-number"

    response = client.post(REGISTER_PATH, json=payload, headers=CURRENT)

    assert response.status_code == 400
    assert response.json()["error"] == "malformed"


def test_simulated_true_without_simulator_fields_is_rejected(
    client: TestClient,
) -> None:
    """MSP §5: simulator_run_id and seed are required together with
    simulated: true. Omitted here on purpose."""
    payload = sample_payload(invite_token="whatever", simulated=True)

    response = client.post(REGISTER_PATH, json=payload, headers=CURRENT)

    assert response.status_code == 400
    assert response.json()["error"] == "malformed"


def test_a_missing_version_header_is_unsupported_version(client: TestClient) -> None:
    """One test confirming the router-level dependency applies here too —
    not the full version battery, already covered generically by
    test_time_endpoint.py."""
    response = client.post(REGISTER_PATH, json=sample_payload(invite_token="whatever"))

    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_version"


def test_secrets_do_not_leak_through_an_invalid_invite_response(
    client: TestClient,
) -> None:
    payload = sample_payload(invite_token="tok_ThisMustNeverComeBack")
    payload["registration_key"] = "key_ThisMustNeverComeBackEither"

    response = client.post(REGISTER_PATH, json=payload, headers=CURRENT)

    assert "tok_ThisMustNeverComeBack" not in response.text
    assert "key_ThisMustNeverComeBackEither" not in response.text


def test_secrets_do_not_leak_through_a_validation_error_response(
    client: TestClient,
) -> None:
    """The path errors.py's own validation handler exists for: a rejected
    register body's submitted value includes the invite token."""
    payload = sample_payload(invite_token="tok_ThisMustNeverComeBack")
    payload["registration_key"] = "key_ThisMustNeverComeBackEither"
    payload["capabilities"][0]["freq_min_hz"] = "not-a-number"

    response = client.post(REGISTER_PATH, json=payload, headers=CURRENT)

    assert response.status_code == 400
    assert "tok_ThisMustNeverComeBack" not in response.text
    assert "key_ThisMustNeverComeBackEither" not in response.text
