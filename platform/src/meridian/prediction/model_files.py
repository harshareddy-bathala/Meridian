"""A fitted model as a published directory, written once and read back whole.

A model is published by the rules a snapshot is (D-144, D-163): written into a
scratch directory, synced, sealed read-only and renamed into place under the
hash of its manifest, so the same fit twice names the same directory and a
changed byte is refused when it is read.

**The manifest names what the model was made from:** the evaluation dataset's
hash as ``derived_from``, the model configuration's hash, and every setting as
``parameters``. ``model.json`` repeats both hashes, so a model file copied
away from its directory still says which data and which settings made it.

Deliberately apart from :mod:`meridian.prediction.fit`: reading a model back,
as ``meridian model show`` and the scheduler do, must not import scikit-learn.

Reference: docs/DECISIONS.md D-144, D-163.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from meridian.datasets.canonical import canonical_bytes
from meridian.datasets.manifest import Manifest, content_sha256, file_entry
from meridian.datasets.publish import (
    PublishedDirectory,
    SnapshotDirectory,
    publish_directory,
    read_directory,
)
from meridian.prediction.model_config import ModelConfig, model_config_sha256
from meridian.prediction.score import MalformedModelError, Model, parse_model

__all__ = [
    "MODELS",
    "MODEL_FILE",
    "MODEL_VERSION",
    "Fitted",
    "FittedDirectory",
    "publish_model",
    "read_model",
]

MODELS = "models"
"""Models live under ``<datasets root>/models/``."""

MODEL_FILE = "model.json"
MODEL_VERSION = "model-1"
"""The fitting procedure's version, bumped when the same inputs would give a
different model: the manifest's ``transformation_version``."""


class Fitted(Protocol):
    """What a fit hands over to be published; ``fit.FittedModel`` is one."""

    @property
    def document(self) -> Mapping[str, object]:
        """The ``model.json`` document."""
        ...

    @property
    def counts(self) -> Mapping[str, int]:
        """Its counts, for the manifest."""
        ...


@dataclass(frozen=True, slots=True)
class FittedDirectory:
    """A verified model directory and the model it holds."""

    directory: SnapshotDirectory
    model: Model


def publish_model(
    fitted: Fitted,
    *,
    dataset: SnapshotDirectory,
    config: ModelConfig,
    root: Path,
    created_at: datetime,
) -> PublishedDirectory:
    """Publish a fitted model under ``root/models``.

    Args:
        fitted: What :func:`~meridian.prediction.fit.fit_model` produced.
        dataset: The evaluation dataset it was fitted on.
        config: The model configuration it was fitted under.
        root: The datasets root.
        created_at: When this run happened, recorded and never hashed.

    Returns:
        Where the model landed; ``written`` is False when the same fit was
        already there.
    """
    dataset_sha256 = content_sha256(dataset.manifest)
    config_sha256 = model_config_sha256(config)
    data = (
        canonical_bytes(
            dict(fitted.document)
            | {"dataset_sha256": dataset_sha256, "config_sha256": config_sha256}
        )
        + b"\n"
    )
    manifest = Manifest(
        kind="model",
        schema_revision=dataset.manifest.schema_revision,
        since=dataset.manifest.since,
        as_of=dataset.manifest.as_of,
        files=(file_entry(MODEL_FILE, data),),
        created_at=created_at,
        counts=dict(fitted.counts),
        derived_from=dataset_sha256,
        transformation_version=MODEL_VERSION,
        config_sha256=config_sha256,
        parameters=config.parameters(),
    )
    name = content_sha256(manifest).hex()[:12]
    return publish_directory(root / MODELS, name, manifest, {MODEL_FILE: data})


def read_model(path: Path) -> FittedDirectory:
    """Read a model directory back, verified, and parse its model.

    Raises:
        DamagedSnapshotError: The directory is not what its manifest says.
        MalformedModelError: It is not a model, or its model cannot be scored.
    """
    directory = read_directory(path)
    if directory.manifest.kind != "model":
        message = f"{path} is a {directory.manifest.kind}, not a model"
        raise MalformedModelError(message)
    return FittedDirectory(directory, parse_model(directory.files[MODEL_FILE]))
