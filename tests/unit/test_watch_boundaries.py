"""What the receive-chain watch may read, and who may read the watch (D-102, D-105).

* **The watch never reaches its answer key.** No module on its path imports the
  simulator, the fault ledger or the modules that judge fault runs: SC-9's
  injected degradation is joined to warnings afterwards, in the evaluation
  report, and nowhere else (D-105, D-290).
* **Nor an archive, a dataset, a model or the network.** A chain's baseline is
  the station's own past (D-291).
* **A warning is never a yield feature.** Nothing on the prediction or the
  scheduling path imports the watch or names its tables.

Each has a positive control, without which an empty list proves nothing.

Reference: docs/DECISIONS.md D-102, D-105, D-290, D-291.
"""

from __future__ import annotations

import ast
from pathlib import Path

PLATFORM = Path(__file__).resolve().parents[2] / "platform" / "src" / "meridian"

WATCH_PATH = (
    "reliability/watch_baseline.py",
    "reliability/watch_build.py",
    "reliability/watch_detect.py",
    "reliability/watch_run.py",
    "jobs/watch_round.py",
    "store/watch_reads.py",
    "store/signal_baselines.py",
    "store/receive_chain_warnings.py",
)
"""Every module a baseline or a warning is made by."""

FORBIDDEN = (
    "meridian_sim",
    "meridian.reliability.fault_ledger",
    "meridian.reliability.fault_record",
    "meridian.reliability.fault_check",
    "meridian.reliability.faults",
    "meridian.reliability.fault_model",
    "meridian.reliability.fault_offline",
    "meridian.reports",
    "meridian.datasets",
    "meridian.prediction",
    "meridian_ingest",
    "httpx",
    "urllib",
    "requests",
    "http",
)
"""The injected cause, what judges it, an archive, a dataset, a model, the network."""

WATCH_MODULES = tuple(
    "meridian." + one.removesuffix(".py").replace("/", ".") for one in WATCH_PATH
)
WATCH_TABLES = ("signal_baselines", "receive_chain_warnings")
YIELD_PATH = ("prediction", "scheduler")


def reaching(path: Path, prefixes: tuple[str, ...]) -> list[str]:
    """Each import in ``path`` of a module under ``prefixes``, ``from a import b``
    read as ``a`` and as ``a.b``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            modules = [node.module, *(f"{node.module}.{a.name}" for a in node.names)]
        else:
            continue
        under = [
            one
            for one in modules
            if any(one == p or one.startswith(f"{p}.") for p in prefixes)
        ]
        if under:
            found.append(f"{path.name}:{node.lineno} imports {under[0]}")
    return found


def test_the_watch_reaches_no_answer_key_archive_model_or_network() -> None:
    crossings = [
        line
        for relative in WATCH_PATH
        for line in reaching(PLATFORM / relative, FORBIDDEN)
    ]

    assert crossings == []


def test_no_yield_feature_reads_the_watch() -> None:
    crossings = [
        f"{path.name}: {line}"
        for package in YIELD_PATH
        for path in sorted((PLATFORM / package).rglob("*.py"))
        for line in [
            *reaching(path, WATCH_MODULES),
            *(
                f"names {table}"
                for table in WATCH_TABLES
                if table in path.read_text(encoding="utf-8")
            ),
        ]
    ]

    assert crossings == []


def test_the_scan_sees_the_watch_reaching_the_ledger(tmp_path: Path) -> None:
    """Positive control, in the form this package imports its own modules."""
    module = tmp_path / "watch_build.py"
    module.write_text(
        "from meridian.reliability import fault_ledger\nimport meridian_sim.ledger\n"
    )

    assert reaching(module, FORBIDDEN) == [
        "watch_build.py:1 imports meridian.reliability.fault_ledger",
        "watch_build.py:2 imports meridian_sim.ledger",
    ]


def test_the_scan_sees_a_feature_reading_a_warning(tmp_path: Path) -> None:
    """Positive control for the yield path."""
    module = tmp_path / "features.py"
    module.write_text("from meridian.store import receive_chain_warnings\n")

    assert reaching(module, WATCH_MODULES) == [
        "features.py:1 imports meridian.store.receive_chain_warnings"
    ]


def test_every_module_on_the_path_exists() -> None:
    """A renamed module would leave its scan reading nothing."""
    assert [one for one in WATCH_PATH if not (PLATFORM / one).is_file()] == []
