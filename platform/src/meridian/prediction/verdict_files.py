"""Publishing a fitted verdict, and reading one back without the fitter.

A verdict model is a directory under ``<datasets root>/verdicts/``, as a yield
model is under ``models/`` (D-163). It has a manifest of kind
``verdict_model`` and one ``verdict.json``. It is made straight from a raw
snapshot, which holds the ratings its labels come from (D-260), so its
``derived_from`` is that snapshot's hash. The directory is named by the
manifest's hash, so the same fit lands in the same place.

**Reading one back never imports scikit-learn.** The writer of verdicts runs
in the image without the ``fit`` extra (D-155), and
``tests/unit/test_prediction_boundaries.py`` holds this module to that.

Reference: docs/DECISIONS.md D-155, D-163, D-260, D-262.
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
from meridian.prediction.score import MalformedModelError
from meridian.prediction.verdict_config import VerdictConfig, verdict_config_sha256
from meridian.prediction.verdict_score import VerdictModel, parse_verdict_model

__all__ = [
    "VERDICTS",
    "VERDICT_FILE",
    "VERDICT_VERSION",
    "FittedVerdictLike",
    "VerdictDirectory",
    "publish_verdict",
    "read_verdict",
]

VERDICTS = "verdicts"
"""Verdict models live under ``<datasets root>/verdicts/``."""

VERDICT_FILE = "verdict.json"
VERDICT_VERSION = "verdict-1"
"""The fitting procedure's version: the manifest's ``transformation_version``."""


class FittedVerdictLike(Protocol):
    """What a verdict fit hands over; ``fit.FittedVerdict`` is one."""

    @property
    def document(self) -> Mapping[str, object]:
        """The ``verdict.json`` document."""
        ...

    @property
    def counts(self) -> Mapping[str, int]:
        """Its counts, for the manifest."""
        ...


@dataclass(frozen=True, slots=True)
class VerdictDirectory:
    """A verified verdict directory and the model it holds."""

    directory: SnapshotDirectory
    model: VerdictModel


def publish_verdict(
    fitted: FittedVerdictLike,
    *,
    snapshot: SnapshotDirectory,
    config: VerdictConfig,
    root: Path,
    created_at: datetime,
) -> PublishedDirectory:
    """Publish a fitted verdict under ``root/verdicts``.

    Args:
        fitted: What :func:`~meridian.prediction.fit.fit_verdict` produced.
        snapshot: The raw snapshot it was fitted from.
        config: The verdict configuration it was fitted under.
        root: The datasets root.
        created_at: When this run happened, recorded and never hashed.

    Returns:
        Where it landed; ``written`` is False when the same fit was there.
    """
    snapshot_sha256 = content_sha256(snapshot.manifest)
    config_sha256 = verdict_config_sha256(config)
    data = (
        canonical_bytes(
            dict(fitted.document)
            | {"snapshot_sha256": snapshot_sha256, "config_sha256": config_sha256}
        )
        + b"\n"
    )
    manifest = Manifest(
        kind="verdict_model",
        schema_revision=snapshot.manifest.schema_revision,
        since=snapshot.manifest.since,
        as_of=snapshot.manifest.as_of,
        files=(file_entry(VERDICT_FILE, data),),
        created_at=created_at,
        counts=dict(fitted.counts),
        sources=snapshot.manifest.sources,
        derived_from=snapshot_sha256,
        transformation_version=VERDICT_VERSION,
        config_sha256=config_sha256,
        parameters=config.parameters(),
    )
    name = content_sha256(manifest).hex()[:12]
    return publish_directory(root / VERDICTS, name, manifest, {VERDICT_FILE: data})


def read_verdict(path: Path) -> VerdictDirectory:
    """Read a verdict directory back, verified, and parse its model.

    Raises:
        DamagedSnapshotError: The directory is not what its manifest says.
        MalformedModelError: It is not a verdict model, or cannot be scored.
    """
    directory = read_directory(path)
    if directory.manifest.kind != "verdict_model":
        message = f"{path} is a {directory.manifest.kind}, not a verdict model"
        raise MalformedModelError(message)
    return VerdictDirectory(
        directory, parse_verdict_model(directory.files[VERDICT_FILE])
    )
