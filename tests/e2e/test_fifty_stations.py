"""Stage 21's scale gate, in process: fifty stations through real MSP.

*Fifty deterministic stations operate through real MSP* — the first half of
Stage 21's completion gate. Fifty virtual stations register, heartbeat, are
scheduled from real pass generation over the development catalogue, and hold
the work they were given, through the real application and database.

And the property that makes fifty no harder to watch than five: **the platform's
metrics do not grow with the fleet** (D-197). Five stations and then fifty,
doing the same things, expose exactly the same series, and no series anywhere is
labelled with an identifier the fleet brought with it. A per-station label would
make Prometheus's cost scale with the network, and the dashboards with it.

Wall-clock latency and pool pressure under a real network are measured by
``deploy/tools/scale_probe.py`` against a running deployment, and recorded in
``docs/SCALE-AND-FAULTS.md``; an in-process client measures neither honestly.

Marked ``e2e`` by the directory hook in ``tests/conftest.py``.

Reference: docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md Stage 21; docs/DECISIONS.md
D-197, D-202.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from meridian.api.app import create_app
from meridian.api.dependencies import get_connection
from meridian.api.domain_collector import DomainCollector
from meridian.catalogue_file import read_catalogue
from meridian.cli_catalogue import load_document
from meridian.orbit.skyfield_service import SkyfieldOrbitService
from meridian.pass_generation import GenerationHorizon, generate_passes
from meridian.reliability.config import ReliabilityConfig
from meridian.scheduler.run import ScheduleRequest, run_schedule
from meridian.scheduler.schedule_config import ScheduleConfig
from meridian.store.invites import hash_invite_token
from meridian_client import transport as transport_module
from meridian_sim import supervisor as supervisor_module
from meridian_sim.config import RunConfig
from meridian_sim.supervisor import RoundOutcome, Supervisor

MASTER_SEED = 4471
STATIONS = 50
FIRST = 5
"""The fleet the series are counted at before it grows to :data:`STATIONS`."""

CATALOGUE = Path("deploy/catalogue/development.json")
HORIZON = timedelta(hours=6)


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rounds are ticked by hand; nothing should sleep."""
    monkeypatch.setattr(supervisor_module, "_sleep", lambda _seconds: None)
    monkeypatch.setattr(transport_module, "_sleep", lambda _seconds: None)


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything the fleet writes."""
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def started(
    database_url: str, rollback: Any, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """The application, sharing this test's rolled-back connection."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("TOKEN_HASH_PEPPER", "fifty-pepper")
    # Fifty stations' rounds arrive back to back in wall time here: D-202's
    # switch for accelerated simulations on loopback.
    monkeypatch.setenv("RATE_LIMITS", "off")
    app = create_app()
    app.dependency_overrides[get_connection] = lambda: rollback
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


class _OnePool:
    """A pool that lends the test's own connection, so the collector reads
    what this test wrote and has not committed."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def get_stats(self) -> dict[str, int]:
        return {"pool_size": 1, "pool_available": 1, "requests_waiting": 0}

    @contextmanager
    def connection(self, timeout: float | None = None) -> Iterator[Any]:  # noqa: ARG002
        yield self._conn


def domain_series(rollback: Any) -> set[tuple[str, tuple[tuple[str, str], ...]]]:
    """Every series the scrape-time collector would expose, without values."""
    collector = DomainCollector(
        lambda: _OnePool(rollback),  # type: ignore[arg-type,return-value]
        head_revision_of=lambda: None,
        reliability_of=ReliabilityConfig,
    )
    return {
        (sample.name, tuple(sorted(sample.labels.items())))
        for family in collector.collect()
        for sample in family.samples
    }


def run_config(tmp_path: Path, count: int) -> RunConfig:
    """A fleet of ``count`` over one state directory, so a larger fleet resumes
    the smaller one's stations rather than registering them again."""
    return RunConfig(
        master_seed=MASTER_SEED,
        run_id="fifty",
        station_count=count,
        base_url="http://platform.test",
        state_dir=tmp_path / "state",
    )


def invites(rollback: Any) -> list[str]:
    """One invite per station."""
    tokens = [f"fifty-invite-{index}" for index in range(1, STATIONS + 1)]
    with rollback.cursor() as cur:
        for token in tokens:
            cur.execute(
                "insert into invite_tokens (token_sha256, label) values (%s, %s)",
                (hash_invite_token(token), token),
            )
    return tokens


def rounds(fleet: Supervisor, count: int, start: int = 0) -> list[RoundOutcome]:
    """Tick the fleet ``count`` rounds."""
    return [
        fleet.tick_round(tick, datetime.now(UTC))
        for tick in range(start, start + count)
    ]


def schedule(rollback: Any) -> int:
    """Passes over the next hours, and a schedule over them. Assignments made."""
    orbit = SkyfieldOrbitService()
    now = datetime.now(UTC)
    generated = generate_passes(
        rollback, orbit, GenerationHorizon(start=now, end=now + HORIZON)
    )
    assert generated.passes_stored > 0
    report = run_schedule(
        rollback,
        orbit,
        ScheduleRequest(start=now, end=now + HORIZON, now=now, config=ScheduleConfig()),
    )
    return report.scheduled


def test_fifty_stations_operate_through_real_msp(
    started: TestClient, rollback: Any, tmp_path: Path
) -> None:
    """All fifty register, are heard every round, are scheduled, and hold work."""
    tokens = invites(rollback)
    load_document(rollback, read_catalogue(CATALOGUE))

    with Supervisor(
        run_config(tmp_path, STATIONS), tokens, started._transport
    ) as fleet:
        station_ids = fleet.bring_up()
        before = rounds(fleet, 2)
        scheduled = schedule(rollback)
        after = rounds(fleet, 3, start=2)

    everyone = tuple(range(1, STATIONS + 1))
    assert len(set(station_ids)) == STATIONS
    assert all(one.heard == everyone for one in before + after)
    assert all(one.stopped == () for one in before + after)
    assert scheduled > 0
    with rollback.cursor() as cur:
        cur.execute(
            "select count(distinct station_id) from heartbeats"
            " where station_id = any(%s)",
            (list(station_ids),),
        )
        (heartbeating,) = cur.fetchone()
        cur.execute(
            "select count(*) from assignments where state = 'held'"
            " and station_id = any(%s)",
            (list(station_ids),),
        )
        (held,) = cur.fetchone()
    assert heartbeating == STATIONS
    assert held > 0, "stations were scheduled and hold none of it"


def test_the_series_do_not_grow_with_the_fleet(
    started: TestClient, rollback: Any, tmp_path: Path
) -> None:
    """D-197: five stations and fifty expose the same series, and none names one."""
    tokens = invites(rollback)

    with Supervisor(run_config(tmp_path, FIRST), tokens, started._transport) as fleet:
        fleet.bring_up()
        rounds(fleet, 2)
    at_five = domain_series(rollback)

    with Supervisor(
        run_config(tmp_path, STATIONS), tokens, started._transport
    ) as fleet:
        station_ids = fleet.bring_up()
        rounds(fleet, 2)
    at_fifty = domain_series(rollback)

    online = ("meridian_stations", (("liveness", "online"), ("simulated", "true")))
    assert online in at_five, "the collector read no stations: nothing was compared"
    assert at_fifty == at_five
    identities = set(station_ids) | {f"sim-{index:03d}" for index in range(1, 51)}
    labelled = {
        value
        for metric in REGISTRY.collect()
        for sample in metric.samples
        for value in sample.labels.values()
    } | {value for _, labels in at_fifty for _, value in labels}
    assert not identities & labelled, "a station identity became a label"
