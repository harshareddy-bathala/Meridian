"""``POST /msp/v0/heartbeat`` on the wire, asserted against the specification.

Conformance, not integration: these assert the **bytes** MSP §4.2 promises —
response fields, error codes, and which bodies are refused — because MSP is
published for other people to implement.

The station is registered through ``POST /msp/v0/register`` rather than inserted
by SQL, so the bearer token under test is one the platform actually minted and
the two endpoints are proven to agree about identity.

Marked ``msp_conformance`` by the directory hook in ``tests/conftest.py``.

Reference: docs/MSP-SPEC.md §4.2, §4.3, §6, §8; docs/DECISIONS.md D-003, D-035.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from meridian.api.app import create_app
from meridian.api.dependencies import get_connection
from meridian.store.invites import hash_invite_token

HEARTBEAT_PATH = "/msp/v0/heartbeat"
REGISTER_PATH = "/msp/v0/register"
CURRENT = {"MSP-Version": "0.1"}
SENT_AT = "2026-08-14T09:31:02Z"


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes — see test_register_endpoint.py's twin."""
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def client(
    database_url: str, rollback: Any, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """A client whose writes are rolled back, sharing this test's connection."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("TOKEN_HASH_PEPPER", "heartbeat-conformance-pepper")
    app = create_app()
    app.dependency_overrides[get_connection] = lambda: rollback
    with TestClient(app) as started:
        yield started
    app.dependency_overrides.clear()


@pytest.fixture
def station(client: TestClient, rollback: Any) -> dict[str, str]:
    """A registered station: its ``station_id`` and a live bearer token."""
    with rollback.cursor() as cur:
        cur.execute(
            "insert into invite_tokens (token_sha256, label) values (%s, %s)",
            (hash_invite_token("heartbeat-invite"), "heartbeat-conformance"),
        )
    response = client.post(
        REGISTER_PATH,
        json={
            "invite_token": "heartbeat-invite",
            "registration_key": "a-registration-key",
            "name": "nec-rooftop-01",
            "operator": "NTTF NEC",
            "location": {"lat": 12.9716, "lon": 77.5946, "alt_m": 920},
            "simulated": False,
            "capabilities": [
                {
                    "band": "vhf",
                    "freq_min_hz": 136000000,
                    "freq_max_hz": 138000000,
                    "modes": ["lrpt"],
                    "polarisation": "rhcp",
                    "tracking": True,
                    "min_elevation_deg": 10,
                }
            ],
            "client": {"impl": "meridian-reference", "version": "0.1.0"},
        },
        headers=CURRENT,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return {"station_id": body["station_id"], "token": body["token"]}


def heartbeat_body(station_id: str, **overrides: Any) -> dict[str, Any]:
    """MSP §4.2's example payload, with the fields a test varies exposed."""
    body: dict[str, Any] = {
        "station_id": station_id,
        "sent_at": SENT_AT,
        "state": "idle",
        "held_assignments": [],
        "health": {"uptime_s": 84213, "disk_free_pct": 62},
    }
    body.update(overrides)
    return body


def auth(station: dict[str, str]) -> dict[str, str]:
    """The headers a heartbeat carries: version and bearer token."""
    return {**CURRENT, "Authorization": f"Bearer {station['token']}"}


def test_the_response_has_exactly_the_two_fields_msp_defines(
    client: TestClient, station: dict[str, str]
) -> None:
    """§4.2: `{ "assignments": [...], "server_time": ... }` and nothing else."""
    response = client.post(
        HEARTBEAT_PATH,
        json=heartbeat_body(station["station_id"]),
        headers=auth(station),
    )

    assert response.status_code == 200, response.text
    assert sorted(response.json()) == ["assignments", "server_time"]


def test_a_station_with_no_work_gets_an_empty_list_not_a_null(
    client: TestClient, station: dict[str, str]
) -> None:
    """An empty list states "nothing due"; null would state nothing at all."""
    response = client.post(
        HEARTBEAT_PATH,
        json=heartbeat_body(station["station_id"]),
        headers=auth(station),
    )

    assert response.json()["assignments"] == []


def test_a_heartbeat_without_a_token_is_unauthorized(
    client: TestClient, station: dict[str, str]
) -> None:
    """§6: 401 covers a bearer token missing, unknown or revoked."""
    response = client.post(
        HEARTBEAT_PATH, json=heartbeat_body(station["station_id"]), headers=CURRENT
    )

    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


def test_one_stations_token_cannot_heartbeat_as_another(
    client: TestClient, station: dict[str, str]
) -> None:
    """Stage 5's auth clause: the token decides who the station is.

    Accepting the token's view would let a leaked credential file heartbeats
    under another station's name; accepting the body's would be no
    authentication at all. The disagreement is refused instead of resolved.
    """
    response = client.post(
        HEARTBEAT_PATH,
        json=heartbeat_body("st_somebody_else"),
        headers=auth(station),
    )

    assert response.status_code == 403
    assert response.json()["error"] == "not_owner"


def test_the_rejection_does_not_echo_the_claimed_station_id(
    client: TestClient, station: dict[str, str]
) -> None:
    """A token holder must not be able to probe which station ids exist."""
    response = client.post(
        HEARTBEAT_PATH,
        json=heartbeat_body("st_probe_target"),
        headers=auth(station),
    )

    assert "st_probe_target" not in response.text


def test_a_partial_listening_block_is_malformed(
    client: TestClient, station: dict[str, str]
) -> None:
    """§4.2: the block is all-or-nothing.

    A partial block cannot support the assertion the block exists to make —
    that a station was tuned to a *specific* frequency for a *specific* target.
    `mode` is missing here, which is the field D-028 added precisely because a
    station running the wrong demodulator did not observe the pass.
    """
    body = heartbeat_body(
        station["station_id"],
        listening={
            "assignment_id": "as_44b2",
            "satellite_id": "norad:57166",
            "centre_freq_hz": 137900000,
        },
    )

    response = client.post(HEARTBEAT_PATH, json=body, headers=auth(station))

    assert response.status_code == 400
    assert response.json()["error"] == "malformed"


def test_a_health_object_over_four_kib_is_malformed(
    client: TestClient, station: dict[str, str]
) -> None:
    """§6 caps `health` at 4 KiB serialised, separately from the body cap.

    The body here is well under 64 KiB, so the request-size middleware passes
    it — this is the second, narrower limit doing its own job.
    """
    body = heartbeat_body(station["station_id"], health={"errors": ["x" * 5000]})

    response = client.post(HEARTBEAT_PATH, json=body, headers=auth(station))

    assert response.status_code == 400
    assert response.json()["error"] == "malformed"


def test_a_naive_sent_at_is_malformed(
    client: TestClient, station: dict[str, str]
) -> None:
    """A naive instant would be stored as though it were UTC.

    That misreports a station in another timezone by whole hours, silently.
    Rejecting it gives the station a 400 it can act on instead.
    """
    body = heartbeat_body(station["station_id"], sent_at="2026-08-14T09:31:02")

    response = client.post(HEARTBEAT_PATH, json=body, headers=auth(station))

    assert response.status_code == 400
    assert response.json()["error"] == "malformed"


def test_an_unknown_state_is_malformed(
    client: TestClient, station: dict[str, str]
) -> None:
    """§4.2 lists six values for `state`; a seventh is not additive."""
    body = heartbeat_body(station["station_id"], state="rebooting")

    response = client.post(HEARTBEAT_PATH, json=body, headers=auth(station))

    assert response.status_code == 400
    assert response.json()["error"] == "malformed"
