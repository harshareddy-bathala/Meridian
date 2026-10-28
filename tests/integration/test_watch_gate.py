"""Stage 28's gate, the integration half: a degrading station, watched and sealed.

*SC-9's detection delay, lead and false-alarm rate regenerate from seeded runs,
and on an injected degradation a warning reaches the dashboard and the
alerting before the degradation fails a reception.*

One virtual station of a pinned seed flies two clean days, so its chain has a
baseline, then a day and a half of a degradation the simulator's ledger alone
knows the onset and rate of. It flies against the real application in a
database of its own, through the harness SC-9's runs are made with
(``deploy/tools/watch_runs.py``), which watches the chain every half hour as
the jobs service does. The gate reads the sealed run from every side:

* **the warning comes before the degradation costs a decode**;
* **it reaches the dashboard**: the station's public receive-chain read shows
  it open, on a simulated station;
* **it reaches the alerting**: the scrape's gauge counts it open for the
  simulated population, which ``ReceiveChainDegrading``'s promtool test shows
  firing on;
* **nothing the platform holds names the fault** or its rate, with a planted
  label as the positive control (D-105);
* **every row is simulated**, and the sealed run reads back.

The report half, which builds SC-9 from sealed runs, is
``tests/unit/test_report_watch.py``. SC-9's figure is measured over the
configuration's fleets, by hand (``OPERATIONS.md`` § The receive-chain watch).

Reference: docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md Stage 28's completion gate;
docs/DECISIONS.md D-105, D-292 to D-296.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.api.domain_collector import DomainCollector  # noqa: E402
from meridian.datasets.watch_runs import read_watch_run  # noqa: E402
from meridian.registry import psycopg_registry  # noqa: E402
from meridian.reliability.config import ReliabilityConfig  # noqa: E402
from meridian.reports.watch import judge_degradations  # noqa: E402

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
CONFIG = ReliabilityConfig()
PLAN = {
    "scenario": "degradation",
    "master_seed": 4472,
    "stations": 1,
    "start": datetime(2026, 8, 12, tzinfo=UTC),
    "clean_days": 2.0,
    "watched_days": 1.5,
}
"""Pinned: a station whose chain loses 7.3 dB a day from the sixth tick of the
watched stretch. A change that moves its warning past its first lost decode
fails the check below, not silently."""

LEDGER_ONLY = ("signal_degradation", "rate_db_per_day")
"""Words only the simulator's ledger uses."""

TEXT_TYPES = ("text", "jsonb", "json", "character varying", "ARRAY")


