"""``meridian.prediction`` — who may hold a numerical stack, and what it may reach.

Two lines, read from the source rather than from a running import:

* **Only ``meridian.prediction.fit`` imports scikit-learn or scipy**, in any
  distribution. They are the ``meridian[fit]`` extra, which the platform image
  does not install (D-155). A second module importing them would work in every
  checkout, where the extra is installed for the tests, and fail on the Pi the
  first time the jobs service scored a pass.
* **No other prediction module imports numpy either.** numpy is in the image
  already, through skyfield, so this line is not about the Pi: scoring is plain
  Python so that a model file gives the same probability whichever numpy is
  installed, or none (D-163).
* **The prediction module reaches no database, no network and no orbit.**
  Features are a pure function of a snapshot (D-157), tracks were frozen at
  export (D-158), and ``CLAUDE.md`` says prediction knows nothing about MSP.
* **The scorer imports the standard library alone,** and nothing that reads a
  model back imports the fitter: the scheduler scores on the Pi from a file
  (D-155, D-163).

Each has a positive control, without which an empty list of crossings proves
nothing.

Reference: docs/DECISIONS.md D-155, D-157, D-158.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PREDICTION = REPO_ROOT / "platform" / "src" / "meridian" / "prediction"
DISTRIBUTIONS = ("platform", "client", "simulator", "ingest")

FITTING_ONLY = ("sklearn", "scipy", "joblib", "pandas")
"""The ``fit`` extra's weight, and what it brings with it. Not in the image."""

NUMERICAL = ("numpy", *FITTING_ONLY)
"""What a prediction module other than ``fit`` may not import."""

MAY_FIT = frozenset({PREDICTION / "fit.py"})

UNREACHABLE_FROM_PREDICTION = (
    "psycopg",
    "socket",
    "urllib",
    "http",
    "httpx",
    "fastapi",
    "sgp4",
    "skyfield",
    "meridian.store",
    "meridian.registry",
    "meridian.api",
    "meridian.config",
    "meridian.orbit",
    "meridian.datasets.export",
    "meridian.datasets.archive_passes",
    "meridian.datasets.pass_tracks",
)
"""A driver, the network, the API, MSP's home, a propagator, or the export
side of ``meridian.datasets``, which holds all three. Features read frozen
files (D-158); they never reach the code that froze them."""


def imported_modules(path: Path) -> Iterator[tuple[int, str]]:
    """Each absolute import in ``path``, as its line and full dotted module."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.lineno, node.module


def reaches(module: str, banned: tuple[str, ...]) -> bool:
    return any(module == one or module.startswith(f"{one}.") for one in banned)


def crossings(paths: list[Path], banned: tuple[str, ...]) -> list[str]:
    return [
        f"{path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path}"
        f":{line} imports {module}"
        for path in paths
        for line, module in imported_modules(path)
        if reaches(module, banned)
    ]


def test_only_the_fitting_module_imports_the_fit_extra() -> None:
    paths = [
        path
        for distribution in DISTRIBUTIONS
        for path in sorted((REPO_ROOT / distribution / "src").rglob("*.py"))
        if path not in MAY_FIT
    ]

    assert paths
    assert crossings(paths, FITTING_ONLY) == []


def test_only_the_fitting_module_of_prediction_imports_numpy() -> None:
    paths = [path for path in sorted(PREDICTION.rglob("*.py")) if path not in MAY_FIT]

    assert paths
    assert crossings(paths, NUMERICAL) == []


def test_prediction_reaches_no_database_network_or_orbit() -> None:
    paths = sorted(PREDICTION.rglob("*.py"))

    assert paths
    assert crossings(paths, UNREACHABLE_FROM_PREDICTION) == []


def test_the_scans_would_notice_a_crossing(tmp_path: Path) -> None:
    """The positive control: one offender for each list."""
    offender = tmp_path / "offender.py"
    offender.write_text(
        "import numpy as np\n"
        "from sklearn.linear_model import LogisticRegression\n"
        "from meridian.store.pool import open_pool\n"
        "from meridian.orbit import ElementSet\n"
        "from meridian.orbital import nothing\n",
        encoding="utf-8",
    )

    assert len(crossings([offender], FITTING_ONLY)) == 1
    assert len(crossings([offender], NUMERICAL)) == 2
    assert len(crossings([offender], UNREACHABLE_FROM_PREDICTION)) == 2


SCORER = PREDICTION / "score.py"
MODEL_READERS = (
    SCORER,
    PREDICTION / "model_files.py",
    PREDICTION / "verdict_score.py",
    PREDICTION / "verdict_files.py",
    PREDICTION / "verdict_evaluation.py",
    PREDICTION / "verdict_report.py",
)


def outside_the_standard_library(path: Path) -> list[str]:
    return [
        module
        for _, module in imported_modules(path)
        if module.split(".")[0] not in sys.stdlib_module_names
    ]


def test_the_scorer_imports_the_standard_library_alone() -> None:
    """What the scheduler imports on the Pi reads a file and does arithmetic."""
    assert list(imported_modules(SCORER))
    assert outside_the_standard_library(SCORER) == []


VERDICT_SCORING = (PREDICTION / "verdict_inputs.py", PREDICTION / "verdict_score.py")
VERDICT_MAY_IMPORT = ("meridian.prediction.score", "meridian.prediction.verdict_inputs")


def test_verdict_scoring_imports_the_standard_library_and_the_scorer_alone() -> None:
    """The verdict writer runs in the image and builds inputs from the database,
    so what it scores with reaches nothing but plain Python (D-261)."""
    for path in VERDICT_SCORING:
        assert list(imported_modules(path))
        outside = [
            module
            for module in outside_the_standard_library(path)
            if module not in VERDICT_MAY_IMPORT
        ]
        assert outside == [], path.name


def test_reading_a_model_back_never_imports_the_fitter() -> None:
    """``meridian model show`` and the scheduler must run without the extra."""
    assert crossings(list(MODEL_READERS), ("meridian.prediction.fit",)) == []


def test_the_standard_library_scan_would_notice_an_import(tmp_path: Path) -> None:
    offender = tmp_path / "offender.py"
    offender.write_text(
        "import json\nfrom meridian.prediction.fit import fit_model\n",
        encoding="utf-8",
    )

    assert outside_the_standard_library(offender) == ["meridian.prediction.fit"]
    assert len(crossings([offender], ("meridian.prediction.fit",))) == 1


PLATFORM = REPO_ROOT / "platform" / "src" / "meridian"
COMMANDS = (
    PLATFORM / "cli.py",
    PLATFORM / "cli_model.py",
    PLATFORM / "cli_verdict.py",
    PLATFORM / "cli_verdict_model.py",
)
NEEDS_THE_EXTRA = (
    "meridian.prediction.fit",
    "meridian.prediction.evaluation",
    "meridian.prediction.calibration_report",
)
"""The fitter, and the two modules that reach it: refitting folds, and the
report over them."""


def top_level_imports(path: Path) -> list[str]:
    """What a module imports as it loads: its body, not its functions."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append(node.module)
    return found


