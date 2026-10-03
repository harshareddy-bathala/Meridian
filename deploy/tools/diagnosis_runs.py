"""``deploy/tools/diagnosis_runs.py`` — simulated fleets, diagnosed and sealed for SC-8.

SC-8 asks whether the diagnosis names the injected cause. Its ground truth is
the simulator's ledger, which never enters the platform (D-105), so the figure
is made in two halves: this tool runs a fleet under a fault scenario against
the real platform, in process, lets the platform classify and diagnose every
loss from its own records, and seals the result with the ledger as a diagnosis
run (``meridian.datasets.diagnosis_runs``). ``meridian report build
--diagnoses`` judges the sealed runs; nothing here judges anything.

**Unlike every other tool here it needs the workspace**, ``uv sync`` and
``uv run``: it runs the platform and the simulator in one process, which is the
only way their two halves meet without the ledger reaching the database.

**Each run gets a database of its own**, created on the server ``--database-url``
names, migrated, and dropped afterwards. Station ids are drawn from the seed
rather than at random, so pass and assignment ids, and with them every pass's
outcome, follow from the seed and the scenario: a run made again from its seed
is the same run (D-278).

    uv run python deploy/tools/diagnosis_runs.py \\
        --config analysis/configs/diagnosis.toml.example \\
        --database-url postgresql://meridian:…@localhost:5432/meridian \\
        --root "$MERIDIAN_DATASETS_ROOT"

Reference: docs/DECISIONS.md D-105, D-138, D-189, D-272, D-277, D-278.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import tempfile
import tomllib
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from meridian.api import platform_clock
from meridian.api.app import create_app
from meridian.api.dependencies import get_connection
from meridian.catalogue_file import read_catalogue
from meridian.cli_catalogue import load_document
from meridian.datasets.diagnosis_runs import publish_diagnosis_run
from meridian.orbit.skyfield_service import SkyfieldOrbitService
from meridian.pass_generation import GenerationHorizon, generate_passes
from meridian.registry import psycopg_registry
from meridian.registry.psycopg_registry import PsycopgRegistry
from meridian.reliability import classification
from meridian.reliability.accounting import classify_settled
from meridian.reliability.config import ReliabilityConfig
from meridian.reliability.diagnosis import METHOD
from meridian.reliability.diagnosis_run import diagnose_settled
from meridian.scheduler.run import ScheduleRequest, run_schedule
from meridian.scheduler.schedule_config import ScheduleConfig
from meridian.store.invites import hash_invite_token
from meridian.store.schema_revision import find_current_revision
from meridian_client import transport as transport_module
from meridian_sim import supervisor as supervisor_module
from meridian_sim.config import RunConfig, seed_for_pass
from meridian_sim.ledger import FaultLedger
from meridian_sim.outcomes import decide_outcome
from meridian_sim.supervisor import Supervisor

REPO = Path(__file__).resolve().parents[2]
CATALOGUE = REPO / "deploy" / "catalogue" / "development.json"
PEPPER = "diagnosis-run-pepper"
IDLE = timedelta(minutes=20)
BUSY = timedelta(seconds=30)
AROUND = timedelta(minutes=26)
"""How far either side of each assignment the fleet ticks densely: past the
longest clock step, so a station whose clock is wrong still ticks through the
stretch it records (D-277)."""


@dataclass(frozen=True, slots=True)
class Plan:
    """One fleet: what breaks, from which seed, how many, and for how long."""

    scenario: str
    master_seed: int
    stations: int
    start: datetime
    hours: float
    silent_satellite: str | None = None

    @property
    def run_id(self) -> str:
        return f"diagnosis-{self.scenario}-{self.master_seed}"

    @property
    def end(self) -> datetime:
        return self.start + timedelta(hours=self.hours)

    def record(self) -> dict[str, object]:
        return {
            "scenario": self.scenario,
            "master_seed": self.master_seed,
            "stations": self.stations,
            "start": self.start.isoformat(),
            "hours": self.hours,
            "silent_satellite": self.silent_satellite,
            "simulated": True,
        }


@contextmanager
def _in_process(conn: Any, clock: list[datetime]) -> Iterator[TestClient]:
    """The platform over ``conn``, on a stated clock, nothing waiting for real."""
    saved = (
        platform_clock.utc_now,
        supervisor_module._sleep,
        transport_module._sleep,
    )
    platform_clock.utc_now = lambda: clock[0]
    supervisor_module._sleep = lambda _seconds: None
    transport_module._sleep = lambda _seconds: None
    environment = {
        name: os.environ.get(name) for name in ("TOKEN_HASH_PEPPER", "RATE_LIMITS")
    }
    os.environ["TOKEN_HASH_PEPPER"] = PEPPER
    os.environ["RATE_LIMITS"] = "off"
    app = create_app()
    app.dependency_overrides[get_connection] = lambda: conn
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        for name, value in environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        (
            platform_clock.utc_now,
            supervisor_module._sleep,
            transport_module._sleep,
        ) = saved


def fly(conn: Any, plan: Plan, *, state_dir: Path) -> tuple[str, tuple[str, ...]]:
    """Run the fleet over real passes, scheduled once; its ledger and its stations."""
    ledger = state_dir / "faults.jsonl"
    tokens = [f"{plan.run_id}-invite-{index}" for index in range(1, plan.stations + 1)]
    for token in tokens:
        conn.execute(
            "insert into invite_tokens (token_sha256, label) values (%s, %s)",
            (hash_invite_token(token), "diagnosis-run"),
        )
    load_document(conn, read_catalogue(CATALOGUE))
    orbit = SkyfieldOrbitService()
    clock = [plan.start - timedelta(minutes=1)]
    config = RunConfig(
        master_seed=plan.master_seed,
        run_id=plan.run_id,
        station_count=plan.stations,
        base_url="http://platform.test",
        state_dir=state_dir / "state",
        scenario=plan.scenario,
        silent_satellite=plan.silent_satellite,
    )
    with (
        _in_process(conn, clock) as client,
        Supervisor(
            config,
            tokens,
            client._transport,
            ledger=FaultLedger(ledger, plan.run_id),
        ) as fleet,
    ):
        station_ids = fleet.bring_up()
        generate_passes(conn, orbit, GenerationHorizon(start=plan.start, end=plan.end))
        run_schedule(
            conn,
            orbit,
            ScheduleRequest(
                start=plan.start, end=plan.end, now=clock[0], config=ScheduleConfig()
            ),
        )
        for tick, instant in enumerate(timeline(conn, plan, station_ids)):
            clock[0] = instant
            fleet.tick_round(tick, instant)
    return ledger.read_text(encoding="utf-8"), tuple(station_ids)


def timeline(conn: Any, plan: Plan, station_ids: tuple[str, ...]) -> list[datetime]:
    """Every instant the fleet ticks at: sparse, and dense around each assignment."""
    instants = {plan.start + k * IDLE for k in range(int(plan.hours * 3) + 1)}
    rows = conn.execute(
        "select start_at, end_at from assignments where station_id = any(%s)",
        (list(station_ids),),
    ).fetchall()
    for start_at, end_at in rows:
        at = start_at - AROUND
        while at <= end_at + AROUND:
            instants.add(at)
            at += BUSY
    return sorted(instants)


def settle(conn: Any, plan: Plan, config: ReliabilityConfig) -> None:
    """Classify every pass once settled, then diagnose every loss, as the jobs do."""
    now = plan.end + timedelta(seconds=config.classification.settle_margin_s + 3600)
    registry = PsycopgRegistry(conn, pepper=PEPPER, recovery_window_s=3600, now_utc=now)
    classify_settled(conn, registry, now=now, config=config.classification)
    diagnose_settled(
        conn, registry, SkyfieldOrbitService(), config=config, verdict_method=None
    )


def cases(conn: Any, station_ids: tuple[str, ...]) -> list[dict[str, object]]:
    """Every scheduled assignment of the fleet, beside its clean outcome."""
    rows = conn.execute(
        "select a.assignment_id, a.station_id, s.seed, a.state, a.revoked_reason,"
        " p.max_elevation_deg, o.outcome, a.simulated"
        " from assignments a join passes p on p.id = a.pass_id"
        " join stations s on s.station_id = a.station_id"
        " left join observations_current o on o.assignment_id = a.assignment_id"
        " where a.station_id = any(%s) and a.decision = 'scheduled'"
        " order by a.start_at, a.assignment_id",
        (list(station_ids),),
    ).fetchall()
    index = {station: number for number, station in enumerate(station_ids, start=1)}
    return [
        {
            "assignment_id": aid,
            "station_index": index[station],
            "state": state,
            "revoked_reason": revoked,
            "max_elevation_deg": elevation,
            "outcome": outcome,
            "clean_outcome": decide_outcome(
                seed_for_pass(int(seed), aid), float(elevation)
            ).outcome,
            "simulated": simulated,
        }
        for aid, station, seed, state, revoked, elevation, outcome, simulated in rows
    ]


def diagnoses(conn: Any, station_ids: tuple[str, ...]) -> list[dict[str, object]]:
    """Every diagnosis the platform wrote for the fleet, without when."""
    rows = conn.execute(
        "select assignment_id, revision, cause, evidence_json ->> 'reason',"
        " evidence_json ->> 'loss', candidates_json, method,"
        " encode(config_sha256, 'hex'), simulated"
        " from loss_diagnoses where station_id = any(%s)"
        " order by assignment_id, diagnosis_id",
        (list(station_ids),),
    ).fetchall()
    keys = ("assignment_id", "revision", "cause", "reason", "loss", "candidates")
    return [
        dict(zip((*keys, "method", "config_sha256", "simulated"), row, strict=True))
        for row in rows
    ]


def seal(
    conn: Any,
    plan: Plan,
    ledger: str,
    station_ids: tuple[str, ...],
    config: ReliabilityConfig,
    root: Path,
) -> Path:
    """Write the run, its ledger, its cases and its diagnoses as one sealed run."""
    revision = find_current_revision(conn) or "unknown"
    run = {
        **plan.record(),
        "classification_method": classification.METHOD,
        "classification_sha256": config.classification.sha256().hex(),
        "diagnosis_method": METHOD,
        "diagnosis_sha256": config.diagnosis.sha256().hex(),
        "diagnosis_parameters": config.diagnosis.parameters(),
    }
    published = publish_diagnosis_run(
        ledger,
        run,
        cases(conn, station_ids),
        diagnoses(conn, station_ids),
        root=root,
        stamp=(revision, plan.start, plan.end),
    )
    return published.path


def station_id_for(seed: int) -> Iterator[str]:
    """Station ids drawn from the seed, in registration order (D-278)."""
    number = 0
    while True:
        number += 1
        yield "st_" + hashlib.sha256(f"{seed}:{number}".encode()).hexdigest()[:6]


@contextmanager
def scratch(server_url: str, seed: int) -> Iterator[str]:
    """A new, migrated database on the server, dropped afterwards."""
    name = f"meridian_diagnosis_{seed}_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(server_url, autocommit=True) as admin:
        admin.execute(f'create database "{name}"')
    url = f"{server_url.rpartition('/')[0]}/{name}"
    before = os.environ.get("DATABASE_URL")
    try:
        # Built in code, not from alembic.ini: env.py applies a file's logging
        # configuration, which disables every logger that already exists.
        alembic = Config()
        alembic.set_main_option("script_location", str(REPO / "deploy" / "migrations"))
        alembic.set_main_option("sqlalchemy.url", url)
        os.environ["DATABASE_URL"] = url  # what env.py reads
        command.upgrade(alembic, "head")
        yield url
    finally:
        if before is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = before
        with psycopg.connect(server_url, autocommit=True) as admin:
            admin.execute(
                "select pg_terminate_backend(pid) from pg_stat_activity"
                " where datname = %s",
                (name,),
            )
            admin.execute(f'drop database if exists "{name}"')


def run_one(server_url: str, plan: Plan, config: ReliabilityConfig, root: Path) -> Path:
    """One fleet in a database of its own, sealed under ``root``."""
    ids = station_id_for(plan.master_seed)
    original = psycopg_registry.generate_station_id
    psycopg_registry.generate_station_id = lambda: next(ids)
    # A fresh directory every time: a station's stored credentials or an old
    # ledger from an earlier run of the same seed would be another run's.
    work = Path(tempfile.mkdtemp(prefix=f"{plan.run_id}-"))
    try:
        with scratch(server_url, plan.master_seed) as url, psycopg.connect(url) as conn:
            os.environ["DATABASE_URL"] = url
            ledger, station_ids = fly(conn, plan, state_dir=work)
            settle(conn, plan, config)
            return seal(conn, plan, ledger, station_ids, config, root)
    finally:
        psycopg_registry.generate_station_id = original
        shutil.rmtree(work, ignore_errors=True)


def plans(text: str) -> list[Plan]:
    """Every fleet the configuration asks for, scenario by seed."""
    stored = tomllib.loads(text)
    start = datetime.fromisoformat(stored["start"]).astimezone(UTC)
    return [
        Plan(
            scenario=scenario,
            master_seed=int(seed),
            stations=int(stored["stations"]),
            start=start,
            hours=float(stored["hours"]),
            silent_satellite=stored.get("silent_satellite"),
        )
        for scenario in stored["scenarios"]
        for seed in stored["seeds"]
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--database-url", required=True, help="a server to make runs on"
    )
    parser.add_argument("--root", type=Path, required=True, help="the datasets root")
    args = parser.parse_args(argv)
    config = ReliabilityConfig()
    for plan in plans(args.config.read_text(encoding="utf-8")):
        print(f"{plan.run_id}: {plan.stations} stations, {plan.hours} h", flush=True)
        sealed = run_one(args.database_url, plan, config, args.root)
        print(f"  sealed {sealed}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