@pytest.fixture(scope="module")
def harness() -> Iterator[ModuleType]:
    """The tool SC-9's runs are made with, imported as running it does."""
    added = [str(REPO / "deploy" / "migrations"), str(REPO / "deploy" / "tools")]
    sys.path[:0] = added
    spec = importlib.util.spec_from_file_location(
        "watch_runs", REPO / "deploy" / "tools" / "watch_runs.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["watch_runs"] = module
    spec.loader.exec_module(module)
    try:
        yield module
    finally:
        sys.modules.pop("watch_runs", None)
        sys.modules.pop("diagnosis_runs", None)
        for one in added:
            sys.path.remove(one)


@pytest.fixture(scope="module")
def flown(
    harness: ModuleType, database_url: str, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[tuple[Any, ModuleType, Path, tuple[str, ...]]]:
    """One station flown, watched and sealed; its connection still open to read."""
    plan = harness.Plan(**PLAN)
    root = tmp_path_factory.mktemp("datasets")
    ids = harness.station_id_for(plan.master_seed)
    original = psycopg_registry.generate_station_id
    psycopg_registry.generate_station_id = lambda: next(ids)
    try:
        with (
            harness.scratch(database_url, plan.master_seed) as scratch_url,
            psycopg.connect(scratch_url) as conn,
        ):
            flown = harness.fly(conn, plan, CONFIG, state_dir=root / "work")
            sealed = harness.seal(conn, plan, CONFIG, flown, root)
            yield conn, harness, sealed, flown[1]
    finally:
        psycopg_registry.generate_station_id = original


def test_the_warning_comes_before_the_degradation_costs_a_decode(
    flown: tuple[Any, ModuleType, Path, tuple[str, ...]],
) -> None:
    _, _, sealed, _ = flown

    (degradation,) = judge_degradations(read_watch_run(sealed))

    assert degradation.warned_at is not None, "the pinned seed was never warned"
    assert degradation.first == "warning"
    assert degradation.loss_db is not None
    assert degradation.loss_db >= CONFIG.watch.raise_db


def test_the_warning_reaches_the_dashboard(
    flown: tuple[Any, ModuleType, Path, tuple[str, ...]],
) -> None:
    conn, harness, _, (station,) = flown

    with harness.in_process(conn, [harness.Plan(**PLAN).end]) as client:
        body = client.get(f"/api/v1/stations/{station}/receive-chain").json()

    assert body["simulated"] is True
    (chain,) = body["chains"]
    assert chain["baseline"] is not None
    newest = chain["warnings"][0]
    assert newest["cleared_at"] is None


def test_the_warning_reaches_the_alerting(
    flown: tuple[Any, ModuleType, Path, tuple[str, ...]],
) -> None:
    conn, *_ = flown

    class Pool:
        def get_stats(self) -> dict[str, int]:
            return {"pool_size": 1, "pool_available": 1, "requests_waiting": 0}

        @contextmanager
        def connection(self, **_options: float) -> Iterator[Any]:
            yield conn

    collector = DomainCollector(
        Pool,  # type: ignore[arg-type]
        reliability_of=lambda: CONFIG,
    )
    families = {family.name: family for family in collector.collect()}

    samples = {
        sample.labels["simulated"]: sample.value
        for sample in families["meridian_receive_chain_warnings_open"].samples
    }
    assert samples == {"true": 1.0}


def test_nothing_the_platform_holds_names_the_fault(
    flown: tuple[Any, ModuleType, Path, tuple[str, ...]],
) -> None:
    conn, *_ = flown

    assert labels_found(conn) == []
    with conn.transaction(force_rollback=True):
        conn.execute(
            "update stations set name = 'signal_degradation' where station_id ="
            " (select min(station_id) from stations)"
        )
        assert labels_found(conn) == ["stations.name"]


def test_every_row_is_simulated_and_reads_back(
    flown: tuple[Any, ModuleType, Path, tuple[str, ...]],
) -> None:
    conn, _, sealed, _ = flown
    run = read_watch_run(sealed)

    for table in ("signal_baselines", "receive_chain_warnings"):
        assert conn.execute(f"select bool_and(simulated) from {table}").fetchone() == (
            True,
        )
    assert (
        len(run.warnings)
        == conn.execute("select count(*) from receive_chain_warnings").fetchone()[0]
    )
    assert all(one["simulated"] is True for one in (*run.cases, *run.warnings))
    assert run.run["simulated"] is True


def labels_found(conn: Any) -> list[str]:
    """Every column of every table whose stored value holds a ledger-only word."""
    columns = conn.execute(
        """
        select table_name, column_name from information_schema.columns
        where table_schema = 'public' and data_type = any(%s)
          and table_name in (select table_name from information_schema.tables
                             where table_schema = 'public'
                               and table_type = 'BASE TABLE')
        order by table_name, column_name
        """,
        (list(TEXT_TYPES),),
    ).fetchall()
    patterns = [f"%{word}%" for word in LEDGER_ONLY]
    return [
        f"{table}.{column}"
        for table, column in columns
        if conn.execute(
            f'select count(*) from "{table}" where "{column}"::text ilike any(%s)',
            (patterns,),
        ).fetchone()[0]
    ]
