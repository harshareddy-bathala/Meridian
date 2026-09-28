"""``meridian.reliability`` — which of its modules the labelling path may call.

The classification is called from two places: the snapshot labeller, which may
reach no database (D-143), and the live accounting, which reads one. So:

* **The shared rules import the standard library and each other, nothing
  else.** A later import of the store into ``classification`` would carry a
  database onto the labelling path without any datasets module changing, which
  the datasets scan, reading one file at a time, would never see.
* **Nothing in the package imports ``meridian.datasets``.** A live figure
  computed from a snapshot would report the past as the present.
* **``__init__`` imports nothing.** Importing ``meridian.reliability.
  classification`` runs the package's ``__init__`` first, so anything imported
  there reaches the labelling path too.

Each has a positive control, without which an empty list of crossings proves
nothing.

Reference: docs/DECISIONS.md D-143, D-180.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RELIABILITY = REPO_ROOT / "platform" / "src" / "meridian" / "reliability"

SHARED_RULES = frozenset({"classification.py", "satellite_silence.py"})
"""The modules both the labeller and the live accounting call."""


def imported_modules(path: Path) -> Iterator[tuple[int, str]]:
    """Each import in ``path``, as its line and full dotted module."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                yield node.lineno, "." * node.level + (node.module or "")
            elif node.module:
                yield node.lineno, node.module


def beyond_the_standard_library(path: Path) -> list[str]:
    """Every import that is neither the standard library nor a shared rule."""
    allowed = {
        f"meridian.reliability.{name.removesuffix('.py')}" for name in SHARED_RULES
    }
    return [
        f"{path.name}:{line} imports {module}"
        for line, module in imported_modules(path)
        if module != "__future__"
        and module not in allowed
        and module.split(".")[0] not in sys.stdlib_module_names
    ]


def test_the_shared_rules_import_only_the_standard_library() -> None:
    crossings = [
        line
        for name in sorted(SHARED_RULES)
        for line in beyond_the_standard_library(RELIABILITY / name)
    ]

    assert crossings == []


def test_the_scan_sees_an_import_from_outside(tmp_path: Path) -> None:
    """Positive control: a module importing the store is caught."""
    module = tmp_path / "classification.py"
    module.write_text("from meridian.store import heartbeats\nimport json\n")

    assert beyond_the_standard_library(module) == [
        "classification.py:1 imports meridian.store"
    ]


def test_nothing_in_the_package_imports_a_dataset() -> None:
    crossings = [
        f"{path.name}:{line} imports {module}"
        for path in sorted(RELIABILITY.rglob("*.py"))
        for line, module in imported_modules(path)
        if module == "meridian.datasets" or module.startswith("meridian.datasets.")
    ]

    assert crossings == []


def test_the_package_init_imports_nothing() -> None:
    imports = [
        module
        for _, module in imported_modules(RELIABILITY / "__init__.py")
        if module != "__future__"
    ]

    assert imports == []


def test_the_labeller_is_seen_calling_the_classification() -> None:
    """Positive control: the labelling path does reach these rules."""
    labels = REPO_ROOT / "platform" / "src" / "meridian" / "datasets" / "labels.py"

    assert "meridian.reliability.classification" in {
        module for _, module in imported_modules(labels)
    }
