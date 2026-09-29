"""MSP §6's ``rate_limited`` on the wire, and the public API's (D-202).

Conformance: the status, the exact two-field body and the ``Retry-After`` header
a refused request gets, and which requests share a bucket. The limiter's clock is
a variable, so "after ten seconds" is a statement rather than a sleep.

Two fixtures. ``client`` has no database behind it, exactly as
``test_request_limits.py``: ``/msp/v0/time`` and an unrouted ``/api/v1`` path need
none, so every request it makes is decided by the limiter or the router alone.
``station_client`` has the test database, because a token-keyed bucket is only
worth testing with a token the platform actually minted.

Marked ``msp_conformance`` by the directory hook in ``tests/conftest.py``.

Reference: docs/MSP-SPEC.md §6; docs/DECISIONS.md D-051, D-088, D-202.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from meridian.api.app import create_app
from meridian.api.dependencies import get_connection
from meridian.api.rate_limits import RateLimiter
from meridian.store.invites import hash_invite_token

CURRENT = {"MSP-Version": "0.1"}
TIME_PATH = "/msp/v0/time"
PUBLIC_PATH = "/api/v1/no-such-thing"

# D-202's table, written out rather than imported: a test that reads its
# expectation from the constant under test passes when both are wrong together.
HEARTBEAT_BURST = 6
HEARTBEAT_REFILL_S = 10
MSP_CLIENT_BURST = 300
PUBLIC_CLIENT_BURST = 50


class Clock:
    """Seconds that pass only when a test says so."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


def _limit(client: TestClient, clock: Clock, client_header: str = "") -> None:
    """Replace the lifespan's limiter with one on the test's clock."""
    client.app.state.rate_limiter = RateLimiter(
        clock=clock, client_header=client_header
    )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, clock: Clock) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", "postgresql://meridian:meridian@127.0.0.1:1/x")
    with TestClient(create_app()) as started:
        _limit(started, clock)
        yield started


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def station_client(
    database_url: str, rollback: Any, monkeypatch: pytest.MonkeyPatch, clock: Clock
) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("TOKEN_HASH_PEPPER", "rate-limit-conformance-pepper")
    app = create_app()
    app.dependency_overrides[get_connection] = lambda: rollback
    with TestClient(app) as started:
        _limit(started, clock)
        yield started
    app.dependency_overrides.clear()


