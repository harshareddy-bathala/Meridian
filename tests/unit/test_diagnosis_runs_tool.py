"""``deploy/tools/diagnosis_runs.py`` — the parts that need no database.

The fleets themselves run in ``tests/integration/test_diagnosis_gate.py``. Here:
the configuration becomes one fleet per scenario and seed, station ids follow
from the seed so a run made again is the same run, and the run's own record
says it is simulated.

Reference: docs/DECISIONS.md D-278.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from collections.abc import Iterator
from itertools import islice
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "analysis" / "configs" / "diagnosis.toml.example"


@pytest.fixture(scope="module")
def tool() -> Iterator[ModuleType]:
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


def test_the_example_is_one_fleet_per_scenario_and_seed(tool: ModuleType) -> None:
    plans = tool.plans(EXAMPLE.read_text())

    assert len(plans) == 12
    assert {one.scenario for one in plans} == {
        "diagnosis",
        "obstruction",
        "interference",
        "silent",
    }
    assert {one.master_seed for one in plans} == {4471, 4472, 4473}
    assert len({one.run_id for one in plans}) == 12


def test_station_ids_follow_from_the_seed(tool: ModuleType) -> None:
    first = list(islice(tool.station_id_for(4471), 8))

    assert first == list(islice(tool.station_id_for(4471), 8))
    assert first != list(islice(tool.station_id_for(4472), 8))
    assert len(set(first)) == 8
    assert all(re.fullmatch(r"st_[0-9a-f]{6}", one) for one in first)


def test_a_run_records_itself_as_simulated(tool: ModuleType) -> None:
    (plan, *_) = tool.plans(EXAMPLE.read_text())

    record = plan.record()

    assert record["simulated"] is True
    assert record["scenario"] == "diagnosis"
    assert plan.end > plan.start
