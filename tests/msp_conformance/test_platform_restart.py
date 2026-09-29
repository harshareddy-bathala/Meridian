"""A platform restart, as a fleet of stations lives through it.

Stage 21's platform restart, in process. The stations talk to one application,
the process goes away — its lifespan shuts down, its pool closes, and every
request meets a refused connection — and a new one starts on the same database.
What has to survive is everything stored. What must not happen is a station
registering again, stopping, or losing what it had queued.

The host tool (``deploy/tools/chaos.py``) does the same to a real deployment
with ``docker compose restart api``. This is the version CI can run.

Marked ``msp_conformance`` by the directory hook in ``tests/conftest.py``.

Reference: docs/DECISIONS.md D-024, D-080, D-194.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from meridian.api.app import create_app
from meridian.api.dependencies import get_connection
from meridian.store.invites import hash_invite_token
from meridian_client import transport as transport_module
from meridian_sim import supervisor as supervisor_module
from meridian_sim.config import RunConfig
from meridian_sim.supervisor import Supervisor

MASTER_SEED = 4471
STATIONS = 3
START = datetime.now(UTC)


class Switchboard(httpx.BaseTransport):
    """Where a fleet's requests go: a running platform process, or nothing.

    ``None`` is the gap between the old process stopping and the new one
    listening, which a station meets as a refused connection.
    """

    def __init__(self) -> None:
        self.inner: httpx.BaseTransport | None = None

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        """Forward to the process that is up, or refuse as a stopped one does."""
        if self.inner is None:
            raise httpx.ConnectError("platform process is down", request=request)
        return self.inner.handle_request(request)


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither the fleet's cadence nor the client's backoff is under test."""
    monkeypatch.setattr(supervisor_module, "_sleep", lambda _seconds: None)
    monkeypatch.setattr(transport_module, "_sleep", lambda _seconds: None)


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes, across both processes."""
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def platform(
    database_url: str, rollback: Any, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Any]:
    """Starts a platform process on demand; each shares the one database."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("TOKEN_HASH_PEPPER", "restart-pepper")
    started: list[TestClient] = []

    def start() -> TestClient:
        app = create_app()
        app.dependency_overrides[get_connection] = lambda: rollback
        client = TestClient(app)
        client.__enter__()
        started.append(client)
        return client

    yield start
    for client in started:
        client.__exit__(None, None, None)


def heartbeats_per_station(rollback: Any) -> dict[str, int]:
    """How many heartbeats each station has had accepted."""
    with rollback.cursor() as cur:
        cur.execute("select station_id, count(*) from heartbeats group by station_id")
        return {station_id: count for station_id, count in cur.fetchall()}


def station_count(rollback: Any) -> int:
    """How many stations are registered."""
    with rollback.cursor() as cur:
        cur.execute("select count(*) from stations")
        (count,) = cur.fetchone()
        return int(count)


def test_a_fleet_lives_through_a_platform_restart(
    platform: Any, rollback: Any, tmp_path: Path
) -> None:
    """Nobody registers twice, nobody stops, and every station is heard again."""
    tokens = [f"restart-invite-{index}" for index in range(1, STATIONS + 1)]
    with rollback.cursor() as cur:
        for token in tokens:
            cur.execute(
                "insert into invite_tokens (token_sha256, label) values (%s, %s)",
                (hash_invite_token(token), token),
            )
    config = RunConfig(
        master_seed=MASTER_SEED,
        run_id="restart-run",
        station_count=STATIONS,
        base_url="http://platform.test",
        state_dir=tmp_path / "state",
    )
    board = Switchboard()
    first = platform()
    board.inner = first._transport
    registered_before = station_count(rollback)

    with Supervisor(config, tokens, board) as fleet:
        station_ids = fleet.bring_up()
        for tick in range(2):
            fleet.tick_round(tick, START + timedelta(seconds=30 * tick))
        before = heartbeats_per_station(rollback)

        first.__exit__(None, None, None)
        board.inner = None
        for tick in range(2, 4):
            down = fleet.tick_round(tick, START + timedelta(seconds=30 * tick))
            assert down.stopped == ()
        during = heartbeats_per_station(rollback)

        board.inner = platform()._transport
        for tick in range(4, 6):
            after_restart = fleet.tick_round(tick, START + timedelta(seconds=30 * tick))
            assert after_restart.stopped == ()
        after = heartbeats_per_station(rollback)

    assert during == before
    assert all(after[one] > before[one] for one in station_ids)
    assert station_count(rollback) == registered_before + STATIONS
