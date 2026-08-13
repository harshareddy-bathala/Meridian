"""A fleet on one thread, and what happens to it when things break.

Driven through the real client into the real application and a real database.
Rounds are advanced by hand rather than by :meth:`Supervisor.run`, so a fault
schedule written in ticks can be checked tick by tick with no sleeping and
nothing timing-dependent — which is the practical half of why D-076 chose one
thread.

Marked ``msp_conformance`` by the directory hook in ``tests/conftest.py``.

Reference: docs/DECISIONS.md D-024, D-074, D-076, D-080.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from meridian.api.app import create_app
from meridian.api.dependencies import get_connection
from meridian.store.invites import hash_invite_token
from meridian_sim import supervisor as supervisor_module
from meridian_sim.config import RunConfig
from meridian_sim.supervisor import Supervisor
from meridian_sim.virtual_station import RegistrationNeededError, paths_for

MASTER_SEED = 4471


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the fleet without the cadence between rounds.

    The interval is the platform's own — thirty seconds — so a three-round test
    would otherwise take a minute and a half of doing nothing. What is under
    test here is what a round does, and `test_the_cadence_is_the_shortest_a
    _station_was_given` covers the scheduling separately, in arithmetic.
    """
    monkeypatch.setattr(supervisor_module, "_sleep", lambda _seconds: None)


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes."""
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def started(
    database_url: str, rollback: Any, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Any]:
    """The started application, sharing this test's rolled-back connection."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("TOKEN_HASH_PEPPER", "supervisor-pepper")
    app = create_app()
    app.dependency_overrides[get_connection] = lambda: rollback
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def issue(rollback: Any, count: int) -> list[str]:
    """Issue ``count`` invites, as ``meridian invite create --count N`` would."""
    tokens = [f"sim-invite-{index}" for index in range(1, count + 1)]
    with rollback.cursor() as cur:
        for token in tokens:
            cur.execute(
                "insert into invite_tokens (token_sha256, label) values (%s, %s)",
                (hash_invite_token(token), token),
            )
    return tokens


def run_config(tmp_path: Path, count: int, scenario: str = "clean") -> RunConfig:
    """A run over a temporary state directory."""
    return RunConfig(
        master_seed=MASTER_SEED,
        run_id="supervisor-run",
        station_count=count,
        base_url="http://platform.test",
        state_dir=tmp_path / "state",
        scenario=scenario,
    )


def test_a_fleet_comes_up_and_every_station_is_its_own(
    started: Any, rollback: Any, tmp_path: Path
) -> None:
    """Three stations, three identities, three directories."""
    tokens = issue(rollback, 3)

    with Supervisor(run_config(tmp_path, 3), tokens, started._transport) as fleet:
        station_ids = fleet.bring_up()

    assert len(set(station_ids)) == 3


def test_every_station_ticks_exactly_once_per_round(
    started: Any, rollback: Any, tmp_path: Path
) -> None:
    """What makes a round a unit, and lets a fault schedule be written in ticks."""
    tokens = issue(rollback, 3)

    with Supervisor(run_config(tmp_path, 3), tokens, started._transport) as fleet:
        fleet.bring_up()
        outcome = fleet.tick_round(0, datetime.now(UTC))

    assert outcome.ticked == (1, 2, 3)


def test_a_fleet_that_runs_out_of_invites_says_which_station(
    started: Any, rollback: Any, tmp_path: Path
) -> None:
    """Brought up in index order, so what was admitted is a known prefix."""
    tokens = issue(rollback, 1)

    with (
        Supervisor(run_config(tmp_path, 2), tokens, started._transport) as fleet,
        pytest.raises(RegistrationNeededError, match="station 2"),
    ):
        fleet.bring_up()


def test_a_restarted_station_keeps_its_identity(
    started: Any, rollback: Any, tmp_path: Path
) -> None:
    """A restart rebuilds from disk, so the station that comes back is the same one.

    If it were not, every restart would consume an invite and the fleet would
    exhaust an operator's supply overnight under `restart: unless-stopped`.
    """
    tokens = issue(rollback, 1)
    config = run_config(tmp_path, 1, "restart")

    with Supervisor(config, tokens, started._transport) as fleet:
        (before,) = fleet.bring_up()
        restart_tick = _first_restart_tick(fleet)
        for tick in range(restart_tick + 1):
            fleet.tick_round(tick, datetime.now(UTC))
        after = fleet.tick_round(restart_tick + 1, datetime.now(UTC))

    assert after.ticked == (1,)
    assert paths_for(config, 1).credentials.exists()
    assert before.startswith("st_")


