"""A write is committed before its response leaves the platform.

A station that registers and heartbeats straight away must find its token
already committed. FastAPI's default request scope returns the connection, and
therefore commits, *after* the response is sent, which refused exactly that
first heartbeat with 401 during the Stage 11 public rehearsal.

Unlike the other files here, nothing is rolled back through an override: the
point is to watch the real commit, so the rows are removed explicitly at the end.
The check runs inside the ASGI ``send`` call for the response body, on a second
connection, which is the moment a client could first act on the answer.

Marked ``msp_conformance`` by the directory hook in ``tests/conftest.py``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from meridian.api.app import create_app
from meridian.store.invites import hash_invite_token

REGISTER_PATH = "/msp/v0/register"
INVITE = "commit-before-response-invite"
LABEL = "commit-before-response"
API = Path(__file__).resolve().parents[2] / "platform/src/meridian/api"
DECLARATION = re.compile(r"Depends\(\s*get_connection\b[^)]*\)")


def committed_when_answered(
    app: ASGIApp, database_url: str, seen: list[bool]
) -> ASGIApp:
    """Wrap ``app`` to record whether the registered station is visible yet."""

    async def wrapped(scope: Scope, receive: Receive, send: Send) -> None:
        async def spy(message: Message) -> None:
            if (
                scope.get("path") == REGISTER_PATH
                and message["type"] == "http.response.body"
            ):
                station_id = json.loads(message["body"])["station_id"]
                with psycopg.connect(database_url) as other:
                    row = other.execute(
                        "select 1 from stations where station_id = %s", (station_id,)
                    ).fetchone()
                seen.append(row is not None)
            await send(message)

        await app(scope, receive, spy)

    return wrapped


@pytest.fixture
def invite(database_url: str) -> Iterator[None]:
    """A committed invite, and every row registration adds, removed afterwards."""
    with psycopg.connect(database_url, autocommit=True) as db:
        db.execute(
            "insert into invite_tokens (token_sha256, label) values (%s, %s)",
            (hash_invite_token(INVITE), LABEL),
        )
        yield
        station = db.execute(
            "select consumed_by_station_id from invite_tokens where label = %s",
            (LABEL,),
        ).fetchone()
        db.execute("delete from invite_tokens where label = %s", (LABEL,))
        if station and station[0]:
            db.execute(
                "delete from station_capabilities where station_id = %s", station
            )
            db.execute("delete from stations where station_id = %s", station)


@pytest.mark.usefixtures("invite")
def test_a_registration_is_committed_before_it_is_answered(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("TOKEN_HASH_PEPPER", "commit-before-response-pepper")
    seen: list[bool] = []
    app = committed_when_answered(create_app(), database_url, seen)

    with TestClient(app) as client:
        response = client.post(
            REGISTER_PATH, json=payload(), headers={"MSP-Version": "0.1"}
        )

    assert response.status_code == 200, response.text
    assert seen == [True]


def test_every_route_commits_before_answering() -> None:
    """No route borrows a connection that is returned after the response.

    Read from the source rather than from ``app.routes``: FastAPI wraps an
    included router in a private class, and a walk that silently stops at it
    passes whatever the routes declare.
    """
    offenders = [
        f"{path.relative_to(API)}: {match.group(0)}"
        for path in sorted(API.rglob("*.py"))
        for match in DECLARATION.finditer(path.read_text())
        if 'scope="function"' not in match.group(0)
    ]

    assert offenders == []


def payload() -> dict[str, Any]:
    return {
        "invite_token": INVITE,
        "registration_key": "commit-before-response-key",
        "name": "commit-before-response",
        "operator": "conformance",
        "location": {"lat": 12.97, "lon": 77.59, "alt_m": 920},
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
    }
