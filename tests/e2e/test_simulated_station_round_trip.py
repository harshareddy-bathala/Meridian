"""Stage 10's completion gate: a virtual station, from catalogue to observation.

*A virtual station registers, receives an assignment, submits an observation, and
keeps simulation provenance at every layer.*

Every link of the chain behind that sentence runs for real here — the catalogue
loader, pass generation over the real propagator, the scheduler, MSP delivery,
the reference client, and the observation store. The only thing stubbed is the
platform's clock, because a pass is where the sky puts it and a test cannot wait
six hours for one.

**The chain is asserted link by link, on purpose.** Five things have to produce
something for an assignment to exist, and every one of them fails the same way
— quietly, with a station that heartbeats happily and is never given anything to
do. A gate that only asserted the observation at the end would report "no
observation" for any of the five, which is the least useful sentence available.

Marked ``e2e`` by the directory hook in ``tests/conftest.py``.

Reference: docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md Stage 10; docs/DECISIONS.md
D-075, D-077, D-078, D-079.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from meridian.api import platform_clock
from meridian.api.app import create_app
from meridian.api.dependencies import get_connection
from meridian.catalogue_file import read_catalogue
from meridian.cli_catalogue import load_document
from meridian.orbit.skyfield_service import SkyfieldOrbitService
from meridian.pass_generation import GenerationHorizon, generate_passes
from meridian.scheduler.run import ScheduleRequest, run_schedule
from meridian.store.invites import hash_invite_token
from meridian.store.satellites import find_active_transmitters
from meridian_sim import supervisor as supervisor_module
from meridian_sim.config import RunConfig, seed_for_pass, seed_for_station
from meridian_sim.outcomes import decide_outcome
from meridian_sim.supervisor import Supervisor

MASTER_SEED = 4471
RUN_ID = "gate-run"
CATALOGUE = Path("deploy/catalogue/development.json")

GENERATION_HORIZON = timedelta(hours=24)
"""How far ahead the gate generates passes.

Long enough that a station somewhere on Earth certainly has one. The delivery
horizon is two hours (D-026), so the platform still hands out only what is
imminent — the clock is what this test moves, not the rules.
"""

LEAD_S = 60
"""How long before its window the station is told about a pass.

One tick's worth. Enough for the assignment to be delivered and held before the
window opens, which is the sequence a real station goes through.
"""


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fleet is ticked by hand here, so nothing should sleep."""
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
    monkeypatch.setenv("TOKEN_HASH_PEPPER", "gate-pepper")
    app = create_app()
    app.dependency_overrides[get_connection] = lambda: rollback
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def run_config(tmp_path: Path, scenario: str = "clean") -> RunConfig:
    """A one-station run over a temporary state directory."""
    return RunConfig(
        master_seed=MASTER_SEED,
        run_id=RUN_ID,
        station_count=1,
        base_url="http://platform.test",
        state_dir=tmp_path / "state",
        scenario=scenario,
    )


def issue_invite(rollback: Any, token: str = "gate-invite") -> str:
    """One invite, as ``meridian invite create`` would."""
    with rollback.cursor() as cur:
        cur.execute(
            "insert into invite_tokens (token_sha256, label) values (%s, %s)",
            (hash_invite_token(token), "stage-10-gate"),
        )
    return token


def earliest_assignment(
    rollback: Any, station_id: str
) -> tuple[str, datetime, datetime, float]:
    """The first pass this station was scheduled for, and how high it climbs."""
    with rollback.cursor() as cur:
        cur.execute(
            """
            select a.assignment_id, a.start_at, a.end_at, p.max_elevation_deg
            from assignments a
            join passes p on p.id = a.pass_id
            where a.station_id = %s and a.state = 'issued'
            order by a.start_at asc
            limit 1
            """,
            (station_id,),
        )
        row = cur.fetchone()
    assert row is not None, "the scheduler issued this station no assignments"
    return (str(row[0]), row[1], row[2], float(row[3]))


def current_observation(rollback: Any, assignment_id: str) -> dict[str, Any]:
    """The one current observation for an assignment, as a dictionary."""
    with rollback.cursor() as cur:
        cur.execute(
            """
            select outcome, simulated, peak_snr_db, signal_detected, station_id
            from observations_current where assignment_id = %s
            """,
            (assignment_id,),
        )
        rows = cur.fetchall()
    assert len(rows) == 1, f"expected one current observation, found {len(rows)}"
    return {
        "outcome": rows[0][0],
        "simulated": rows[0][1],
        "peak_snr_db": rows[0][2],
        "signal_detected": rows[0][3],
        "station_id": rows[0][4],
    }


