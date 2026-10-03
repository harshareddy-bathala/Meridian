"""What a diagnosis may read, and who may read a diagnosis (D-102, D-105).

* **A diagnosis is never a yield feature.** Nothing on the prediction or the
  scheduling path imports the diagnosis or names its table, so what a pass's
  loss was blamed on cannot leak into whether the next pass is scheduled.
* **The diagnosis never reaches its own answer key.** No module on its path
  imports the simulator, the fault ledger or the modules that judge fault runs
  against it: the injected cause is joined to diagnoses afterwards, in the
  evaluation report, and nowhere else.
* **Nor an archive, a model or the network.** Meridian's own data only at
  runtime (D-102, D-108): no dataset, no prediction module (a verdict is read
  from its stored row, never scored here), no ingest, no HTTP client.

Each has a positive control, without which an empty list of crossings proves
nothing.

Reference: docs/DECISIONS.md D-102, D-105, D-108, D-272.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

PLATFORM = Path(__file__).resolve().parents[2] / "platform" / "src" / "meridian"

DIAGNOSIS_PATH = (
    "reliability/diagnosis.py",
    "reliability/diagnosis_causes.py",
    "reliability/diagnosis_evidence.py",
    "reliability/diagnosis_gather.py",
    "reliability/diagnosis_run.py",
    "reliability/obstruction_map.py",
    "reliability/satellite_evidence.py",
    "store/loss_diagnoses.py",
    "store/diagnosis_reads.py",
    "jobs/diagnosis_round.py",
)
"""Every module a diagnosis is made by."""

ANSWER_KEY = (
    "meridian_sim",
    "meridian.reliability.fault_ledger",
    "meridian.reliability.fault_record",
    "meridian.reliability.fault_check",
    "meridian.reliability.faults",
    "meridian.reliability.fault_model",
    "meridian.reliability.fault_offline",
)
"""Where the injected cause lives, or what reads it."""

ELSEWHERE = (
    "meridian.datasets",
    "meridian.prediction",
    "meridian_ingest",
    "httpx",
    "urllib",
    "requests",
    "http",
)
"""An archive, a dataset, a model or the network."""

DIAGNOSIS_MODULES = (
    "meridian.reliability.diagnosis",
    "meridian.reliability.diagnosis_causes",
    "meridian.reliability.diagnosis_evidence",
    "meridian.reliability.diagnosis_gather",
    "meridian.reliability.diagnosis_run",
    "meridian.reliability.obstruction_map",
    "meridian.store.loss_diagnoses",
)

YIELD_PATH = ("prediction", "scheduler")


def imported_modules(path: Path) -> Iterator[tuple[int, tuple[str, ...]]]:
    """Each import in ``path``: its line, and every module it may be importing.

    ``from a.b import c`` imports the module ``a.b.c`` whenever ``c`` is one,
    which is how this package imports its own (``from meridian.reliability
    import classification``). So it is read as ``a.b`` and as ``a.b.c``, or a
    module reached that way would pass unseen.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, (alias.name,)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            named = (f"{node.module}.{alias.name}" for alias in node.names)
            yield node.lineno, (node.module, *named)


def reaching(path: Path, prefixes: tuple[str, ...]) -> list[str]:
    """Every import in ``path`` of a module under one of ``prefixes``, once each."""
    found = []
    for line, modules in imported_modules(path):
        under = [
            module
            for module in modules
            if any(module == one or module.startswith(f"{one}.") for one in prefixes)
        ]
        if under:
            found.append(f"{path.name}:{line} imports {under[0]}")
    return found


def test_the_diagnosis_never_reaches_its_answer_key() -> None:
    crossings = [
        line
        for relative in DIAGNOSIS_PATH
        for line in reaching(PLATFORM / relative, ANSWER_KEY)
    ]

    assert crossings == []


def test_the_diagnosis_reads_no_archive_model_or_network() -> None:
    crossings = [
        line
        for relative in DIAGNOSIS_PATH
        for line in reaching(PLATFORM / relative, ELSEWHERE)
    ]

    assert crossings == []


def test_no_yield_feature_reads_a_diagnosis() -> None:
    crossings = [
        line
        for package in YIELD_PATH
        for path in sorted((PLATFORM / package).rglob("*.py"))
        for line in [
            *reaching(path, DIAGNOSIS_MODULES),
            *(
                [f"{path.name} names loss_diagnoses"]
                if "loss_diagnoses" in path.read_text(encoding="utf-8")
                else []
            ),
        ]
    ]

    assert crossings == []


def test_the_scan_sees_a_diagnosis_reaching_the_ledger(tmp_path: Path) -> None:
    """Positive control for the answer key."""
    module = tmp_path / "diagnosis_run.py"
    module.write_text("from meridian.reliability.fault_ledger import read\n")

    assert reaching(module, ANSWER_KEY) == [
        "diagnosis_run.py:1 imports meridian.reliability.fault_ledger"
    ]


def test_the_scan_sees_a_module_imported_from_its_package(tmp_path: Path) -> None:
    """Positive control for the form this package imports its own modules by."""
    module = tmp_path / "diagnosis_run.py"
    module.write_text(
        "from meridian.reliability import classification, fault_ledger\n"
        "from meridian import datasets\n"
    )

    assert reaching(module, ANSWER_KEY) == [
        "diagnosis_run.py:1 imports meridian.reliability.fault_ledger"
    ]
    assert reaching(module, ELSEWHERE) == [
        "diagnosis_run.py:2 imports meridian.datasets"
    ]


def test_the_scan_sees_a_feature_reading_a_diagnosis(tmp_path: Path) -> None:
    """Positive control for the yield path."""
    module = tmp_path / "features.py"
    module.write_text("import meridian.store.loss_diagnoses\n")

    assert reaching(module, DIAGNOSIS_MODULES) == [
        "features.py:1 imports meridian.store.loss_diagnoses"
    ]


def test_every_module_on_the_path_exists() -> None:
    """A renamed module would leave its scan reading nothing."""
    assert [one for one in DIAGNOSIS_PATH if not (PLATFORM / one).is_file()] == []
