"""Stage 27's gate, the integration half: a faulted fleet, diagnosed and sealed.

*SC-8's confusion matrix, per-cause recall, wrong-cause fraction and
undetermined fraction regenerate from the simulator's seeds, labelled as
simulated and reported apart from any real labelled cases.*

A small fleet runs the ``diagnosis`` scenario — a dead receiver, a degraded
decoder, a stepped clock, an obstruction, interference and a silent satellite —
against the real application in a database of its own, through the same
harness SC-8's runs are made with (``deploy/tools/diagnosis_runs.py``). The
platform then classifies and diagnoses every loss from its own records, and the
run is sealed with the simulator's ledger. The gate reads it from every side:

* **every loss is diagnosed**, and nothing is left for another run;
* **the causes this seed lost passes to are named for them**: the seed is
  pinned so that a dead receiver loses passes, and a stepped clock loses
  passes it shares with one, and the check fails if either loses none;
* **a loss several faults acted on is given one of their causes, or none**,
  never a cause no fault there has;
* **the control is never given a cause**: a loss only the degraded decoder
  caused is *undetermined*;
* **nothing the platform holds names a fault** or one of its parameters, with
  a planted label as the positive control (D-105);
* **every row is simulated**, and the sealed run reads back, judged;
* **a run again writes nothing.**

The report half, which builds SC-8 from sealed runs, is
``tests/unit/test_report_diagnosis.py``. The rarer causes — an obstruction, an
interference source or a silence that loses a whole pass — are measured over the
longer runs the harness makes by hand (``OPERATIONS.md`` § Loss diagnosis),
because a fleet small enough for every build seldom has one.

Reference: docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md Stage 27's completion gate;
docs/DECISIONS.md D-105, D-189, D-270 to D-278.
"""

from __future__ import annotations

import importlib.util
import sys
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.datasets.diagnosis_runs import read_diagnosis_run  # noqa: E402
from meridian.registry import psycopg_registry  # noqa: E402
from meridian.reliability import classification  # noqa: E402
from meridian.reliability.config import ReliabilityConfig  # noqa: E402
from meridian.reliability.diagnosis import METHOD  # noqa: E402
from meridian.reports.diagnosis_truth import CAUSE_OF, judge_run  # noqa: E402
from meridian.store.loss_diagnoses import find_undiagnosed  # noqa: E402

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
CONFIG = ReliabilityConfig()
PLAN = {
    "scenario": "diagnosis",
    "master_seed": 4472,
    "stations": 4,
    "start": datetime(2026, 8, 12, tzinfo=UTC),
    "hours": 12.0,
    "silent_satellite": "norad:57166",
}
"""Pinned: a fleet in which a dead receiver loses passes, and a stepped clock
loses passes it held while the receiver was also down. A change that moves
them fails the checks below, not silently. A fleet small enough for every build
seldom has an obstruction, interference or silence that loses a whole pass;
those are measured over the harness's longer runs."""

LEDGER_ONLY = (
    "receiver_down",
    "decoder_degraded",
    "clock_step",
    "step_s",
    "rise_db",
    "below_elevation_deg",
    "azimuth_from_deg",
    "start_hour_utc",
    "fleet_wide",
)
"""Words only the simulator's ledger uses. A cause's own name, such as
``obstruction``, is a diagnosis's answer and legitimately stored."""

TEXT_TYPES = ("text", "jsonb", "json", "character varying", "ARRAY")


