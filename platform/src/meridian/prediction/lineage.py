"""From a published directory to what it was made from, and to its examples.

A model names the evaluation dataset it was fitted on, and a dataset names the
raw snapshot it was labelled from, each by the hash of its manifest (D-144,
D-163). This module follows those names, and refuses a directory whose hash
is not the one named: a model evaluated against a dataset it was not fitted
on would give figures that look like any others.

By default a parent is looked for where publishing put it, under the datasets
root — ``snapshots/<as_of>-<hash[:12]>``, since a dataset keeps its raw
snapshot's ``as_of``, or ``evaluation/<hash[:12]>`` — and a path can be given
instead for one copied elsewhere. Either way it is verified.

**Examples are rebuilt, never stored.** They are a pure function of the raw
snapshot, the labels and the labelling configuration the dataset recorded
(D-157), so ``fit`` and ``evaluate`` rebuild the same examples from the same
directories.

Reference: docs/DECISIONS.md D-144, D-156, D-157, D-163.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path

from meridian.datasets.evaluation import EVALUATION, PROPENSITIES
from meridian.datasets.label_rows import read_labels
from meridian.datasets.manifest import content_sha256
from meridian.datasets.manifest_parse import whole
from meridian.datasets.publish import SnapshotDirectory, read_directory
from meridian.datasets.selection_config import parse_completeness
from meridian.datasets.snapshot_rows import parse_rows
from meridian.prediction.examples import (
    ExampleSet,
    archive_examples,
    own_examples,
    weighted,
)
from meridian.prediction.feature_rows import read_feature_rows
from meridian.prediction.model_config import (
    ModelConfig,
    config_from_parameters,
    model_config_sha256,
)

__all__ = [
    "Inputs",
    "LineageError",
    "config_of",
    "dataset_of",
    "examples_of",
    "raw_of",
]

_SNAPSHOTS = "snapshots"
"""Where ``meridian.datasets.export`` publishes; repeated rather than imported,
since the export side reaches the database and prediction may not."""


class LineageError(ValueError):
    """A directory that is not what the one made from it names."""


@dataclass(frozen=True, slots=True)
class Inputs:
    """A dataset's examples, and each satellite's band for the segments."""

    examples: ExampleSet
    bands: Mapping[str, str]


def raw_of(
    dataset: SnapshotDirectory, *, root: Path, path: Path | None = None
) -> SnapshotDirectory:
    """The raw snapshot an evaluation dataset was labelled from, verified.

    Raises:
        LineageError: Not an evaluation dataset, the snapshot is not where it
            is looked for, or it is another snapshot.
        DamagedSnapshotError: It is there and does not match its manifest.
    """
    if dataset.manifest.kind != "evaluation_dataset":
        message = f"{dataset.path} is a {dataset.manifest.kind}, not a dataset"
        raise LineageError(message)
    stamp = dataset.manifest.as_of.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return _parent(
        dataset,
        root / _SNAPSHOTS,
        f"{stamp}-",
        path,
        ("raw_snapshot", "--snapshot"),
    )


def dataset_of(
    model: SnapshotDirectory, *, root: Path, path: Path | None = None
) -> SnapshotDirectory:
    """The evaluation dataset a model was fitted on, verified.

    Raises:
        LineageError: As :func:`raw_of`.
        DamagedSnapshotError: As :func:`raw_of`.
    """
    return _parent(
        model, root / EVALUATION, "", path, ("evaluation_dataset", "--dataset")
    )


def config_of(model: SnapshotDirectory) -> ModelConfig:
    """The configuration a model was fitted under, as its manifest names it.

    Raises:
        LineageError: The recorded settings do not hash to the recorded hash.
        ModelConfigError: A recorded setting is refused.
    """
    config = config_from_parameters(model.manifest.parameters)
    if model_config_sha256(config) != model.manifest.config_sha256:
        message = (
            f"{model.path} records settings that do not hash to its"
            " config_sha256; it was not published by `meridian model fit`"
        )
        raise LineageError(message)
    return config


def examples_of(
    dataset: SnapshotDirectory, raw: SnapshotDirectory, config: ModelConfig
) -> Inputs:
    """The configuration's population's examples, weighted if it says so.

    Args:
        dataset: The evaluation dataset.
        raw: The raw snapshot it was labelled from.
        config: The model configuration.

    Returns:
        The examples and the bands.
    """
    parameters = dataset.manifest.parameters
    rows = read_feature_rows(raw.files)
    if config.population == "own":
        found = own_examples(
            read_labels(dataset.files),
            rows,
            settle_margin_s=whole(parameters.get("settle_margin_s"), "settle_margin_s"),
        )
    else:
        tolerance = parse_completeness(
            parameters.get("completeness", {})
        ).archive_match_tolerance_s
        found = archive_examples(
            parse_rows(raw.files), tolerance_s=tolerance, since=dataset.manifest.since
        )
    if config.weighting == "ipw":
        found = weighted(found, dataset.files[PROPENSITIES])
    return Inputs(examples=found, bands=rows.bands)


def _parent(
    child: SnapshotDirectory,
    under: Path,
    prefix: str,
    path: Path | None,
    wanted: tuple[str, str],
) -> SnapshotDirectory:
    """The directory ``child`` names, at ``path`` or where publishing put it."""
    kind, option = wanted
    named = child.manifest.derived_from
    if named is None:
        message = f"{child.path} names nothing it was made from"
        raise LineageError(message)
    where = path if path is not None else under / f"{prefix}{named.hex()[:12]}"
    if not where.is_dir():
        message = (
            f"{child.path} was made from {named.hex()}, which is not at {where};"
            f" name its directory with {option}"
        )
        raise LineageError(message)
    parent = read_directory(where)
    held = content_sha256(parent.manifest)
    if parent.manifest.kind != kind or held != named:
        message = (
            f"{where} is a {parent.manifest.kind} with hash {held.hex()}, and"
            f" {child.path} was made from the {kind} {named.hex()}"
        )
        raise LineageError(message)
    return parent
