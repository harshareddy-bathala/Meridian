"""``deploy/tools/watch_runs.py`` — simulated fleets for SC-9, watched and sealed.

SC-9 asks whether the receive-chain watch warns before a degradation fails a
reception, and how often it warns when nothing is wrong. The degradation's
onset and rate are in the simulator's ledger, which never enters the platform
(D-105). So, as for SC-8, the figure is made in two halves (D-296):
- this tool flies a fleet against the real platform, in process, and lets the
  platform watch every chain from its own records exactly as the jobs service
  does, every half hour;
- ``meridian report build --watches`` judges the sealed runs.

**Two stretches, one fleet.** A chain needs a clean history before a
degradation is anything to detect (``EVALUATION.md`` §11.3). The fleet first
flies ``clean_days`` with the fault schedule held at its first tick, before any
onset, so the watch trains each chain's baseline on clean signal. Then the
ticks run on, and a ``degradation`` fleet's faults open within the first hour.
A ``clean`` fleet's never do: its warnings are SC-9's false alarms.

**Unlike every other tool here it needs the workspace**, ``uv sync`` and
``uv run``, for the reason ``diagnosis_runs.py`` does, whose harness it shares.

    uv run python deploy/tools/watch_runs.py \\
        --config analysis/configs/watch.toml.example \\
        --database-url postgresql://meridian:…@localhost:5432/meridian \\
        --root "$MERIDIAN_DATASETS_ROOT" [--run watch-degradation-4471 …]

**It takes hours.** A trial fleet of four stations flew six simulated days in
56 minutes, so each of the example configuration's six fleets, six stations
over eight days, takes about two hours, and all six about twelve. ``--run``
picks fleets, so they can be run a few a night; each is sealed on its own, and
the report takes every one given.

Reference: docs/DECISIONS.md D-105, D-138, D-253, D-278, D-294, D-296.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
from diagnosis_runs import (
    AROUND,
    BUSY,
    CATALOGUE,
    IDLE,
    in_process,
    scratch,
    station_id_for,
)

from meridian.catalogue_file import read_catalogue
from meridian.cli_catalogue import load_document
from meridian.datasets.watch_runs import publish_watch_run
from meridian.orbit.skyfield_service import SkyfieldOrbitService
from meridian.pass_generation import GenerationHorizon, generate_passes
from meridian.registry import psycopg_registry
from meridian.reliability import watch_baseline, watch_detect
from meridian.reliability.config import ReliabilityConfig
from meridian.reliability.watch_build import baseline_sha256
from meridian.reliability.watch_run import watch_chains, watch_sha256
from meridian.scheduler.run import ScheduleRequest, run_schedule
from meridian.scheduler.schedule_config import ScheduleConfig
from meridian.store.invites import hash_invite_token
from meridian.store.schema_revision import find_current_revision
from meridian_sim.config import RunConfig, seed_for_pass
from meridian_sim.ledger import FaultLedger
from meridian_sim.outcomes import decide_outcome
from meridian_sim.supervisor import Supervisor

WATCH_EVERY = timedelta(minutes=30)
"""How often the fleet's chains are watched: the jobs service's round is five
minutes, so a warning here is up to twenty-five minutes later than it would be
live, never earlier."""

SCENARIOS = ("degradation", "clean")


@dataclass(frozen=True, slots=True)
class Plan:
    """One fleet: degraded or clean, from which seed, how many, for how long."""

    scenario: str
    master_seed: int
    stations: int
    start: datetime
    clean_days: float
    watched_days: float

    @property
    def run_id(self) -> str:
        return f"watch-{self.scenario}-{self.master_seed}"

    @property
    def watched_from(self) -> datetime:
        """When the fault schedule starts to run."""
        return self.start + timedelta(days=self.clean_days)

    @property
    def end(self) -> datetime:
        return self.watched_from + timedelta(days=self.watched_days)

    def record(self) -> dict[str, object]:
        return {
            "scenario": self.scenario,
            "master_seed": self.master_seed,
            "stations": self.stations,
            "start": self.start.isoformat(),
            "watched_from": self.watched_from.isoformat(),
            "end": self.end.isoformat(),
            "watch_every_s": int(WATCH_EVERY.total_seconds()),
            "simulated": True,
        }


def fly(
    conn: Any,
    plan: Plan,
    config: ReliabilityConfig,
    *,
    state_dir: Path,
    progress: Callable[[str], None] = lambda _line: None,
) -> tuple[str, tuple[str, ...]]:
    """Fly the fleet over real passes, watched as the jobs service watches.

    Returns:
        The ledger the simulator wrote, and the station ids in index order.
    """
    ledger = state_dir / "faults.jsonl"
    tokens = [f"{plan.run_id}-invite-{n}" for n in range(1, plan.stations + 1)]
    for token in tokens:
        conn.execute(
            "insert into invite_tokens (token_sha256, label) values (%s, %s)",
            (hash_invite_token(token), "watch-run"),
        )
    load_document(conn, read_catalogue(CATALOGUE))
    orbit = SkyfieldOrbitService()
    clock = [plan.start - timedelta(minutes=1)]
    run = RunConfig(
        master_seed=plan.master_seed,
        run_id=plan.run_id,
        station_count=plan.stations,
        base_url="http://platform.test",
        state_dir=state_dir / "state",
        scenario=plan.scenario,
    )
    with (
        in_process(conn, clock) as client,
        Supervisor(
            run, tokens, client._transport, ledger=FaultLedger(ledger, plan.run_id)
        ) as fleet,
    ):
        station_ids = fleet.bring_up()
        generate_passes(conn, orbit, GenerationHorizon(start=plan.start, end=plan.end))
        conn.commit()
        day = plan.start
        while day < plan.end:
            run_schedule(
                conn,
                orbit,
                ScheduleRequest(
                    start=day,
                    end=min(day + timedelta(days=1), plan.end),
                    now=clock[0],
                    config=ScheduleConfig(),
                ),
            )
            day += timedelta(days=1)
        next_watch = plan.start + WATCH_EVERY
        next_day = plan.start
        tick = 0
        for instant in timeline(conn, plan, station_ids):
            clock[0] = instant
            # Held at the first tick through the clean stretch: every onset is
            # a later tick, so nothing opens until the schedule runs on.
            if instant >= plan.watched_from:
                tick += 1
            fleet.tick_round(tick, instant)
            if instant >= next_watch:
                report = watch_chains(conn, orbit, config=config, now=instant)
                for line in report.raised:
                    progress(f"  {instant:%Y-%m-%d %H:%M} raised {line}")
                next_watch = instant + WATCH_EVERY
                if instant >= next_day:
                    progress(f"  {instant:%Y-%m-%d}: watched, {report.open} open")
                    next_day += timedelta(days=1)
                # A round's work is kept, as the jobs service's is. One
                # transaction over days of heartbeats would hold every old
                # version of every station's row, and slow each update more
                # than the last.
                conn.commit()
    return ledger.read_text(encoding="utf-8"), tuple(station_ids)


def timeline(conn: Any, plan: Plan, station_ids: tuple[str, ...]) -> list[datetime]:
    """Every instant the fleet ticks at: sparse, and dense around each assignment."""
    span = int((plan.end - plan.start) / IDLE)
    instants = {plan.start + k * IDLE for k in range(span + 1)}
    rows = conn.execute(
        "select start_at, end_at from assignments where station_id = any(%s)"
        " and decision = 'scheduled'",
        (list(station_ids),),
    ).fetchall()
    for start_at, end_at in rows:
        at = start_at - AROUND
        while at <= end_at + AROUND:
            instants.add(at)
            at += BUSY
    return sorted(instants)


def cases(conn: Any, station_ids: tuple[str, ...]) -> list[dict[str, object]]:
    """Every scheduled assignment of the fleet, beside its clean outcome."""
    rows = conn.execute(
        "select a.assignment_id, a.station_id, s.seed, a.start_at, a.end_at,"
        " p.max_elevation_deg, o.outcome, o.peak_snr_db, a.simulated"
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
            "start_at": start_at.isoformat(),
            "end_at": end_at.isoformat(),
            "max_elevation_deg": elevation,
            "outcome": outcome,
            "peak_snr_db": peak,
            "clean_outcome": decide_outcome(
                seed_for_pass(int(seed), aid), float(elevation)
            ).outcome,
            "simulated": simulated,
        }
        for (
            aid,
            station,
            seed,
            start_at,
            end_at,
            elevation,
            outcome,
            peak,
            simulated,
        ) in rows
    ]


def warnings(conn: Any, station_ids: tuple[str, ...]) -> list[dict[str, object]]:
    """Every warning the platform raised for the fleet, with its station's index."""
    rows = conn.execute(
        "select station_id, raised_at, shortfall_db, affected_bins_json,"
        " cleared_at, cleared_shortfall_db, method, encode(config_sha256, 'hex'),"
        " simulated from receive_chain_warnings where station_id = any(%s)"
        " order by raised_at, id",
        (list(station_ids),),
    ).fetchall()
    index = {station: number for number, station in enumerate(station_ids, start=1)}
    return [
        {
            "station_index": index[station],
            "raised_at": raised.isoformat(),
            "shortfall_db": shortfall,
            "affected_bins": bins,
            "cleared_at": None if cleared is None else cleared.isoformat(),
            "cleared_shortfall_db": cleared_db,
            "method": method,
            "config_sha256": sha,
            "simulated": simulated,
        }
        for (
            station,
            raised,
            shortfall,
            bins,
            cleared,
            cleared_db,
            method,
            sha,
            simulated,
        ) in rows
    ]


