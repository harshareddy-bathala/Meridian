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

SHARED_RULES = frozenset(
    {
        "classification.py",
        "satellite_silence.py",
        "config.py",
        "slis.py",
        "budget.py",
        "report.py",
    }
)
"""The modules both the snapshot path and the live path call: the rules, the
configuration, the indicators, the budget and the report."""

DIAGNOSIS_RULES = frozenset(
    {
        "diagnosis.py",
        "diagnosis_causes.py",
        "diagnosis_evidence.py",
        "obstruction_map.py",
    }
)
"""Stage 27's rules: evidence in, a cause out, nothing reached (D-102, D-105).
Held to the same standard, so a rule cannot look up its own answer."""

PURE = SHARED_RULES | DIAGNOSIS_RULES


PACKAGE = "meridian.reliability"


def imported_modules(path: Path) -> Iterator[tuple[int, str]]:
    """Each import in ``path``, as its line and full dotted module.

    ``from meridian.reliability import x`` is read as ``meridian.reliability.x``:
    the package's ``__init__`` imports nothing, so whatever is taken from it is
    one of its modules, and is held to the rule its own name would be.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            for module in _from(node):
                yield node.lineno, module


def _from(node: ast.ImportFrom) -> list[str]:
    """The modules one ``from … import …`` names."""
    if node.level:
        return ["." * node.level + (node.module or "")]
    if node.module == PACKAGE:
        return [f"{PACKAGE}.{alias.name}" for alias in node.names]
    return [node.module] if node.module else []


def beyond_the_standard_library(path: Path) -> list[str]:
    """Every import that is neither the standard library nor a shared rule."""
    allowed = {f"{PACKAGE}.{name.removesuffix('.py')}" for name in PURE}
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
        for name in sorted(PURE)
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


def test_a_diagnosis_rule_reaching_the_ledger_is_caught(tmp_path: Path) -> None:
    """Positive control for Stage 27's rules: the answer key is outside."""
    module = tmp_path / "diagnosis_causes.py"
    module.write_text(
        "from meridian.reliability.fault_ledger import read_fault_ledger\n"
    )

    assert beyond_the_standard_library(module) == [
        "diagnosis_causes.py:1 imports meridian.reliability.fault_ledger"
    ]


def test_a_rule_taking_the_ledger_from_the_package_is_caught(tmp_path: Path) -> None:
    """Positive control for the form ``diagnosis.py`` imports its causes by."""
    module = tmp_path / "diagnosis.py"
    module.write_text(
        "from meridian.reliability import diagnosis_causes, fault_ledger\n"
    )

    assert beyond_the_standard_library(module) == [
        "diagnosis.py:1 imports meridian.reliability.fault_ledger"
    ]


def code_words(path: Path) -> set[str]:
    """Every name, attribute and imported module in ``path``: its code, not prose."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    words: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            words.add(node.id)
        elif isinstance(node, ast.Attribute):
            words.add(node.attr)
        elif isinstance(node, ast.alias):
            words.add(node.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            words.add(node.module)
    return words


def test_a_capture_does_not_depend_on_a_verdict() -> None:
    """D-271: no code SC-4 is counted by mentions a verdict."""
    readers = [
        f"{name}: {word}"
        for name in ("classification.py", "slis.py", "budget.py")
        for word in sorted(code_words(RELIABILITY / name))
        if "verdict" in word.lower()
    ]

    assert readers == []


def test_the_verdict_scan_sees_a_verdict_read(tmp_path: Path) -> None:
    """Positive control: code that reads a verdict is seen, prose is not."""
    module = tmp_path / "slis.py"
    module.write_text('"""No verdict here."""\nfrom meridian.store.verdicts import x\n')

    assert "meridian.store.verdicts" in code_words(module)
    assert "No verdict here." not in code_words(module)


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
