"""Stage 21's gate: a faulted fleet, through real MSP, judged from what was stored.

Five virtual stations run the ``chaos`` scenario — every recurring station fault
at once — against the real application and database, on a stated clock. Every
round the scheduler decides the next two hours; afterwards every settled pass
is classified; then ``meridian reliability faults``'s own function judges the
run's ledger against the platform's records (D-192). The gate is that every
fault passes, that enough of them were actually tested, and that the verdict
fails when the platform's record of handling one is removed.

**On a stated clock, not the wall clock.** The stations are handed each
round's instant, and ``platform_clock.utc_now`` returns the same one, so every
heartbeat is stored at the instant the round says (D-195). An hour of faults
runs in seconds, and ninety seconds of silence is ninety seconds.

Reference: docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md Stage 21's completion gate;
docs/DECISIONS.md D-171, D-188, D-189, D-192, D-195.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from meridian.api import platform_clock
from meridian.api.app import create_app
from meridian.api.dependencies import get_connection
from meridian.orbit.skyfield_service import SkyfieldOrbitService
from meridian.registry.psycopg_registry import PsycopgRegistry
from meridian.reliability.accounting import classify_settled
from meridian.reliability.config import ReliabilityConfig
from meridian.reliability.fault_check import check_faults
from meridian.reliability.faults import FaultVerdict, read_fault_ledger
from meridian.scheduler.candidates import ScheduleRequest
from meridian.scheduler.run import run_schedule
from meridian.scheduler.schedule_config import ScheduleConfig
from meridian.store.invites import hash_invite_token
from meridian_client import transport as transport_module
from meridian_sim import supervisor as supervisor_module
from meridian_sim.config import RunConfig
from meridian_sim.faults import PARTITION, SCENARIOS
from meridian_sim.ledger import FaultLedger
from meridian_sim.supervisor import Supervisor

MASTER_SEED = 4471
STATIONS = 5
SCENARIO = "chaos"
TICKS = 180
"""Ninety minutes at the thirty-second cadence.

The last pass ends about seventy-eight minutes in. The rest is time for its
report to arrive through whatever upload fault is open: a run that stopped at
the last window's close would leave a listening station's report unsent, and
Stage 20 rightly counts that as a miss.
"""

ROUND_EVERY = 2
"""A scheduling round every minute, so most offline spells contain one."""

HORIZON = timedelta(hours=2)
T0 = datetime(2026, 8, 14, tzinfo=UTC)
SETTLED = T0 + timedelta(days=3)
"""When the passes are classified and the run judged: every pass has settled."""

PASS_MINUTES = (6, 20, 34, 48, 62)
"""Where each station's passes begin, offset by a minute per station."""

METEOR = "norad:57166"
LINE1 = "1 57166U 23091A   26220.09250000  .00000098  00000-0  61234-4 0  9995"
LINE2 = "2 57166  98.7123 201.3345 0002145  85.1234 275.0123 14.22150000123456"


class Clock:
    """The one instant the platform and the fleet agree it is."""

    def __init__(self) -> None:
        self.now = T0


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither the fleet's cadence nor the client's backoff is under test."""
    monkeypatch.setattr(supervisor_module, "_sleep", lambda _seconds: None)
    monkeypatch.setattr(transport_module, "_sleep", lambda _seconds: None)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    """The platform's clock, stated rather than read (D-195)."""
    stated = Clock()
    monkeypatch.setattr(platform_clock, "utc_now", lambda: stated.now)
    return stated


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything the run writes."""
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def platform(
    database_url: str, rollback: Any, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """The application, sharing this test's rolled-back connection."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("TOKEN_HASH_PEPPER", "fault-gate-pepper")
    # An hour of heartbeats arrives in seconds of wall time here; D-202's
    # switch for accelerated simulations on loopback is what keeps the rate
    # limiter, which counts real time, from refusing them.
    monkeypatch.setenv("RATE_LIMITS", "off")
    app = create_app()
    app.dependency_overrides[get_connection] = lambda: rollback
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def satellite(rollback: Any) -> int:
    """One LRPT satellite the fleet can hear, with an element set; its set id."""
    with rollback.cursor() as cur:
        cur.execute(
            "insert into satellites (satellite_id, name, priority)"
            " values (%s, 'Meteor-M', 1.0)",
            (METEOR,),
        )
        cur.execute(
            "insert into satellite_transmitters (satellite_id, centre_freq_hz, mode)"
            " values (%s, 137100000, 'lrpt')",
            (METEOR,),
        )
        cur.execute(
            "insert into element_sets (satellite_id, epoch, line1, line2, source)"
            " values (%s, %s, %s, %s, 'manual') returning id",
            (METEOR, T0 - timedelta(days=1), LINE1, LINE2),
        )
        (element_set_id,) = cur.fetchone()
        return int(element_set_id)


