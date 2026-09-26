"""``meridian model`` — fit a model on a dataset, judge it, and show it.

Three verbs, none of which opens a database:

* ``fit <dataset> [--config …]`` rebuilds the dataset's examples from it and
  the raw snapshot it names, fits the configuration's model on the stated
  dates and publishes it under ``<root>/models``. The same dataset and the
  same configuration give the same directory, so reproducibility is shown by
  running it twice and reading the name (D-163). With too few examples — as
  on every dataset before a station has reported — it refuses with the counts.
* ``evaluate <model>`` follows the model to its dataset and its raw snapshot,
  each verified by hash, and prints the calibration report of D-164 with the
  dataset's completeness beside it. The configuration is the one the model's
  manifest records, so there is no ``--config`` to get wrong.
* ``show <model>`` prints what the model is: its inputs, coefficients,
  calibration map and dates.

**The fitter is imported inside ``fit`` and ``evaluate``, never at the top.**
scikit-learn is the ``fit`` extra, which the platform image does not install
(D-155), and ``meridian`` there must still start — ``show`` included. Without
the extra those two verbs say what to install instead of raising.

Reference: docs/DECISIONS.md D-155, D-162, D-163, D-164.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from meridian.cli_snapshot import (
    DATASETS_ROOT_ENV,
    DEFAULT_DATASETS_ROOT,
    EXIT_CORRUPT,
    datasets_root,
    report_published,
)
from meridian.datasets.label_config import LabelConfigError
from meridian.datasets.manifest import MalformedManifestError, content_sha256
from meridian.datasets.publish import DamagedSnapshotError, read_directory
from meridian.datasets.result_reader import NoSelectionError, read_results
from meridian.datasets.row_fields import MalformedSnapshotError
from meridian.prediction.lineage import (
    LineageError,
    config_of,
    dataset_of,
    examples_of,
    raw_of,
)
from meridian.prediction.model_config import ModelConfigError, load_model_config
from meridian.prediction.model_files import MODEL_FILE, publish_model, read_model
from meridian.prediction.score import Linear, MalformedModelError
from meridian.prediction.splits import SplitError

if TYPE_CHECKING:
    from meridian.prediction.model_files import FittedDirectory

__all__ = ["FIT_EXTRA", "add_model_parser", "run_model", "show_lines"]

EXIT_FAILED = 1
"""Matches ``meridian.cli.EXIT_FAILED``."""

FIT_EXTRA = ("sklearn", "scipy", "joblib")
"""A missing import from these means the ``fit`` extra is not installed."""

_NEEDS_EXTRA = (
    "needs the fit extra, which this installation does not have"
    " (uv sync --extra fit, or pip install 'meridian[fit]'); scoring and"
    " `meridian model show` do not (D-155)"
)

_REFUSED = (
    LabelConfigError,
    LineageError,
    MalformedManifestError,
    MalformedModelError,
    MalformedSnapshotError,
    ModelConfigError,
    NoSelectionError,
    SplitError,
    OSError,
)
"""What every verb refuses with exit 1; a damaged directory is exit 3."""


def add_model_parser(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Wire ``meridian model`` and its three actions."""
    model = subcommands.add_parser(
        "model",
        help="fit, evaluate and show prediction models",
        description=(
            "A model is fitted on an evaluation dataset with the dates a"
            " configuration states, published by hash, and judged on the span"
            " after them (D-162, D-163)."
        ),
    )
    model.add_argument(
        "--root",
        type=Path,
        default=None,
        help=(
            f"datasets root (default: ${DATASETS_ROOT_ENV}, "
            f"else {DEFAULT_DATASETS_ROOT})"
        ),
    )
    actions = model.add_subparsers(dest="action", metavar="<action>")
    fit = actions.add_parser("fit", help="fit and publish a model")
    fit.add_argument("dataset", type=Path, help="an evaluation dataset")
    fit.add_argument(
        "--config",
        type=Path,
        default=None,
        help="model settings; see deploy/model.toml.example",
    )
    fit.add_argument(
        "--snapshot", type=Path, default=None, help="the raw snapshot, if moved"
    )
    evaluate = actions.add_parser("evaluate", help="print a model's calibration report")
    evaluate.add_argument("model", type=Path, help="a model directory")
    evaluate.add_argument(
        "--dataset", type=Path, default=None, help="its dataset, if moved"
    )
    evaluate.add_argument(
        "--snapshot", type=Path, default=None, help="the raw snapshot, if moved"
    )
    show = actions.add_parser("show", help="print what a model is")
    show.add_argument("model", type=Path, help="a model directory")


def run_model(args: argparse.Namespace) -> int:
    """Run one ``meridian model`` action."""
    action = args.action
    try:
        return _dispatch(action, args)
    except ModuleNotFoundError as exc:
        if (exc.name or "").split(".")[0] not in FIT_EXTRA:
            raise
        return _refuse(action, _NEEDS_EXTRA)
    except DamagedSnapshotError as exc:
        _refuse(action, str(exc))
        return EXIT_CORRUPT
    except _REFUSED as exc:
        return _refuse(action, str(exc))


