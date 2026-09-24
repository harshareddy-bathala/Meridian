"""``meridian.datasets`` — which modules may reach a database, and who reaches it.

Two lines, both read from the source rather than from a running import:

* **Only ``export`` may reach a database.** Every other module in the package is
  on the labelling path, and Stage 15's gate is that labelling reads a snapshot
  and nothing else (D-143). The gate tests prove that for the fixtures they
  run; this proves it for every line, including the ones no fixture reaches.
* **Only the ``meridian snapshot`` command, and prediction's fitting side,
  import the package.** The scheduler, the API and the jobs service run on
  live tables; a snapshot is training and evaluation input, and a runtime
  path that read one would be scheduling on the past without saying so.
  ``meridian.prediction`` fits on datasets (D-156), except ``score``, which
  the scheduler will import and which reads a model file instead.

Stage 16 adds two more:

* **Only the export side propagates.** Archive passes and pass tracks are
  computed once, at export, and frozen (D-150, D-158); ``archive_passes`` and
  ``pass_tracks`` take the orbit's plain types and are handed a propagator,
  and nothing that labels imports either of them or a propagator.
* **The propensity imports nothing that holds an outcome.** D-152 is kept by
  the estimator's signature; this keeps it at the module line too, so a later
  import of labels or evidence into the estimator fails here.

Each has a positive control: the scan run on a module known to cross the line,
without which an empty list of crossings proves nothing.

Reference: docs/DECISIONS.md D-143, D-145, D-150, D-152, D-158.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PLATFORM = REPO_ROOT / "platform" / "src" / "meridian"
DATASETS = PLATFORM / "datasets"

MAY_READ_THE_DATABASE = frozenset({"export.py"})

REACHES_OUTSIDE = (
    "psycopg",
    "socket",
    "urllib",
    "http",
    "httpx",
    "meridian.store",
    "meridian.registry",
    "meridian.api",
    "meridian.config",
)
"""A driver, the network, the SQL layer, the registry over it, or the settings
that name a database. The labelling path needs none of them."""

MAY_IMPORT_DATASETS = frozenset({PLATFORM / "cli_snapshot.py"})
PREDICTION = PLATFORM / "prediction"
SCORED_AT_RUNTIME = frozenset({"score.py"})
"""The one prediction module the scheduler will import (D-155). It reads a
model file, never a dataset, so it is held to the runtime rule."""


def may_import_datasets(path: Path) -> bool:
    """The snapshot command, and prediction's fitting side (D-156)."""
    if path in MAY_IMPORT_DATASETS:
        return True
    return PREDICTION in path.parents and path.name not in SCORED_AT_RUNTIME


def imported_modules(path: Path) -> Iterator[tuple[int, str]]:
    """Each absolute import in ``path``, as its line and full dotted module."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.lineno, node.module


def reaches_outside(module: str) -> bool:
    return any(
        module == banned or module.startswith(f"{banned}.")
        for banned in REACHES_OUTSIDE
    )


def crossings_to_outside(path: Path) -> list[str]:
    return [
        f"{path.relative_to(REPO_ROOT)}:{line} imports {module}"
        for line, module in imported_modules(path)
        if reaches_outside(module)
    ]


def labelling_modules() -> list[Path]:
    return [
        path
        for path in sorted(DATASETS.glob("*.py"))
        if path.name not in MAY_READ_THE_DATABASE
    ]


def test_nothing_on_the_labelling_path_can_reach_a_database() -> None:
    crossings = [
        line for path in labelling_modules() for line in crossings_to_outside(path)
    ]

    assert labelling_modules()
    assert crossings == []


def test_the_scan_sees_the_one_module_that_may() -> None:
    """Positive control: ``export`` reads the database, and the scan says so."""
    assert crossings_to_outside(DATASETS / "export.py")


def test_only_the_snapshot_command_imports_the_package() -> None:
    crossings = [
        f"{path.relative_to(REPO_ROOT)}:{line} imports {module}"
        for path in sorted(PLATFORM.rglob("*.py"))
        if DATASETS not in path.parents and not may_import_datasets(path)
        for line, module in imported_modules(path)
        if module == "meridian.datasets" or module.startswith("meridian.datasets.")
    ]

    assert crossings == []


def test_the_command_is_seen_importing_it() -> None:
    """Positive control for the test above."""
    (command,) = MAY_IMPORT_DATASETS

    assert any(
        module.startswith("meridian.datasets.")
        for _, module in imported_modules(command)
    )


MAY_PROPAGATE = frozenset({"export.py"})
"""Holds the orbit service. The two modules below hold only the orbit's types."""

HANDED_A_PROPAGATOR = frozenset({"archive_passes.py", "pass_tracks.py"})
"""The export side's pure halves: rows and a propagator in, rows out (D-150,
D-158). Nothing on the labelling path imports either."""

OUTCOME_FREE = {
    "propensity.py": frozenset({"meridian.datasets.selection_config"}),
    "weighting.py": frozenset({"meridian.datasets.propensity"}),
}
"""The estimator and the weights, and everything each may import from Meridian."""


def orbit_imports(path: Path) -> list[str]:
    return [
        module
        for _, module in imported_modules(path)
        if module == "meridian.orbit" or module.startswith("meridian.orbit.")
    ]


def test_nothing_that_labels_imports_the_orbit() -> None:
    crossings = [
        f"{path.name} imports {module}"
        for path in labelling_modules()
        if path.name not in HANDED_A_PROPAGATOR
        for module in orbit_imports(path)
    ]

    assert crossings == []


@pytest.mark.parametrize("name", sorted(HANDED_A_PROPAGATOR))
def test_the_export_side_takes_types_and_is_handed_a_propagator(name: str) -> None:
    assert orbit_imports(DATASETS / name) == ["meridian.orbit.types"]


def test_nothing_that_labels_imports_the_export_side() -> None:
    """The tracks and the archive passes reach labelling as files, never as code."""
    crossings = [
        f"{path.name} imports {module}"
        for path in labelling_modules()
        if path.name not in HANDED_A_PROPAGATOR
        for _, module in imported_modules(path)
        if module.removeprefix("meridian.datasets.") + ".py" in HANDED_A_PROPAGATOR
    ]

    assert crossings == []


def test_the_export_is_seen_importing_the_export_side() -> None:
    """Positive control for the test above: the scan's match, on ``export``."""
    held = {
        module.removeprefix("meridian.datasets.") + ".py"
        for _, module in imported_modules(DATASETS / "export.py")
    }

    assert held >= HANDED_A_PROPAGATOR


def test_the_export_is_seen_holding_the_orbit_service() -> None:
    """Positive control for the two tests above."""
    (export,) = MAY_PROPAGATE

    assert "meridian.orbit.skyfield_service" in orbit_imports(DATASETS / export)


@pytest.mark.parametrize("name", sorted(OUTCOME_FREE))
def test_the_propensity_imports_nothing_that_holds_an_outcome(name: str) -> None:
    ours = {
        module
        for _, module in imported_modules(DATASETS / name)
        if module.startswith("meridian.")
    }

    assert ours <= OUTCOME_FREE[name]


def test_the_module_that_joins_outcomes_is_seen_doing_so() -> None:
    """Positive control: ``selection`` pairs outcomes with estimates, after."""
    modules = {module for _, module in imported_modules(DATASETS / "selection.py")}

    assert "meridian.datasets.labels" in modules