def seal(
    conn: Any,
    plan: Plan,
    config: ReliabilityConfig,
    flown: tuple[str, tuple[str, ...]],
    root: Path,
) -> Path:
    """Write the run, its ledger, its cases and its warnings as one sealed run."""
    ledger, station_ids = flown
    run = {
        **plan.record(),
        # Drawn from the seed (D-278): how the ledger's stations are matched
        # to the cases' indexes, and nothing about a person.
        "station_ids": list(station_ids),
        "baseline_method": watch_baseline.METHOD,
        "baseline_sha256": baseline_sha256(config).hex(),
        "watch_method": watch_detect.METHOD,
        "watch_sha256": watch_sha256(config).hex(),
        "watch_parameters": config.watch.parameters(),
        "baseline_parameters": config.baseline.parameters(),
    }
    revision = find_current_revision(conn) or "unknown"
    return publish_watch_run(
        ledger,
        run,
        cases(conn, station_ids),
        warnings(conn, station_ids),
        root=root,
        stamp=(revision, plan.start, plan.end),
    ).path


def run_one(server_url: str, plan: Plan, config: ReliabilityConfig, root: Path) -> Path:
    """One fleet in a database of its own, sealed under ``root``."""
    ids = station_id_for(plan.master_seed)
    original = psycopg_registry.generate_station_id
    psycopg_registry.generate_station_id = lambda: next(ids)
    work = Path(tempfile.mkdtemp(prefix=f"{plan.run_id}-"))
    try:
        with scratch(server_url, plan.master_seed) as url, psycopg.connect(url) as conn:
            flown = fly(
                conn,
                plan,
                config,
                state_dir=work,
                progress=lambda line: print(line, flush=True),
            )
            return seal(conn, plan, config, flown, root)
    finally:
        psycopg_registry.generate_station_id = original
        shutil.rmtree(work, ignore_errors=True)