@pytest.fixture(scope="module")
def harness() -> Iterator[ModuleType]:
    """The tool SC-8's runs are made with, imported as running it does."""
    sys.path.insert(0, str(REPO / "deploy" / "migrations"))
    spec = importlib.util.spec_from_file_location(
        "diagnosis_runs", REPO / "deploy" / "tools" / "diagnosis_runs.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["diagnosis_runs"] = module
    spec.loader.exec_module(module)
    try:
        yield module
    finally:
        sys.modules.pop("diagnosis_runs", None)


@pytest.fixture(scope="module")
def flown(
    harness: ModuleType, database_url: str, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[tuple[Any, Any, Path]]:
    """One fleet flown, settled and sealed; its connection still open to read."""
    url = database_url
    plan = harness.Plan(**PLAN)
    root = tmp_path_factory.mktemp("datasets")
    ids = harness.station_id_for(plan.master_seed)
    original = psycopg_registry.generate_station_id
    psycopg_registry.generate_station_id = lambda: next(ids)
    try:
        with (
            harness.scratch(url, plan.master_seed) as scratch_url,
            psycopg.connect(scratch_url) as conn,
        ):
            ledger, station_ids = harness.fly(conn, plan, state_dir=root / "work")
            harness.settle(conn, plan, CONFIG)
            sealed = harness.seal(conn, plan, ledger, station_ids, CONFIG, root)
            yield conn, harness, sealed
    finally:
        psycopg_registry.generate_station_id = original


def judged(sealed: Path) -> list[Any]:
    return judge_run(read_diagnosis_run(sealed))


def test_every_loss_is_diagnosed(flown: tuple[Any, Any, Path]) -> None:
    conn, _, sealed = flown

    left = find_undiagnosed(
        conn,
        method=METHOD,
        config_sha256=CONFIG.diagnosis.sha256(),
        classification_method=classification.METHOD,
        classification_sha256=CONFIG.classification.sha256(),
        verdict_method=None,
    )

    assert left == []
    assert read_diagnosis_run(sealed).diagnoses


def test_a_dead_receiver_s_losses_are_named_for_it(
    flown: tuple[Any, Any, Path],
) -> None:
    _, _, sealed = flown
    found = [one for one in judged(sealed) if one.truth == "station_not_listening"]

    assert found, "the pinned seed lost no pass to a dead receiver"
    assert Counter(one.diagnosed for one in found)["station_not_listening"]


def test_a_stepped_clock_s_losses_are_named_for_it(
    flown: tuple[Any, Any, Path],
) -> None:
    _, _, sealed = flown

    stepped = [one for one in judged(sealed) if "clock_step" in one.kinds]

    assert stepped, "the stepped clock acted on no lost pass"
    assert any(one.diagnosed == "timing_fault" for one in stepped)


def test_a_loss_several_faults_acted_on_gets_one_of_theirs_or_none(
    flown: tuple[Any, Any, Path],
) -> None:
    _, _, sealed = flown

    for one in judged(sealed):
        if one.truth == "several":
            allowed = {CAUSE_OF.get(kind, "undetermined") for kind in one.kinds}
            assert one.diagnosed in allowed | {"undetermined"}, one


def test_the_control_is_never_given_a_cause(flown: tuple[Any, Any, Path]) -> None:
    _, _, sealed = flown

    controls = [one for one in judged(sealed) if one.truth == "control"]

    assert all(one.diagnosed == "undetermined" for one in controls)


def test_nothing_the_platform_holds_names_a_fault(
    flown: tuple[Any, Any, Path],
) -> None:
    conn, _, _ = flown

    assert labels_found(conn) == []
    with conn.transaction(force_rollback=True):
        conn.execute(
            "update stations set name = 'clock_step' where station_id ="
            " (select min(station_id) from stations)"
        )
        assert labels_found(conn) == ["stations.name"]


def test_every_row_is_simulated_and_reads_back(flown: tuple[Any, Any, Path]) -> None:
    conn, _, sealed = flown
    run = read_diagnosis_run(sealed)

    stored = conn.execute(
        "select bool_and(simulated), count(*) from loss_diagnoses"
    ).fetchone()
    assert stored == (True, len(run.diagnoses))
    assert all(one["simulated"] is True for one in (*run.cases, *run.diagnoses))
    assert run.run["simulated"] is True
    assert len(judged(sealed)) == len(run.diagnoses)


def test_a_run_again_writes_nothing(flown: tuple[Any, Any, Path]) -> None:
    conn, harness, _ = flown
    before = conn.execute("select count(*) from loss_diagnoses").fetchone()[0]

    harness.settle(conn, harness.Plan(**PLAN), CONFIG)

    assert conn.execute("select count(*) from loss_diagnoses").fetchone()[0] == before


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