def passes(rollback: Any, station_ids: tuple[str, ...], element_set_id: int) -> None:
    """Five eleven-minute passes for each station, staggered by a minute."""
    with rollback.cursor() as cur:
        for offset, station_id in enumerate(station_ids):
            for minute in PASS_MINUTES:
                aos = T0 + timedelta(minutes=minute + offset)
                cur.execute(
                    "insert into passes (satellite_id, station_id, aos, los,"
                    " max_elevation_deg, max_elevation_at, aos_azimuth_deg,"
                    " los_azimuth_deg, element_set_id, min_elevation_deg, simulated)"
                    " values (%s, %s, %s, %s, 40, %s, 10, 200, %s, 10, true)",
                    (
                        METEOR,
                        station_id,
                        aos,
                        aos + timedelta(minutes=11),
                        aos + timedelta(minutes=5),
                        element_set_id,
                    ),
                )


def invites(rollback: Any) -> list[str]:
    """One invite per station, as ``meridian invite create --count N`` issues them."""
    tokens = [f"gate-invite-{index}" for index in range(1, STATIONS + 1)]
    with rollback.cursor() as cur:
        for token in tokens:
            cur.execute(
                "insert into invite_tokens (token_sha256, label) values (%s, %s)",
                (hash_invite_token(token), token),
            )
    return tokens


def judge(rollback: Any, ledger: Path) -> tuple[FaultVerdict, ...]:
    """What ``meridian reliability faults --ledger`` would print, as values."""
    with ledger.open(encoding="utf-8") as handle:
        faults = read_fault_ledger(handle)
    return check_faults(rollback, faults, now=SETTLED)


def failures(verdicts: tuple[FaultVerdict, ...]) -> list[str]:
    """Every failed check, readable in an assertion message."""
    return [
        f"{one.fault.kind} on {one.fault.target}: {check.name}: {check.detail}"
        for one in verdicts
        for check in one.checks
        if check.passed is False
    ]


def answered(verdicts: tuple[FaultVerdict, ...], name: str) -> list[str]:
    """The details of every passing answer to one check."""
    return [
        check.detail
        for one in verdicts
        for check in one.checks
        if check.name == name and check.passed is True
    ]


@pytest.fixture
def run(platform: TestClient, rollback: Any, clock: Clock, tmp_path: Path) -> Path:
    """Run the faulted fleet, schedule it every round, classify; the ledger."""
    element_set_id = satellite(rollback)
    config = RunConfig(
        master_seed=MASTER_SEED,
        run_id="fault-gate",
        station_count=STATIONS,
        base_url="http://platform.test",
        state_dir=tmp_path / "state",
        scenario=SCENARIO,
    )
    ledger = tmp_path / "faults.jsonl"
    orbit = SkyfieldOrbitService()

    with Supervisor(
        config,
        invites(rollback),
        platform._transport,
        ledger=FaultLedger(ledger, config.run_id),
    ) as fleet:
        clock.now = T0 - timedelta(minutes=1)
        station_ids = fleet.bring_up()
        passes(rollback, station_ids, element_set_id)
        for tick in range(TICKS):
            clock.now = T0 + tick * timedelta(seconds=30)
            if tick % ROUND_EVERY == 0:
                run_schedule(
                    rollback,
                    orbit,
                    ScheduleRequest(
                        start=clock.now,
                        end=clock.now + HORIZON,
                        now=clock.now,
                        config=ScheduleConfig(),
                    ),
                )
            fleet.tick_round(tick, clock.now)

    clock.now = SETTLED
    registry = PsycopgRegistry(
        rollback, pepper="fault-gate-pepper", recovery_window_s=3600, now_utc=SETTLED
    )
    classify_settled(
        rollback,
        registry,
        now=SETTLED,
        config=ReliabilityConfig().classification,
    )
    return ledger