def seed_the_platform(rollback: Any, station_id: str) -> tuple[int, int]:
    """Catalogue, passes and a schedule — the three steps an operator runs.

    Returns:
        How many passes and how many assignments the station ended up with,
        so the caller can say which link was empty rather than only that the
        end of the chain was.
    """
    orbit = SkyfieldOrbitService()
    now = datetime.now(UTC)

    load_document(rollback, read_catalogue(CATALOGUE))
    assert find_active_transmitters(rollback), "the catalogue loaded no transmitters"

    generated = generate_passes(
        rollback, orbit, GenerationHorizon(start=now, end=now + GENERATION_HORIZON)
    )
    assert generated.passes_stored > 0, (
        "pass generation stored nothing: the station's declared capabilities and"
        " the catalogue's transmitters do not meet"
    )

    scheduled = run_schedule(
        rollback,
        orbit,
        ScheduleRequest(
            start=now,
            end=now + GENERATION_HORIZON,
            model_config="A",
            turnaround_s=0.0,
        ),
    )
    assert scheduled.scheduled > 0, "the scheduler took none of the generated passes"

    with rollback.cursor() as cur:
        cur.execute(
            "select count(*) from assignments where station_id = %s", (station_id,)
        )
        assignments = int(cur.fetchone()[0])
    return (generated.passes_stored, assignments)


