"""``meridian verdict fit`` and ``meridian verdict evaluate`` — files, no database.

``fit`` reads a raw snapshot, joins every measured reception to its label
(D-260), fits one calibrated model per route (D-261) and publishes it under
``<datasets root>/verdicts/``. ``evaluate`` reads a verdict model back, finds
the snapshot it was fitted from, and prints SC-7's figures for the test span
(D-262).

**Only ``fit`` needs the ``fit`` extra**, and it imports the fitter inside
itself, as ``meridian model fit`` does (D-155). ``evaluate`` scores with the
standard library, so it runs in the image.

Reference: docs/DECISIONS.md D-155, D-260, D-261, D-262.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from meridian.cli_model import FIT_EXTRA, NEEDS_EXTRA
from meridian.cli_snapshot import (
    DATASETS_ROOT_ENV,
    DEFAULT_DATASETS_ROOT,
    EXIT_CORRUPT,
    datasets_root,
    report_published,
)
from meridian.datasets.manifest import MalformedManifestError
from meridian.datasets.publish import (
    DamagedSnapshotError,
    SnapshotDirectory,
    read_directory,
)
from meridian.datasets.row_fields import MalformedSnapshotError
from meridian.datasets.usable_labels import read_usable_labels
from meridian.prediction.lineage import LineageError, snapshot_of
from meridian.prediction.score import MalformedModelError
from meridian.prediction.splits import SplitError
from meridian.prediction.verdict_config import VerdictConfigError, load_verdict_config
from meridian.prediction.verdict_evaluation import (
    VerdictEvaluationError,
    evaluate_verdict,
)
from meridian.prediction.verdict_examples import (
    VerdictExamples,
    build_verdict_examples,
)
from meridian.prediction.verdict_files import (
    VERDICT_FILE,
    publish_verdict,
    read_verdict,
)
from meridian.prediction.verdict_report import verdict_lines
from meridian.prediction.verdict_rows import read_receptions

__all__ = ["MODEL_ACTIONS", "add_model_actions", "run_model_action"]

EXIT_FAILED = 1

MODEL_ACTIONS = ("fit", "evaluate")

_REFUSED = (
    LineageError,
    MalformedManifestError,
    MalformedModelError,
    MalformedSnapshotError,
    SplitError,
    VerdictConfigError,
    VerdictEvaluationError,
    OSError,
)
"""What both verbs refuse with exit 1; a damaged directory is exit 3."""


def add_model_actions(
    actions: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Wire ``fit`` and ``evaluate`` under ``meridian verdict``."""
    root_help = (
        f"datasets root (default: ${DATASETS_ROOT_ENV}, else {DEFAULT_DATASETS_ROOT})"
    )
    fit = actions.add_parser("fit", help="fit and publish a verdict model")
    fit.add_argument("snapshot", type=Path, help="a raw snapshot directory")
    fit.add_argument(
        "--config",
        type=Path,
        required=True,
        help="verdict settings; see deploy/verdict.toml.example",
    )
    fit.add_argument("--root", type=Path, default=None, help=root_help)
    evaluate = actions.add_parser(
        "evaluate", help="print a verdict model's SC-7 calibration report"
    )
    evaluate.add_argument("verdict", type=Path, help="a verdict model directory")
    evaluate.add_argument(
        "--snapshot", type=Path, default=None, help="its raw snapshot, if moved"
    )
    evaluate.add_argument("--root", type=Path, default=None, help=root_help)


def run_model_action(args: argparse.Namespace) -> int:
    """Run ``meridian verdict fit`` or ``meridian verdict evaluate``."""
    action = args.action
    try:
        return _fit(args) if action == "fit" else _evaluate(args)
    except ModuleNotFoundError as exc:
        if (exc.name or "").split(".")[0] not in FIT_EXTRA:
            raise
        return _refuse(action, NEEDS_EXTRA)
    except DamagedSnapshotError as exc:
        _refuse(action, str(exc))
        return EXIT_CORRUPT
    except _REFUSED as exc:
        return _refuse(action, str(exc))


def examples_from(snapshot: SnapshotDirectory, *, rubric: str) -> VerdictExamples:
    """Every measured, labelled reception in a raw snapshot."""
    if snapshot.manifest.kind != "raw_snapshot":
        message = f"{snapshot.path} is a {snapshot.manifest.kind}, not a raw snapshot"
        raise LineageError(message)
    return build_verdict_examples(
        read_receptions(snapshot.files),
        read_usable_labels(snapshot.files),
        rubric=rubric,
    )


def _fit(args: argparse.Namespace) -> int:
    """``meridian verdict fit``."""
    from meridian.prediction.fit import ModelFitError, fit_verdict  # noqa: PLC0415

    root = datasets_root(args.root)
    config = load_verdict_config(args.config)
    snapshot = read_directory(args.snapshot)
    found = examples_from(snapshot, rubric=config.rubric)
    try:
        fitted = fit_verdict(found, config, as_of=snapshot.manifest.as_of)
    except ModelFitError as exc:
        return _refuse("fit", str(exc))
    published = publish_verdict(
        fitted,
        snapshot=snapshot,
        config=config,
        root=root,
        created_at=datetime.now(UTC),
    )
    report_published("verdict model", published)
    _say(f"  method             {fitted.document['method']}")
    _say(f"  partial below      {config.partial_below} · rubric {config.rubric}")
    return 0


def _evaluate(args: argparse.Namespace) -> int:
    """``meridian verdict evaluate``."""
    root = datasets_root(args.root)
    held = read_verdict(args.verdict)
    snapshot = snapshot_of(held.directory, root=root, path=args.snapshot)
    found = examples_from(snapshot, rubric=held.model.rubric)
    document = json.loads(held.directory.files[VERDICT_FILE])
    evaluation = evaluate_verdict(
        held.model,
        found,
        train_until=_instant(document.get("train_until"), "train_until"),
        validate_until=_instant(document.get("validate_until"), "validate_until"),
        as_of=snapshot.manifest.as_of,
    )
    for line in verdict_lines(evaluation, found, method=held.model.method):
        _say(line)
    return 0


def _instant(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        message = f"the verdict model's {name} is {value!r}, not a date-time"
        raise MalformedModelError(message)
    return datetime.fromisoformat(value)


def _say(line: str) -> None:
    print(line)  # noqa: T201 — this is a CLI; stdout is the interface


def _refuse(action: str, reason: str) -> int:
    print(f"meridian verdict {action}: {reason}", file=sys.stderr)  # noqa: T201
    return EXIT_FAILED