def test_every_injected_fault_is_detected_and_handled(run: Path, rollback: Any) -> None:
    """The completion gate: no fault fails any question it is asked."""
    verdicts = judge(rollback, run)

    assert failures(verdicts) == []


def test_the_gate_tested_what_it_claims_to(run: Path, rollback: Any) -> None:
    """A gate every fault passes by not applying proves nothing: count the answers.

    Every station fault kind was injected; stations went offline and were
    detected; a round ran while one was offline and revoked its work; a
    station declined work in time and had it revoked; and passes a fault
    touched were classified, none of them as a miss.
    """
    verdicts = judge(rollback, run)
    kinds = Counter(one.fault.kind for one in verdicts)

    assert set(kinds) == set(SCENARIOS[SCENARIO]), kinds
    assert kinds[PARTITION] >= 2
    assert any("offline" in one for one in answered(verdicts, "detected"))
    assert any("revoked" in one for one in answered(verdicts, "replanned"))
    assert any(
        not one.startswith("0 of") for one in answered(verdicts, "declines_honoured")
    )
    assert any(
        not one.startswith("0 of") for one in answered(verdicts, "no_false_miss")
    )


def test_a_revocation_removed_from_the_record_fails_the_gate(
    run: Path, rollback: Any
) -> None:
    """Positive control: the scheduler's handling is what the verdict reads.

    One offline revocation is removed from the history — from a round that
    revoked more than one, so the round itself is still on the record and the
    verdict is owed an answer about it (D-196).
    """
    with rollback.cursor() as cur:
        cur.execute(
            """
            select e.assignment_id from assignment_revocations e
            where e.event = 'revoked' and e.reason = 'offline'
              and (select count(*) from assignment_revocations f
                    where f.at = e.at and f.event = 'revoked'
                      and f.reason = 'offline') >= 2
            order by e.at, e.assignment_id limit 1
            """
        )
        (assignment_id,) = cur.fetchone()
        cur.execute(
            "delete from assignment_revocations where assignment_id = %s",
            (assignment_id,),
        )

    assert any(
        "replanned" in line and assignment_id in line
        for line in failures(judge(rollback, run))
    )


def test_a_planted_confirmed_miss_fails_the_gate(run: Path, rollback: Any) -> None:
    """Positive control: a pass the station reported, classified as a miss.

    Planted as a second classification under another method, the way a wrong
    rule would write one; the verdict reads a pass as missed if any method says
    so, and a reported pass is never a miss (CLAUDE.md rule 7).
    """
    verdicts = judge(rollback, run)
    touched = [
        one.fault
        for one in verdicts
        for check in one.checks
        if check.name == "no_false_miss"
        and check.passed is True
        and not check.detail.startswith("0 of")
    ]
    with rollback.cursor() as cur:
        cur.execute(
            """
            insert into pass_classifications
                (assignment_id, assignment_ids, pass_id, station_id, satellite_id,
                 window_start, window_end, classification, evidence, method,
                 config_sha256, simulated)
            select c.assignment_id, c.assignment_ids, c.pass_id, c.station_id,
                   c.satellite_id, c.window_start, c.window_end, 'confirmed_miss',
                   '{}'::jsonb, 'planted', c.config_sha256, c.simulated
            from pass_classifications c
            where c.station_id = %s
              and c.window_start < %s and c.window_end > %s
              and exists (select 1 from observations o
                          where o.assignment_id = any(c.assignment_ids))
            order by c.window_start limit 1
            returning assignment_id
            """,
            (
                touched[0].station_id,
                touched[0].closed_at or SETTLED,
                touched[0].opened_at,
            ),
        )
        (planted,) = cur.fetchone()

    assert any(
        "no_false_miss" in line and planted in line
        for line in failures(judge(rollback, run))
    )