def test_the_gate(
    started: Any, rollback: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    """The whole sentence, in one run.

    Registers, is scheduled real passes over real orbital geometry, receives one
    over MSP, executes it, and reports what it heard — with `simulated` true on
    every row the platform wrote.
    """
    config = run_config(tmp_path)
    fleet = Supervisor(config, [issue_invite(rollback)], started._transport)

    with fleet:
        (station_id,) = fleet.bring_up()
        passes, assignments = seed_the_platform(rollback, station_id)
        assert assignments > 0, f"{passes} passes produced no assignment"

        assignment_id, start_at, end_at, elevation_deg = earliest_assignment(
            rollback, station_id
        )
        _freeze(monkeypatch, start_at - timedelta(seconds=LEAD_S))

        delivered = fleet.tick_round(0, start_at - timedelta(seconds=LEAD_S))
        executing = fleet.tick_round(1, start_at + timedelta(seconds=10))
        finished = fleet.tick_round(2, end_at + timedelta(seconds=10))

    assert delivered.ticked == (1,)
    assert executing.ticked == (1,)
    assert finished.submitted == (assignment_id,), (
        "the station executed the pass but the observation did not land"
    )

    observation = current_observation(rollback, assignment_id)
    assert observation["station_id"] == station_id
    assert _state_of(rollback, assignment_id) == "reported"

    # D-077's second assertion: what the platform stored is what the model
    # decided, so the decision survived the whole chain rather than something
    # plausible being produced somewhere along it.
    expected = decide_outcome(
        seed_for_pass(seed_for_station(MASTER_SEED, 1), assignment_id), elevation_deg
    )
    assert observation["outcome"] == expected.outcome
    assert observation["peak_snr_db"] == expected.peak_snr_db


def test_provenance_survives_every_layer(
    started: Any, rollback: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    """CLAUDE.md rule 5: simulated data is labelled at every layer.

    Checked as four separate rows rather than one, because each is written by a
    different part of the platform and each takes the flag from the station's
    registration rather than from anything the station later sends (D-048). One
    of them losing it would be a correctness bug that no other test would see.
    """
    config = run_config(tmp_path)
    fleet = Supervisor(config, [issue_invite(rollback)], started._transport)

    with fleet:
        (station_id,) = fleet.bring_up()
        seed_the_platform(rollback, station_id)
        assignment_id, start_at, end_at, _ = earliest_assignment(rollback, station_id)
        _freeze(monkeypatch, start_at - timedelta(seconds=LEAD_S))

        fleet.tick_round(0, start_at - timedelta(seconds=LEAD_S))
        fleet.tick_round(1, start_at + timedelta(seconds=10))
        fleet.tick_round(2, end_at + timedelta(seconds=10))

    assert _flag(rollback, "stations", "station_id", station_id) is True
    assert _flag(rollback, "heartbeats", "station_id", station_id) is True
    assert _flag(rollback, "assignments", "assignment_id", assignment_id) is True
    assert current_observation(rollback, assignment_id)["simulated"] is True


def test_a_second_run_reports_the_same_thing_about_the_same_pass(
    started: Any, rollback: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    """A resubmission of an unchanged body writes nothing (D-015).

    The station's conclusion about a pass is a function of its seed and the
    assignment id, so a restart that re-executed the pass would produce the
    identical body — and the platform answers with the identical id rather than
    appending a revision. Determinism and idempotency have to agree here or the
    observation store fills with revisions recording no change.
    """
    config = run_config(tmp_path)
    fleet = Supervisor(config, [issue_invite(rollback)], started._transport)

    with fleet:
        (station_id,) = fleet.bring_up()
        seed_the_platform(rollback, station_id)
        assignment_id, start_at, end_at, _ = earliest_assignment(rollback, station_id)
        _freeze(monkeypatch, start_at - timedelta(seconds=LEAD_S))

        fleet.tick_round(0, start_at - timedelta(seconds=LEAD_S))
        fleet.tick_round(1, start_at + timedelta(seconds=10))
        fleet.tick_round(2, end_at + timedelta(seconds=10))
        fleet.tick_round(3, end_at + timedelta(seconds=40))

    with rollback.cursor() as cur:
        cur.execute(
            "select count(*) from observations where assignment_id = %s",
            (assignment_id,),
        )
        assert int(cur.fetchone()[0]) == 1


def test_a_station_whose_radio_is_down_reports_not_attempted(
    started: Any, rollback: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    """MSP §4.4's hardest distinction, end to end.

    A dead receiver does not stop the client: the station registers, heartbeats,
    holds the assignment and says nothing is wrong. What it cannot do is
    receive. Reporting `no_signal` would put a measurement in the store that no
    receiver produced, and reporting nothing would look like a decline it never
    made — so the only honest answer is `not_attempted`, and it has to survive
    the whole chain to be worth anything.

    Driven through the supervisor rather than by constructing an executor,
    because the wiring between the fault schedule and the receiver is exactly
    what a unit test of the executor cannot see.
    """
    config = run_config(tmp_path, scenario="receiver")
    fleet = Supervisor(config, [issue_invite(rollback)], started._transport)

    with fleet:
        (station_id,) = fleet.bring_up()
        seed_the_platform(rollback, station_id)
        assignment_id, start_at, end_at, _ = earliest_assignment(rollback, station_id)
        _freeze(monkeypatch, start_at - timedelta(seconds=LEAD_S))

        first = _first_tick_with_a_dead_radio(fleet, wanted=3)
        fleet.tick_round(first, start_at - timedelta(seconds=LEAD_S))
        fleet.tick_round(first + 1, start_at + timedelta(seconds=10))
        fleet.tick_round(first + 2, end_at + timedelta(seconds=10))

    assert current_observation(rollback, assignment_id)["outcome"] == "not_attempted"


def _first_tick_with_a_dead_radio(fleet: Supervisor, wanted: int) -> int:
    """The first tick with ``wanted`` consecutive ticks of a down receiver."""
    (member,) = fleet._members
    for tick in range(500):
        window = range(tick, tick + wanted)
        if all("receiver_down" in member.schedule.active_at(one) for one in window):
            return tick
    raise AssertionError("the receiver scenario never keeps the radio down long enough")


def _freeze(monkeypatch: Any, instant: datetime) -> None:
    """Hold the platform's clock still at ``instant``.

    The one thing stubbed in this file. A pass is where the sky puts it, which
    may be six hours away, and the delivery horizon is two (D-026) — so either
    the clock moves or the test waits. Everything else, including the geometry
    that decided when the pass is, is real.
    """
    monkeypatch.setattr(platform_clock, "utc_now", lambda: instant)


def _state_of(rollback: Any, assignment_id: str) -> str:
    """One assignment's state."""
    with rollback.cursor() as cur:
        cur.execute(
            "select state from assignments where assignment_id = %s", (assignment_id,)
        )
        return str(cur.fetchone()[0])


def _flag(rollback: Any, table: str, key: str, value: str) -> bool:
    """The ``simulated`` flag on one row of one table."""
    with rollback.cursor() as cur:
        cur.execute(
            f"select simulated from {table} where {key} = %s limit 1",
            (value,),
        )
        row = cur.fetchone()
    assert row is not None, f"no row in {table} for {value}"
    return bool(row[0])