def _dispatch(action: str, args: argparse.Namespace) -> int:
    """The action, once what it reads is known to be a directory."""
    actions = {"fit": _fit, "evaluate": _evaluate, "show": _show}
    target = args.dataset if action == "fit" else args.model
    if not Path(target).is_dir():
        return _refuse(action, f"{target} is not a directory")
    return actions[action](args)


def _fit(args: argparse.Namespace) -> int:
    """``meridian model fit``."""
    from meridian.prediction.fit import ModelFitError, fit_model  # noqa: PLC0415

    root = datasets_root(args.root)
    config = load_model_config(args.config)
    dataset = read_directory(args.dataset)
    raw = raw_of(dataset, root=root, path=args.snapshot)
    inputs = examples_of(dataset, raw, config)
    try:
        fitted = fit_model(inputs.examples, config, as_of=dataset.manifest.as_of)
    except ModelFitError as exc:
        return _refuse("fit", str(exc))
    published = publish_model(
        fitted, dataset=dataset, config=config, root=root, created_at=datetime.now(UTC)
    )
    report_published("model", published)
    _say(
        f"  configuration      {config.configuration} on {config.population},"
        f" seed {config.seed}"
    )
    _say(
        f"  split              train until {fitted.split.train_until.isoformat()},"
        f" validate until {fitted.split.validate_until.isoformat()}"
    )
    return 0


def _evaluate(args: argparse.Namespace) -> int:
    """``meridian model evaluate``."""
    from meridian.prediction.calibration_report import (  # noqa: PLC0415
        Provenance,
        evaluation_lines,
    )
    from meridian.prediction.evaluation import (  # noqa: PLC0415
        EvaluationError,
        evaluate_model,
    )

    root = datasets_root(args.root)
    held = read_model(args.model)
    config = config_of(held.directory)
    dataset = dataset_of(held.directory, root=root, path=args.dataset)
    raw = raw_of(dataset, root=root, path=args.snapshot)
    inputs = examples_of(dataset, raw, config)
    try:
        evaluation = evaluate_model(
            held.model,
            inputs.examples,
            config,
            as_of=dataset.manifest.as_of,
            bands=inputs.bands,
        )
    except EvaluationError as exc:
        return _refuse("evaluate", str(exc))
    result = next(
        one for one in read_results(dataset) if one.population == config.population
    )
    provenance = Provenance(
        model_sha256=content_sha256(held.directory.manifest).hex(),
        dataset_sha256=content_sha256(dataset.manifest).hex(),
        config_sha256=_hex(held.directory.manifest.config_sha256),
    )
    for line in evaluation_lines(evaluation, config, provenance, result):
        _say(line)
    return 0


def _show(args: argparse.Namespace) -> int:
    """``meridian model show``: reads the file, and never imports the fitter."""
    held = read_model(args.model)
    for line in show_lines(held):
        _say(line)
    return 0


def show_lines(held: FittedDirectory) -> list[str]:
    """What a model is, from its verified directory."""
    document = json.loads(held.directory.files[MODEL_FILE])
    libraries = document.get("libraries", {})
    model = held.model
    history = (
        f"yes; below {model.min_station_history} settled outcomes a station"
        " takes the geometry-only model (D-161)"
        if model.reads_history
        else "no"
    )
    lines = [
        f"model {held.directory.path}",
        f"  hash               {content_sha256(held.directory.manifest).hex()}",
        f"  configuration      {model.configuration} on {document.get('population')}",
        f"  reads history      {history}",
        f"  train until        {document.get('train_until')}",
        f"  validate until     {document.get('validate_until')}",
        f"  as_of              {document.get('as_of')}",
        f"  seed               {document.get('seed')},"
        f" C = {document.get('inverse_regularisation')},"
        f" weighting {document.get('weighting')}",
        f"  fitted with        numpy {libraries.get('numpy')},"
        f" scikit-learn {libraries.get('scikit-learn')}",
        f"  dataset            {document.get('dataset_sha256')}",
        f"  configuration file {document.get('config_sha256')}",
        *_linear_lines("configured", model.configured),
    ]
    if model.fallback is None:
        lines.append("fallback: none; this configuration reads no history")
    else:
        lines.extend(_linear_lines("geometry fallback", model.fallback))
    return lines


def _linear_lines(title: str, linear: Linear) -> list[str]:
    lines = [
        f"{title} model, {len(linear.features)} features (standardised inputs)",
        f"  intercept          {linear.intercept:+.6g}",
        f"  calibration        sigmoid({linear.calibration_a:.6g} × logit"
        f" {linear.calibration_b:+.6g})",
        f"  {'feature':<28} {'mean':>12} {'scale':>12} {'coefficient':>12}",
    ]
    lines.extend(
        f"  {name:<28} {mean:>12.6g} {scale:>12.6g} {weight:>+12.6g}"
        for name, mean, scale, weight in zip(
            linear.features,
            linear.mean,
            linear.scale,
            linear.coefficients,
            strict=True,
        )
    )
    return lines


def _hex(value: bytes | None) -> str:
    return "—" if value is None else value.hex()


def _say(line: str) -> None:
    print(line)  # noqa: T201 — this is a CLI; stdout is the interface


def _refuse(action: str, reason: str) -> int:
    print(  # noqa: T201 — this is a CLI; stderr is the interface
        f"meridian model {action}: {reason}", file=sys.stderr
    )
    return EXIT_FAILED
