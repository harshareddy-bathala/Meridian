"""Regional monitoring stays apart from the platform's own monitoring and the schedule.

Three properties the roadmap and D-228 state, checked by reading the source:

* nothing in ``meridian.regions`` imports ``meridian.metrics`` — Prometheus
  watches Meridian, this watches places, and the two share no name in code;
* no Prometheus rule, Grafana dashboard or metric is named for a region;
* nothing on the scheduling, prediction, reliability or API path imports
  ``meridian.regions`` — a report is an operator's file, never a runtime input.

Each scan has a positive control, so a scan that could not see an import does
not pass by default.

Reference: docs/DECISIONS.md D-131, D-227, D-228.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLATFORM = REPO / "platform" / "src" / "meridian"


def imports(path: Path) -> Iterator[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module


def crossings(paths: list[Path], banned: str) -> list[str]:
    return [
        f"{path.name} imports {module}"
        for path in paths
        for module in imports(path)
        if module == banned or module.startswith(banned + ".")
    ]


def test_regions_never_reach_the_platforms_metrics() -> None:
    paths = sorted((PLATFORM / "regions").rglob("*.py"))
    assert paths
    assert crossings(paths, "meridian.metrics") == []


def test_no_runtime_module_imports_regions() -> None:
    runtime = [
        path
        for folder in (
            "scheduler",
            "prediction",
            "reliability",
            "api",
            "jobs",
            "registry",
        )
        for path in sorted((PLATFORM / folder).rglob("*.py"))
    ]
    assert runtime
    assert crossings(runtime, "meridian.regions") == []


def test_the_import_scan_would_notice_a_crossing(tmp_path: Path) -> None:
    rogue = tmp_path / "rogue.py"
    rogue.write_text("from meridian.metrics.exposition import x\n", encoding="utf-8")
    assert crossings([rogue], "meridian.metrics") != []


def test_no_prometheus_or_grafana_name_is_a_regional_one() -> None:
    watched = [
        path
        for pattern in ("*.yml", "*.yaml", "*.json")
        for folder in ("prometheus", "grafana", "alertmanager")
        for path in (REPO / "deploy" / folder).rglob(pattern)
    ]
    assert watched
    named = [
        str(path.relative_to(REPO))
        for path in watched
        if "region" in path.read_text(encoding="utf-8").lower()
    ]
    assert named == []


def test_no_metric_is_named_for_a_region() -> None:
    metrics = sorted((PLATFORM / "metrics").rglob("*.py"))
    assert metrics
    assert not any("region" in path.read_text(encoding="utf-8") for path in metrics)
