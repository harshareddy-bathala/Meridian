"""``deploy/tools/watch_runs.py`` — what a night's batch reads before it flies.

The tool flies fleets for hours, so a batch must be able to stop and start
again: a fleet already sealed is found and skipped, and only the scenarios
SC-9 runs are accepted. Flying itself is ``tests/integration/test_watch_gate.py``.

Reference: docs/DECISIONS.md D-296.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

from meridian.datasets.watch_runs import publish_watch_run

REPO = Path(__file__).resolve().parents[2]
CONFIG = (REPO / "analysis" / "configs" / "watch.toml.example").read_text()


@pytest.fixture(scope="module")
def tool() -> Iterator[ModuleType]:
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


def test_the_example_asks_for_six_fleets_each_clean_first(tool: ModuleType) -> None:
    fleets = tool.plans(CONFIG)

    assert [one.run_id for one in fleets] == [
        f"watch-{scenario}-{seed}"
        for scenario in ("degradation", "clean")
        for seed in (4471, 4472, 4473)
    ]
    assert all(one.watched_from > one.start for one in fleets)


def test_a_scenario_sc9_does_not_run_is_refused(tool: ModuleType) -> None:
    with pytest.raises(ValueError, match="unknown scenarios"):
        tool.plans(CONFIG.replace('["degradation", "clean"]', '["sky"]'))


def test_a_fleet_already_sealed_is_found_and_another_is_not(
    tool: ModuleType, tmp_path: Path
) -> None:
    first, second, *_ = tool.plans(CONFIG)
    sealed = publish_watch_run(
        "",
        {**first.record(), "station_ids": []},
        [],
        [],
        root=tmp_path,
        stamp=("0030", first.start, datetime(2026, 8, 20, tzinfo=UTC)),
    ).path

    assert tool.already_sealed(first, tmp_path) == sealed
    assert tool.already_sealed(second, tmp_path) is None
