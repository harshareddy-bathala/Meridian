"""``meridian.datasets`` — which modules may reach a database, and who reaches it.

Two lines, both read from the source rather than from a running import:

* **Only ``export`` may reach a database.** Every other module in the package is
  on the labelling path, and Stage 15's gate is that labelling reads a snapshot
  and nothing else (D-143). The gate tests prove that for the fixtures they
  run; this proves it for every line, including the ones no fixture reaches.
* **Only the ``meridian snapshot`` command imports the package.** The
  scheduler, the API and the jobs service run on live tables; a snapshot is
  training and evaluation input, and a runtime path that read one would be
  scheduling on the past without saying so.

Each has a positive control: the scan run on a module known to cross the line,
without which an empty list of crossings proves nothing.

Reference: docs/DECISIONS.md D-143, D-145.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

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
        if DATASETS not in path.parents and path not in MAY_IMPORT_DATASETS
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