def plans(text: str) -> list[Plan]:
    """Every fleet the configuration asks for, scenario by seed."""
    stored = tomllib.loads(text)
    start = datetime.fromisoformat(stored["start"]).astimezone(UTC)
    unknown = sorted(set(stored["scenarios"]) - set(SCENARIOS))
    if unknown:
        raise ValueError(f"unknown scenarios {unknown}; SC-9 runs {list(SCENARIOS)}")
    return [
        Plan(
            scenario=scenario,
            master_seed=int(seed),
            stations=int(stored["stations"]),
            start=start,
            clean_days=float(stored["clean_days"]),
            watched_days=float(stored["watched_days"]),
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
    parser.add_argument(
        "--run",
        action="append",
        default=[],
        metavar="RUN_ID",
        help="only this fleet, as watch-<scenario>-<seed>; repeat for several."
        " The configuration's fleets take hours, so they are run in batches",
    )
    args = parser.parse_args(argv)
    config = ReliabilityConfig()
    chosen = plans(args.config.read_text(encoding="utf-8"))
    if args.run:
        unknown = sorted(set(args.run) - {plan.run_id for plan in chosen})
        if unknown:
            parser.error(f"no fleet {unknown} in {args.config}")
        chosen = [plan for plan in chosen if plan.run_id in args.run]
    for plan in chosen:
        print(
            f"{plan.run_id}: {plan.stations} stations, {plan.clean_days} clean days,"
            f" {plan.watched_days} watched",
            flush=True,
        )
        sealed = run_one(args.database_url, plan, config, args.root)
        print(f"  sealed {sealed}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