def test_the_command_imports_the_fitter_only_when_it_fits() -> None:
    """``meridian`` must start in the image, where the extra is not (D-155).

    ``fit`` and ``evaluate`` import the fitter inside themselves, so only they
    need it — and they are seen doing so, or this would prove nothing.
    """
    at_load = [
        f"{path.name} imports {module}"
        for path in COMMANDS
        for module in top_level_imports(path)
        if reaches(module, NEEDS_THE_EXTRA)
    ]

    assert at_load == []
    assert crossings([PLATFORM / "cli_model.py"], NEEDS_THE_EXTRA)
    assert crossings([PLATFORM / "cli_verdict_model.py"], NEEDS_THE_EXTRA)


def test_the_load_time_scan_would_notice_an_import(tmp_path: Path) -> None:
    offender = tmp_path / "offender.py"
    offender.write_text(
        "from meridian.prediction.fit import fit_model\n"
        "def later():\n"
        "    from meridian.prediction.evaluation import evaluate_model\n",
        encoding="utf-8",
    )

    assert top_level_imports(offender) == ["meridian.prediction.fit"]


VERDICT_ONLY = (
    "meridian.prediction.verdict_inputs",
    "meridian.prediction.verdict_score",
    "meridian.prediction.verdict_rows",
    "meridian.prediction.verdict_examples",
    "meridian.prediction.verdict_files",
    "meridian.prediction.verdict_evaluation",
    "meridian.prediction.verdict_report",
    "meridian.prediction.verdict_config",
    "meridian.datasets.usable_labels",
    "meridian.verdict_build",
    "meridian.store.verdicts",
    "meridian.store.ratings",
)
"""What a reception's verdict and its label are made of (Stage 26)."""

VERDICT_TABLES = ("reception_verdicts", "reception_ratings")

MAY_READ_VERDICTS = frozenset({"fit.py"})
"""The fitter fits both kinds of model; nothing it fits a yield model with
reads a verdict, which the yield path's own modules below show."""


def yield_path() -> list[Path]:
    """Every module a pass's yield prediction is made by: prediction's own,
    except the verdict's, and the scheduler that scores with it."""
    return [
        path
        for path in sorted(
            [*PREDICTION.rglob("*.py"), *(PLATFORM / "scheduler").rglob("*.py")]
        )
        if not path.name.startswith("verdict_") and path.name not in MAY_READ_VERDICTS
    ]


def reads_a_verdict(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return [
        *crossings([path], VERDICT_ONLY),
        *(f"{path.name} names {table}" for table in VERDICT_TABLES if table in text),
    ]


def test_no_pass_s_verdict_is_a_yield_feature() -> None:
    """D-102: a pass's own verdict never feeds the prediction of its yield.

    The verdict reads what a pass returned; the yield model predicts it before
    the pass. A verdict among the yield features would let the outcome leak
    into its own forecast, and SC-2 would measure the leak.
    """
    paths = yield_path()

    assert len(paths) > 10
    assert [line for path in paths for line in reads_a_verdict(path)] == []


def test_the_verdict_scan_would_notice_a_reader(tmp_path: Path) -> None:
    offender = tmp_path / "offender.py"
    offender.write_text(
        "from meridian.prediction.verdict_score import score_reception\n"
        'QUERY = "select probability_usable from reception_verdicts"\n',
        encoding="utf-8",
    )

    assert len(reads_a_verdict(offender)) == 2