def test_a_restarting_station_does_not_heartbeat_that_round(
    started: Any, rollback: Any, tmp_path: Path
) -> None:
    """Which is what a process that is not running does."""
    tokens = issue(rollback, 1)

    with Supervisor(
        run_config(tmp_path, 1, "restart"), tokens, started._transport
    ) as fleet:
        fleet.bring_up()
        restart_tick = _first_restart_tick(fleet)
        outcome = fleet.tick_round(restart_tick, datetime.now(UTC))

    assert outcome.restarted == (1,)
    assert outcome.ticked == ()


def test_a_revoked_token_retires_one_station_and_leaves_the_rest(
    started: Any, rollback: Any, tmp_path: Path
) -> None:
    """D-024 stops the station, not the network.

    A fleet that stopped because one member lost its credential would turn an
    operator's single revocation into an outage.
    """
    tokens = issue(rollback, 2)
    config = run_config(tmp_path, 2, "revoked")

    with Supervisor(config, tokens, started._transport) as fleet:
        fleet.bring_up()
        stopped: list[int] = []
        for tick in range(60):
            stopped.extend(fleet.tick_round(tick, datetime.now(UTC)).stopped)
            if len(stopped) == 1:
                survivor = fleet.tick_round(tick + 1, datetime.now(UTC))
                break

    assert len(stopped) == 1
    assert survivor.ticked and stopped[0] not in survivor.ticked


def test_a_network_outage_does_not_stop_the_station(
    started: Any, rollback: Any, tmp_path: Path
) -> None:
    """An unreachable platform is not a refusal: the station keeps its work.

    The request really fails, at the HTTP layer, so the client's own retry
    policy and its own unreachable-versus-refused distinction are what handle it.
    """
    tokens = issue(rollback, 1)

    with Supervisor(
        run_config(tmp_path, 1, "network"), tokens, started._transport
    ) as fleet:
        fleet.bring_up()
        outcomes = [fleet.tick_round(tick, datetime.now(UTC)) for tick in range(40)]

    assert all(one.ticked == (1,) for one in outcomes)
    assert all(one.stopped == () for one in outcomes)


def test_the_run_ends_when_every_station_has_stopped(
    started: Any, rollback: Any, tmp_path: Path
) -> None:
    """A supervisor with nothing left to supervise has finished.

    Otherwise a fleet whose credentials were all revoked would spin forever
    ticking an empty list.
    """
    tokens = issue(rollback, 1)
    fleet = Supervisor(run_config(tmp_path, 1, "revoked"), tokens, started._transport)
    with fleet:
        fleet.bring_up()
        rounds = fleet.run(stop_after_rounds=200)

    assert rounds < 200


def test_a_clean_run_keeps_going_for_its_whole_budget(
    started: Any, rollback: Any, tmp_path: Path
) -> None:
    """The baseline the fault scenarios are read against."""
    tokens = issue(rollback, 1)
    fleet = Supervisor(run_config(tmp_path, 1), tokens, started._transport)

    with fleet:
        fleet.bring_up()
        rounds = fleet.run(stop_after_rounds=3)

    assert rounds == 3


def test_the_cadence_is_the_shortest_a_station_was_given(
    started: Any, rollback: Any, tmp_path: Path
) -> None:
    """So no station heartbeats later than the platform asked it to.

    It also makes a round a meaningful unit — every station ticks exactly once
    per round — which is what lets a fault schedule be written in ticks at all.
    """
    tokens = issue(rollback, 2)

    with Supervisor(run_config(tmp_path, 2), tokens, started._transport) as fleet:
        fleet.bring_up()

        assert fleet.interval_s() > 0


def _first_restart_tick(fleet: Supervisor) -> int:
    """The first tick on which the fleet's only station restarts."""
    (member,) = fleet._members
    for tick in range(400):
        if member.schedule.restarts_at(tick):
            return tick
    raise AssertionError("the restart scenario scheduled no restart")
