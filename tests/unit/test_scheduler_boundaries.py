"""``meridian.scheduler`` — what it may reach, read from the source and a fresh import.

* **It consumes predictions, never the observation store** (``ARCHITECTURE.md``).
  What a station did reaches a schedule only as a model's probability, which
  reads a labelled dataset; never as a query on ``observations``.
* **From prediction it imports the scorer, the live path and the replay
  only** (D-169, D-172): never the fitter, the evaluation or the report over
  them, which need the ``meridian[fit]`` extra the image does not install
  (D-155). The replay is how the retrospective comparison reads a dataset's
  outcomes — from a frozen snapshot, for the oracle and the tally — so no
  scheduler module reads a dataset, or an observation, itself.
* **Loading every scheduler module, and the live path it scores with, leaves
  scikit-learn, scipy and the fitter unimported.** The source scan sees direct
  imports; a fresh interpreter sees what they drag in.

Each has a positive control.

Reference: docs/DECISIONS.md D-155, D-169, D-172; docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEDULER = REPO_ROOT / "platform" / "src" / "meridian" / "scheduler"

OBSERVATION_STORE = (
    "meridian.observations",
    "meridian.store.observations",
    "meridian.store.observation_history",
    "meridian.store.archive_observations",
    "meridian.store.snapshot_reads",
    "meridian.store.ingest_records",
)
"""What stations reported, and the archive's receptions."""

PREDICTION_ALLOWED = (
    "meridian.prediction.score",
    "meridian.prediction.live",
    "meridian.prediction.replay",
)

FIT_EXTRA = ("sklearn", "scipy", "joblib", "pandas", "meridian.prediction.fit")


def imported_modules(path: Path) -> Iterator[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.lineno, node.module


def reaches(module: str, banned: tuple[str, ...]) -> bool:
    return any(module == one or module.startswith(f"{one}.") for one in banned)


def crossings(paths: list[Path]) -> list[str]:
    found = []
    for path in paths:
        for line, module in imported_modules(path):
            observed = reaches(module, OBSERVATION_STORE)
            fitting = reaches(module, FIT_EXTRA)
            predicting = reaches(module, ("meridian.prediction",)) and not reaches(
                module, PREDICTION_ALLOWED
            )
            if observed or fitting or predicting:
                found.append(f"{path.name}:{line} imports {module}")
    return found


def test_the_scheduler_reads_predictions_not_observations() -> None:
    paths = sorted(SCHEDULER.rglob("*.py"))

    assert len(paths) > 1
    assert crossings(paths) == []


def test_the_scan_would_notice_each_crossing(tmp_path: Path) -> None:
    offender = tmp_path / "offender.py"
    offender.write_text(
        "from meridian.store.observations import find_observation\n"
        "import meridian.observations.ingest\n"
        "from meridian.prediction.evaluation import evaluate_model\n"
        "from meridian.prediction.lineage import raw_of\n"
        "from sklearn.linear_model import LogisticRegression\n"
        "from meridian.prediction.live import load_live_scorer\n"
        "from meridian.prediction.score import predict\n"
        "from meridian.store.passes import find_passes_in_horizon\n",
        encoding="utf-8",
    )

    assert len(crossings([offender])) == 5


LOAD_EVERY_MODULE = """
import importlib, pkgutil, sys
import meridian.scheduler as scheduler
for one in pkgutil.iter_modules(scheduler.__path__):
    importlib.import_module(f"meridian.scheduler.{one.name}")
importlib.import_module("meridian.prediction.live")
importlib.import_module("meridian.prediction.replay")
banned = sys.argv[1:]
print(" ".join(sorted(
    name for name in sys.modules
    if any(name == one or name.startswith(one + ".") for one in banned)
)))
"""


def loaded_after_import(banned: tuple[str, ...]) -> str:
    """What a fresh interpreter holds of ``banned`` once every module is loaded."""
    result = subprocess.run(
        [sys.executable, "-c", LOAD_EVERY_MODULE, *banned],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_loading_the_scheduler_leaves_the_fit_extra_unimported() -> None:
    assert loaded_after_import(FIT_EXTRA) == ""


def test_the_fresh_import_would_notice_a_module_loaded() -> None:
    """Positive control: the solver is loaded, and is seen to be."""
    assert "highspy" in loaded_after_import(("highspy",)).split()
