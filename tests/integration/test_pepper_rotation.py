"""Rotating ``TOKEN_HASH_PEPPER`` without stranding a station (D-201).

Driven through the API, as a station would meet it: a station registers under
one pepper, the platform restarts under a new one with the old kept as
``TOKEN_HASH_PEPPER_PREVIOUS``, and the station's next heartbeat and next
recovery must both still work — and must leave its credentials stored under the
new pepper, so that the old one can then be retired.

Each rotation has its control: the same request against a platform that
replaced the pepper outright is refused. Without that, a test that passed
because nothing was checked would look exactly like one that passed because the
overlap works.

Every write is rolled back.

Reference: docs/DECISIONS.md D-017, D-023, D-034, D-201.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from meridian.api.app import create_app
from meridian.api.dependencies import get_connection
from meridian.registry.pepper_rotation import hash_with_pepper
from meridian.store.invites import create_invite, hash_invite_token

OLD = "the-pepper-being-retired"
NEW = "the-pepper-taking-over"
CURRENT = {"MSP-Version": "0.1"}
REGISTRATION_KEY = "a-registration-key-for-rotation"

Platform = Callable[..., AbstractContextManager[TestClient]]


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def platform(
    database_url: str, rollback: Any, monkeypatch: pytest.MonkeyPatch
) -> Platform:
    """Start the platform under a given pepper pair, sharing one rolled-back DB."""

    @contextmanager
    def start(pepper: str, previous: str = "") -> Iterator[TestClient]:
        monkeypatch.setenv("DATABASE_URL", database_url)
        monkeypatch.setenv("TOKEN_HASH_PEPPER", pepper)
        monkeypatch.setenv("TOKEN_HASH_PEPPER_PREVIOUS", previous)
        app = create_app()
        app.dependency_overrides[get_connection] = lambda: rollback
        with TestClient(app) as client:
            yield client

    return start


def _registration(invite: str) -> dict[str, Any]:
    return {
        "invite_token": invite,
        "registration_key": REGISTRATION_KEY,
        "name": "rotation-station",
        "operator": "NTTF NEC",
        "location": {"lat": 12.97, "lon": 77.59, "alt_m": 920},
        "simulated": False,
        "capabilities": [
            {
                "band": "vhf",
                "freq_min_hz": 136000000,
                "freq_max_hz": 138000000,
                "modes": ["lrpt"],
                "polarisation": "rhcp",
                "tracking": False,
                "min_elevation_deg": 10,
            }
        ],
        "client": {"impl": "meridian-reference", "version": "0.1.0"},
    }


def _register(client: TestClient, rollback: Any) -> dict[str, str]:
    with rollback.cursor() as cur:
        cur.execute(
            "insert into invite_tokens (token_sha256, label) values (%s, %s)",
            (hash_invite_token("rotation-invite"), "pepper-rotation"),
        )
    response = client.post(
        "/msp/v0/register", json=_registration("rotation-invite"), headers=CURRENT
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return {"station_id": body["station_id"], "token": body["token"]}


def _heartbeat(client: TestClient, station: dict[str, str]) -> int:
    response = client.post(
        "/msp/v0/heartbeat",
        json={
            "station_id": station["station_id"],
            "sent_at": "2026-09-28T09:31:02Z",
            "state": "idle",
            "held_assignments": [],
            "health": {},
        },
        headers={**CURRENT, "Authorization": f"Bearer {station['token']}"},
    )
    return response.status_code


def _recover(client: TestClient, rollback: Any, station_id: str) -> int:
    """Present a bound invite and the stored registration key (D-034)."""
    invite = create_invite(
        rollback,
        label="rotation-recovery",
        expires_at=None,
        issued_for_station_id=station_id,
    )
    response = client.post(
        "/msp/v0/register", json=_registration(invite), headers=CURRENT
    )
    return response.status_code


def _stored(rollback: Any, station_id: str) -> tuple[bytes, bytes]:
    with rollback.cursor() as cur:
        cur.execute(
            "select token_sha256, registration_key_sha256 from stations"
            " where station_id = %s",
            (station_id,),
        )
        token_hash, key_hash = cur.fetchone()
    return bytes(token_hash), bytes(key_hash)


def test_replacing_the_pepper_outright_strands_the_station(
    platform: Platform, rollback: Any
) -> None:
    """The control, and the finding D-201 records: neither path survives it."""
    with platform(OLD) as client:
        station = _register(client, rollback)

    with platform(NEW) as client:
        assert _heartbeat(client, station) == 401
        assert _recover(client, rollback, station["station_id"]) == 403


def test_a_heartbeat_during_the_overlap_moves_the_token(
    platform: Platform, rollback: Any
) -> None:
    with platform(OLD) as client:
        station = _register(client, rollback)
    old_token_hash, _ = _stored(rollback, station["station_id"])

    with platform(NEW, previous=OLD) as client:
        assert _heartbeat(client, station) == 200

    token_hash, _ = _stored(rollback, station["station_id"])
    assert token_hash == hash_with_pepper(NEW, station["token"])
    assert token_hash != old_token_hash
    with platform(NEW) as client:
        assert _heartbeat(client, station) == 200


def test_a_recovery_during_the_overlap_moves_the_registration_key(
    platform: Platform, rollback: Any
) -> None:
    with platform(OLD) as client:
        station = _register(client, rollback)

    with platform(NEW, previous=OLD) as client:
        assert _recover(client, rollback, station["station_id"]) == 200

    _, key_hash = _stored(rollback, station["station_id"])
    assert key_hash == hash_with_pepper(NEW, REGISTRATION_KEY)
    with platform(NEW) as client:
        assert _recover(client, rollback, station["station_id"]) == 200


def test_the_previous_pepper_never_hashes_anything_new(
    platform: Platform, rollback: Any
) -> None:
    """A station registered during the overlap is stored under the new pepper."""
    with platform(NEW, previous=OLD) as client:
        station = _register(client, rollback)

    token_hash, key_hash = _stored(rollback, station["station_id"])
    assert token_hash == hash_with_pepper(NEW, station["token"])
    assert key_hash == hash_with_pepper(NEW, REGISTRATION_KEY)


def test_a_revoked_token_stays_revoked_under_the_old_pepper(
    platform: Platform, rollback: Any
) -> None:
    """The overlap must not become a way round `meridian station revoke`."""
    with platform(OLD) as client:
        station = _register(client, rollback)
    with rollback.cursor() as cur:
        cur.execute(
            "update stations set token_revoked_at = now() where station_id = %s",
            (station["station_id"],),
        )

    with platform(NEW, previous=OLD) as client:
        assert _heartbeat(client, station) == 401