def _register(client: TestClient, rollback: Any, name: str) -> dict[str, str]:
    with rollback.cursor() as cur:
        cur.execute(
            "insert into invite_tokens (token_sha256, label) values (%s, %s)",
            (hash_invite_token(f"{name}-invite"), name),
        )
    response = client.post(
        "/msp/v0/register",
        json={
            "invite_token": f"{name}-invite",
            "registration_key": f"{name}-registration-key",
            "name": name,
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
        },
        headers=CURRENT,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return {"station_id": body["station_id"], "token": body["token"]}


def _heartbeat(client: TestClient, station: dict[str, str]) -> Any:
    return client.post(
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


def test_a_station_past_its_heartbeat_burst_is_rate_limited(
    station_client: TestClient, rollback: Any
) -> None:
    """§6: `rate_limited` at 429, in the two-field body, with `Retry-After`."""
    station = _register(station_client, rollback, "burst-station")
    statuses = [_heartbeat(station_client, station).status_code for _ in range(6)]

    refused = _heartbeat(station_client, station)

    assert statuses == [200] * HEARTBEAT_BURST
    assert refused.status_code == 429
    assert refused.json() == {
        "error": "rate_limited",
        "message": "Too many requests; retry after 10 s.",
    }
    assert refused.headers["Retry-After"] == str(HEARTBEAT_REFILL_S)


def test_the_heartbeat_bucket_refills_with_time(
    station_client: TestClient, rollback: Any, clock: Clock
) -> None:
    station = _register(station_client, rollback, "refill-station")
    for _ in range(HEARTBEAT_BURST):
        _heartbeat(station_client, station)
    assert _heartbeat(station_client, station).status_code == 429

    clock.now += HEARTBEAT_REFILL_S

    assert _heartbeat(station_client, station).status_code == 200
    assert _heartbeat(station_client, station).status_code == 429


def test_one_station_being_limited_does_not_limit_another(
    station_client: TestClient, rollback: Any
) -> None:
    """The bucket is the token's, not the address's: both share `testclient`."""
    noisy = _register(station_client, rollback, "noisy-station")
    quiet = _register(station_client, rollback, "quiet-station")
    for _ in range(HEARTBEAT_BURST + 3):
        _heartbeat(station_client, noisy)

    assert _heartbeat(station_client, quiet).status_code == 200


def test_an_unknown_token_is_limited_like_a_valid_one(
    station_client: TestClient,
) -> None:
    """The limiter does no lookup, so a guessed token cannot avoid it."""
    stranger = {"station_id": "st_000000", "token": "not-a-token-anyone-issued"}
    statuses = [_heartbeat(station_client, stranger).status_code for _ in range(7)]

    assert statuses == [401] * HEARTBEAT_BURST + [429]


def test_a_refused_request_costs_the_caller_nothing(
    station_client: TestClient, rollback: Any, clock: Clock
) -> None:
    """Hammering while refused must not push the next admission further away."""
    station = _register(station_client, rollback, "patient-station")
    for _ in range(HEARTBEAT_BURST + 20):
        _heartbeat(station_client, station)

    clock.now += HEARTBEAT_REFILL_S

    assert _heartbeat(station_client, station).status_code == 200


def test_every_msp_request_from_one_client_shares_its_bucket(
    client: TestClient,
) -> None:
    """Rotating tokens is still one address; `/time` needs no credential at all."""
    statuses = {client.get(TIME_PATH, headers=CURRENT).status_code for _ in range(299)}
    # A chunked body, so request_limits refuses it before any route runs: the
    # limiter, which sits outside that check, has charged it by then (D-202).
    rotated = client.post(
        "/msp/v0/heartbeat",
        content=iter([b"{}"]),
        headers={
            **CURRENT,
            "Authorization": "Bearer a-rotated-token",
            "Content-Type": "application/json",
        },
    )
    refused = client.get(TIME_PATH, headers=CURRENT)

    assert statuses == {200}
    assert rotated.status_code == 400
    assert refused.status_code == 429
    assert refused.json()["error"] == "rate_limited"


def test_the_public_api_answers_in_its_own_vocabulary(client: TestClient) -> None:
    """D-084: the same body shape, the public table's code and status."""
    for _ in range(PUBLIC_CLIENT_BURST):
        assert client.get(PUBLIC_PATH).status_code == 404

    refused = client.get(PUBLIC_PATH)

    assert refused.status_code == 429
    assert refused.json() == {
        "error": "rate_limited",
        "message": "Too many requests; retry after 1 s.",
    }
    assert refused.headers["Retry-After"] == "1"


def test_the_public_and_msp_buckets_are_separate(client: TestClient) -> None:
    for _ in range(PUBLIC_CLIENT_BURST + 5):
        client.get(PUBLIC_PATH)

    assert client.get(TIME_PATH, headers=CURRENT).status_code == 200


def test_the_dashboard_and_health_paths_are_not_limited(client: TestClient) -> None:
    """The healthcheck and the tunnel poll these; `/metrics` answers 404 unlocked."""
    for _ in range(MSP_CLIENT_BURST + 1):
        assert client.get("/").status_code != 429
    assert client.get("/metrics").status_code == 404


def test_a_trusted_header_separates_callers_behind_one_peer(
    client: TestClient, clock: Clock
) -> None:
    """Behind the tunnel all requests share one peer; the edge header splits them."""
    _limit(client, clock, client_header="cf-connecting-ip")
    for _ in range(PUBLIC_CLIENT_BURST):
        client.get(PUBLIC_PATH, headers={"CF-Connecting-IP": "198.51.100.7"})

    blocked = client.get(PUBLIC_PATH, headers={"CF-Connecting-IP": "198.51.100.7"})
    other = client.get(PUBLIC_PATH, headers={"CF-Connecting-IP": "203.0.113.9"})

    assert blocked.status_code == 429
    assert other.status_code == 404


def test_the_header_is_ignored_unless_it_is_configured(client: TestClient) -> None:
    """Otherwise any caller could name a fresh address per request (D-051)."""
    statuses = [
        client.get(
            PUBLIC_PATH, headers={"CF-Connecting-IP": f"198.51.100.{n}"}
        ).status_code
        for n in range(PUBLIC_CLIENT_BURST + 1)
    ]

    assert statuses[-1] == 429


def test_rate_limits_off_means_no_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    """`RATE_LIMITS=off`, for accelerated simulations on loopback (D-202)."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://meridian:meridian@127.0.0.1:1/x")
    monkeypatch.setenv("RATE_LIMITS", "off")
    with TestClient(create_app()) as started:
        statuses = {started.get(PUBLIC_PATH).status_code for _ in range(60)}

    assert statuses == {404}
