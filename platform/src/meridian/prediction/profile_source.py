"""The newest labelled dataset, read for the profile build.

The platform's runtime code reaches datasets only through ``meridian.prediction``
(``test_datasets_boundaries``), as the scheduler does through
:mod:`meridian.prediction.live`. This is the same door for
``meridian.profile_build``. It finds the newest dataset from manifests alone,
names it by its manifest's hash, and computes its stations' profiles with the
functions the features use (D-174).

Every way a dataset can fail to be read is one error,
:class:`~meridian.prediction.live.LiveScoringError`, as it is for live scoring:
a round refuses on the prediction layer's own error, not the dataset layer's.

Reference: docs/DECISIONS.md D-155, D-169, D-174.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from meridian.datasets.label_rows import read_labels
from meridian.datasets.manifest import content_sha256, parse_manifest
from meridian.datasets.manifest_parse import MalformedManifestError, whole
from meridian.datasets.publish import (
    MANIFEST_NAME,
    DamagedSnapshotError,
    read_directory,
)
from meridian.datasets.row_fields import MalformedSnapshotError
from meridian.prediction.feature_rows import read_feature_rows
from meridian.prediction.lineage import LineageError, raw_of
from meridian.prediction.live import LiveScoringError, newest_dataset_path
from meridian.prediction.profile_cells import StationProfiles, station_profiles

__all__ = ["DatasetProfiles", "NewestDataset", "newest_dataset", "read_profiles"]


@dataclass(frozen=True, slots=True)
class NewestDataset:
    """Where the newest labelled dataset is, and what names it."""

    path: Path
    sha256: bytes
    as_of: datetime


@dataclass(frozen=True, slots=True)
class DatasetProfiles:
    """Every station's profiles from one dataset, and the span they cover."""

    since: datetime
    """The raw snapshot's ``since``: the earliest pass the dataset could hold."""
    as_of: datetime
    profiles: tuple[StationProfiles, ...]


def newest_dataset(root: Path) -> NewestDataset | None:
    """The newest labelled dataset, read from its manifest alone.

    Raises:
        LiveScoringError: A manifest cannot be read.
    """
    path = newest_dataset_path(root)
    if path is None:
        return None
    try:
        manifest = parse_manifest((path / MANIFEST_NAME).read_bytes())
    except (MalformedManifestError, OSError) as exc:
        message = f"the manifest of {path} cannot be read: {exc}"
        raise LiveScoringError(message) from exc
    return NewestDataset(
        path=path, sha256=content_sha256(manifest), as_of=manifest.as_of
    )


def read_profiles(dataset: NewestDataset, *, root: Path) -> DatasetProfiles:
    """Verify the dataset and its raw snapshot, and compute every station's profile.

    Raises:
        LiveScoringError: Either does not match its manifest, the raw snapshot
            is not where the dataset says, or a row cannot be read. An older
            dataset is not read instead.
    """
    try:
        labelled = read_directory(dataset.path)
        raw = raw_of(labelled, root=root)
        settle = whole(labelled.manifest.parameters.get("settle_margin_s"), "settle")
        profiles = station_profiles(
            read_labels(labelled.files),
            read_feature_rows(raw.files),
            settle_margin_s=settle,
            as_of=labelled.manifest.as_of,
        )
    except (
        DamagedSnapshotError,
        LineageError,
        MalformedManifestError,
        MalformedSnapshotError,
    ) as exc:
        message = f"the dataset at {dataset.path} cannot be profiled: {exc}"
        raise LiveScoringError(message) from exc
    return DatasetProfiles(
        since=raw.manifest.since, as_of=labelled.manifest.as_of, profiles=profiles
    )
