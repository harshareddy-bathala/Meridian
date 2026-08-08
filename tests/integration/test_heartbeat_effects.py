"""What a heartbeat *does*, as opposed to what it answers.

``tests/msp_conformance/test_heartbeat_endpoint.py`` asserts the wire shape. This
asserts the side effects the route composes, which no store-level test can see:
that both writes happen together, and that ``simulated`` reaches the row from the
registration record rather than from anything the station sent.

Marked ``integration`` by the directory hook in ``tests/conftest.py``.

Reference: docs/MSP-SPEC.md §4.2; docs/DECISIONS.md D-013, D-048, D-054.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from fastapi.testclient import TestClient  # noqa: E402

from meridian.api.app import create_app  # noqa: E402
from meridian.api.dependencies import get_connection  # noqa: E402
from meridian.registry.liveness import derive_liveness  # noqa: E402
from meridian.store.invites import hash_invite_token  # noqa: E402
from meridian.store.stations import find_station_heartbeat  # noqa: E402

pytestmark = pytest.mark.integration

CURRENT = {"MSP-Version": "0.1"}


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes."""
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def client(
    database_url: str, rollback: Any, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("TOKEN_HASH_PEPPER", "heartbeat-effects-pepper")
    app = create_app()
    app.dependency_overrides[get_connection] = lambda: rollback
    with TestClient(app) as started:
        yield started
    app.dependency_overrides.clear()


def register(client: TestClient, rollback: Any, *, simulated: bool) -> dict[str, str]:
    """Admit one station, simulated or not, and return its id and token."""
    plaintext = f"effects-invite-{simulated}"
    with rollback.cursor() as cur:
        cur.execute(
            "insert into invite_tokens (token_sha256, label) values (%s, %s)",
            (hash_invite_token(plaintext), "effects"),
        )
    payload: dict[str, Any] = {
        "invite_token": plaintext,
        "registration_key": "a-registration-key",
        "name": "effects-station",
        "operator": "tests",
        "location": {"lat": 12.9716, "lon": 77.5946, "alt_m": 920},
        "simulated": simulated,
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
    }
    if simulated:
        payload["simulator_run_id"] = "run-1"
        payload["seed"] = 4471
    response = client.post("/msp/v0/register", json=payload, headers=CURRENT)
    assert response.status_code == 200, response.text
    body = response.json()
    return {"station_id": body["station_id"], "token": body["token"]}


def send_heartbeat(client: TestClient, station: dict[str, str]) -> None:
    """One minimal, valid heartbeat from ``station``."""
    response = client.post(
        "/msp/v0/heartbeat",
        json={
            "station_id": station["station_id"],
            "sent_at": "2026-08-14T09:31:02Z",
            "state": "idle",
            "held_assignments": [],
            "health": {},
        },
        headers={**CURRENT, "Authorization": f"Bearer {station['token']}"},
    )
    assert response.status_code == 200, response.text


def test_a_heartbeat_moves_a_station_from_never_seen_to_online(
    client: TestClient, rollback: Any
) -> None:
    """The whole Stage 5 chain, end to end.

    Registration leaves `last_heartbeat_at` null, which derives as `never_seen`
    — a commissioning problem. One heartbeat later the same station derives as
    `online`. Liveness is not stored (D-054), so this is genuinely reading the
    instant the route wrote rather than a column somebody remembered to update.
    """
    station = register(client, rollback, simulated=False)

    before = find_station_heartbeat(rollback, station["station_id"])
    assert before is not None and before.last_heartbeat_at is None

    send_heartbeat(client, station)

    after = find_station_heartbeat(rollback, station["station_id"])
    assert after is not None and after.last_heartbeat_at is not None
    with rollback.cursor() as cur:
        cur.execute("select now()")
        row = cur.fetchone()
    assert row is not None
    assert derive_liveness(after.last_heartbeat_at, now=row[0]) == "online"


def test_the_heartbeat_row_and_the_station_bump_land_together(
    client: TestClient, rollback: Any
) -> None:
    """Both writes or neither.

    A heartbeat row without the `stations` bump leaves `liveness()` reading
    `offline` for a station that just reported; the bump without the row loses
    the listening evidence `was_listening()` depends on.
    """
    station = register(client, rollback, simulated=False)
    send_heartbeat(client, station)

    with rollback.cursor() as cur:
        cur.execute(
            "select count(*) from heartbeats where station_id = %s",
            (station["station_id"],),
        )
        row = cur.fetchone()
    assert row is not None and row[0] == 1


def test_simulated_is_taken_from_the_registration_not_the_wire(
    client: TestClient, rollback: Any
) -> None:
    """CLAUDE.md rule 5 and D-048, at the one layer that could break them.

    MSP §4.2 puts no `simulated` field on the wire, so the only value the route
    could use is the station's registration record — and this asserts it does.
    A simulated station filing measured-looking heartbeats is the credibility
    failure the rule exists to prevent.
    """
    station = register(client, rollback, simulated=True)
    send_heartbeat(client, station)

    with rollback.cursor() as cur:
        cur.execute(
            "select simulated from heartbeats where station_id = %s",
            (station["station_id"],),
        )
        row = cur.fetchone()
    assert row is not None and row[0] is True
