"""Stage 25's gate: four faults, their evidence stored, their causes never.

*A simulated fleet running each of the four new faults produces observations
carrying the evidence that fault changes, and a run record naming every
injected cause, while nothing the platform holds reveals the cause.*

A small fleet runs the ``sky`` scenario — degradation, obstruction,
interference and a silent satellite at once — against the real application and
database, over real passes of the development catalogue at each station's own
site. Then the gate reads the run from both sides:

* **the run record** names every cause, with its parameters, on the stations it
  reached, and the passes each one acted on;
* **the stored observations** of those passes carry what the specification in
  ``docs/SCALE-AND-FAULTS.md`` § Ground-truth faults says each fault changes,
  measured against the evidence the same pass would have had with nothing wrong,
  recomputed from its seed;
* **nothing the platform holds** — no text or JSON value in any table — names a
  cause or one of its parameters, with a planted label as the positive control;
* **every row the run derived** is labelled simulated.

**On a stated clock, scheduled once.** Passes over three sites are hours apart,
so the clock steps every thirty seconds around each assignment and every twenty
minutes between them. One scheduling round decides the whole horizon first, so
no round ever reads a station between ticks as offline. Fault onsets are ticks,
so the sparse stretches also spread the faults through the run.

Reference: docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md Stage 25's completion gate;
docs/DECISIONS.md D-105, D-189, D-195, D-251, D-253.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
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
from meridian.scheduler.schedule_config import ScheduleConfig
from meridian.store.invites import hash_invite_token
from meridian_client import transport as transport_module
from meridian_sim import supervisor as supervisor_module
from meridian_sim.config import RunConfig, seed_for_pass
from meridian_sim.evidence import (
    DETECT_SNR_DB,
    evidence_for,
    station_noise_floor_dbfs,
)
from meridian_sim.faults import (
    INTERFERENCE,
    OBSTRUCTION,
    SATELLITE_SILENT,
    SIGNAL_DEGRADATION,
    SKY_FAULTS,
)
from meridian_sim.ledger import FaultLedger, FaultRecord, read_ledger
from meridian_sim.outcomes import decide_outcome
from meridian_sim.supervisor import Supervisor

MASTER_SEED = 4471
STATIONS = 3
SCENARIO = "sky"
SILENT = "norad:57166"
"""The satellite this run silences: the one a station is receiving while the
seed's silence holds, ticks 42 to 65. Named by the gate as an operator would
name it. A change that moves the silence or the schedule fails the first clause,
that every cause acted, rather than passing with a silence that touched
nothing."""
CATALOGUE = Path("deploy/catalogue/development.json")
T0 = datetime(2026, 8, 12, tzinfo=UTC)
HORIZON = timedelta(hours=24)
IDLE_STEP = timedelta(minutes=20)
BUSY_STEP = timedelta(seconds=30)

LABELS = (
    *SKY_FAULTS,
    "rate_db_per_day",
    "azimuth_from_deg",
    "below_elevation_deg",
    "start_hour_utc",
    "rise_db",
    "fleet_wide",
)
"""Every name a cause or its parameters is written under in the run record."""

TEXT_TYPES = ("text", "character varying", "jsonb", "json", "ARRAY")


class Clock:
    """The one instant the platform and the fleet agree it is."""

    def __init__(self) -> None:
        self.now = T0


@dataclass(frozen=True, slots=True)
class Stored:
    """One stored observation, and what it would have been with nothing wrong."""

    assignment_id: str
    outcome: str
    noise_floor_dbfs: float | None
    peak_snr_db: float | None
    snr_db: tuple[float, ...]
    clean_outcome: str
    clean_floor: float | None
    clean_snr: tuple[float, ...]


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supervisor_module, "_sleep", lambda _seconds: None)
    monkeypatch.setattr(transport_module, "_sleep", lambda _seconds: None)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    stated = Clock()
    monkeypatch.setattr(platform_clock, "utc_now", lambda: stated.now)
    return stated


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def platform(
    database_url: str, rollback: Any, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("TOKEN_HASH_PEPPER", "ground-truth-pepper")
    monkeypatch.setenv("RATE_LIMITS", "off")
    app = create_app()
    app.dependency_overrides[get_connection] = lambda: rollback
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def invites(rollback: Any) -> list[str]:
    tokens = [f"truth-invite-{index}" for index in range(1, STATIONS + 1)]
    with rollback.cursor() as cur:
        for token in tokens:
            cur.execute(
                "insert into invite_tokens (token_sha256, label) values (%s, %s)",
                (hash_invite_token(token), "stage-25-gate"),
            )
    return tokens


def timeline(rollback: Any, station_ids: tuple[str, ...]) -> list[datetime]:
    """Every instant the fleet ticks at: sparse, and dense around each assignment."""
    instants = {T0 + k * IDLE_STEP for k in range(int(HORIZON / IDLE_STEP) + 1)}
    with rollback.cursor() as cur:
        cur.execute(
            "select start_at, end_at from assignments where station_id = any(%s)",
            (list(station_ids),),
        )
        for start_at, end_at in cur.fetchall():
            at = start_at - BUSY_STEP
            while at <= end_at + 3 * BUSY_STEP:
                instants.add(at)
                at += BUSY_STEP
    return sorted(instants)


@pytest.fixture
def run(
    platform: TestClient, rollback: Any, clock: Clock, tmp_path: Path
) -> tuple[Path, tuple[str, ...]]:
    """The faulted fleet, over real passes, scheduled once; its ledger and ids."""
    config = RunConfig(
        master_seed=MASTER_SEED,
        run_id="ground-truth-gate",
        station_count=STATIONS,
        base_url="http://platform.test",
        state_dir=tmp_path / "state",
        scenario=SCENARIO,
        silent_satellite=SILENT,
    )
    ledger = tmp_path / "faults.jsonl"
    orbit = SkyfieldOrbitService()
    load_document(rollback, read_catalogue(CATALOGUE))

    with Supervisor(
        config,
        invites(rollback),
        platform._transport,
        ledger=FaultLedger(ledger, config.run_id),
    ) as fleet:
        clock.now = T0 - timedelta(minutes=1)
        station_ids = fleet.bring_up()
        generate_passes(rollback, orbit, GenerationHorizon(start=T0, end=T0 + HORIZON))
        run_schedule(
            rollback,
            orbit,
            ScheduleRequest(
                start=T0, end=T0 + HORIZON, now=clock.now, config=ScheduleConfig()
            ),
        )
        for tick, instant in enumerate(timeline(rollback, station_ids)):
            clock.now = instant
            fleet.tick_round(tick, instant)
    return ledger, station_ids


def acted_on(records: tuple[FaultRecord, ...]) -> dict[str, set[str]]:
    """Each cause, and every assignment the run record says it acted on."""
    acts: dict[str, set[str]] = defaultdict(set)
    for one in records:
        acts[one.kind].update(one.assignment_ids)
    return acts


def stored(rollback: Any, records: tuple[FaultRecord, ...]) -> dict[str, Stored]:
    """Every current observation, beside the clean evidence of the same pass."""
    seeds = {one.station_id: one.seed for one in records if one.seed is not None}
    with rollback.cursor() as cur:
        cur.execute(
            """
            select o.assignment_id, o.station_id, o.outcome, o.noise_floor_dbfs,
                   o.peak_snr_db,
                   coalesce((select array_agg((s ->> 'snr_db')::float8 order by n)
                             from jsonb_array_elements(o.snr_samples)
                                  with ordinality as e(s, n)), '{}'),
                   p.max_elevation_deg, a.start_at, a.end_at
            from observations_current o
            join assignments a using (assignment_id)
            join passes p on p.id = a.pass_id
            where o.station_id = any(%s)
            """,
            (list(seeds),),
        )
        rows = cur.fetchall()
    found: dict[str, Stored] = {}
    for row in rows:
        assignment_id, station_id, outcome, floor, peak, snr, elevation = row[:7]
        station_seed = seeds[station_id]
        pass_seed = seed_for_pass(station_seed, assignment_id)
        clean_outcome = decide_outcome(pass_seed, float(elevation))
        clean = evidence_for(
            pass_seed,
            clean_outcome,
            (row[8] - row[7]).total_seconds(),
            station_noise_floor_dbfs(station_seed),
        )
        found[assignment_id] = Stored(
            assignment_id=assignment_id,
            outcome=outcome,
            noise_floor_dbfs=floor,
            peak_snr_db=peak,
            snr_db=tuple(snr),
            clean_outcome=clean_outcome.outcome,
            clean_floor=None if clean is None else clean.noise_floor_dbfs,
            clean_snr=() if clean is None else clean.snr_db,
        )
    return found


def labels_found(rollback: Any) -> list[str]:
    """Every table column whose stored value names a cause or a parameter."""
    with rollback.cursor() as cur:
        cur.execute(
            """
            select table_name, column_name from information_schema.columns
            where table_schema = 'public' and data_type = any(%s)
              and table_name in (select table_name from information_schema.tables
                                 where table_schema = 'public'
                                   and table_type = 'BASE TABLE')
            order by table_name, column_name
            """,
            (list(TEXT_TYPES),),
        )
        columns = cur.fetchall()
        patterns = [f"%{label}%" for label in LABELS]
        found = []
        for table, column in columns:
            cur.execute(
                f'select count(*) from "{table}" where "{column}"::text ilike any(%s)',
                (patterns,),
            )
            if cur.fetchone()[0]:
                found.append(f"{table}.{column}")
    return found


def check_the_run_record(
    records: tuple[FaultRecord, ...], station_ids: tuple[str, ...]
) -> dict[str, set[str]]:
    """Every cause named, with its parameters, where it reached; and what it did."""
    opened: dict[str, set[str | None]] = defaultdict(set)
    for one in records:
        opened[one.kind].add(one.station_id)
        assert one.detail, f"{one.kind} opened with no parameters"
    assert set(opened) == set(SKY_FAULTS)
    assert opened[SATELLITE_SILENT] == set(station_ids)
    acts = acted_on(records)
    assert all(acts[kind] for kind in SKY_FAULTS), {k: len(v) for k, v in acts.items()}
    return acts


def check_the_evidence(
    observations: dict[str, Stored], acts: dict[str, set[str]]
) -> None:
    """Each acted-on pass changed as the specification says; the rest did not.

    A pass two faults acted on shows both, so a clause that one fault leaves
    alone — the floor, under a degradation — is checked only where the other
    did not act.
    """

    def reported(kind: str) -> list[Stored]:
        found = [observations[one] for one in acts[kind] if one in observations]
        assert found, f"no pass {kind} acted on was reported"
        return found

    for one in reported(SIGNAL_DEGRADATION):
        assert max(one.snr_db) < max(one.clean_snr)
        if one.assignment_id not in acts[INTERFERENCE]:
            assert one.noise_floor_dbfs == one.clean_floor
    for one in reported(OBSTRUCTION):
        assert any(a < b for a, b in zip(one.snr_db, one.clean_snr, strict=True))
    for one in reported(INTERFERENCE):
        assert one.noise_floor_dbfs is not None and one.clean_floor is not None
        assert one.noise_floor_dbfs > one.clean_floor
    for one in reported(SATELLITE_SILENT):
        assert one.outcome == "no_signal"
        assert max(one.snr_db) < DETECT_SNR_DB

    untouched = set(observations) - set().union(*acts.values())
    assert untouched, "the gate needs passes no fault touched, to compare against"
    for one in (observations[a] for a in untouched):
        assert (one.outcome, one.snr_db) == (one.clean_outcome, one.clean_snr)


def check_every_row_is_simulated(rollback: Any, station_ids: tuple[str, ...]) -> None:
    """CLAUDE.md rule 5, on every table the run wrote a station's rows into."""
    with rollback.cursor() as cur:
        for table in ("observations", "noise_measurements", "assignments", "passes"):
            cur.execute(
                "select count(*), count(*) filter (where not simulated)"
                f" from {table} where station_id = any(%s)",
                (list(station_ids),),
            )
            total, measured = cur.fetchone()
            assert total > 0, f"the run wrote no {table}"
            assert measured == 0, f"{measured} {table} rows not labelled simulated"


def test_the_gate(run: tuple[Path, tuple[str, ...]], rollback: Any) -> None:
    """Each clause of Stage 25's gate, read from the run and from the platform."""
    ledger, station_ids = run
    records = read_ledger(ledger)

    acts = check_the_run_record(records, station_ids)
    observations = stored(rollback, records)
    check_the_evidence(observations, acts)
    assert labels_found(rollback) == []
    check_every_row_is_simulated(rollback, station_ids)

    # Positive control: a label that did reach the platform is found.
    with rollback.cursor() as cur:
        cur.execute(
            "update observations set client_notes = client_notes || ' obstruction'"
            " where assignment_id = %s",
            (next(iter(observations)),),
        )
    assert labels_found(rollback) == ["observations.client_notes"]
