"""``meridian snapshot label`` — a raw snapshot and a configuration, made a dataset.

Reads a raw snapshot only through :func:`~meridian.datasets.publish.read_directory`,
so an edited snapshot is refused rather than labelled; labels every pass
(D-146, D-147); and publishes two files beside a manifest naming exactly what
they were made from — the raw snapshot's hash, the labelling rules' version and
the configuration's hash (D-144).

* ``labels.jsonl`` — one row per geometrically available pass.
* ``archive_receptions.jsonl`` — the archive's receptions in their own
  vocabulary, each beside the licence and terms it arrived under. They never
  receive a Meridian label: we hold no heartbeat for somebody else's station
  (D-139).

**Nothing here reads a clock.** ``created_at`` is handed in, and it is the one
field the hash leaves out, so the same raw snapshot and the same configuration
always give the same directory name. That is Stage 15's gate.

Reference: docs/DECISIONS.md D-139, D-143, D-144, D-146, D-147.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from meridian.datasets.canonical import canonical_line
from meridian.datasets.label_config import LabelConfig, config_sha256
from meridian.datasets.labels import TRANSFORMATION_VERSION, label_counts, label_passes
from meridian.datasets.manifest import Manifest, SourceEntry, content_sha256, file_entry
from meridian.datasets.publish import (
    PublishedDirectory,
    SnapshotDirectory,
    publish_directory,
)
from meridian.datasets.snapshot_rows import parse_rows

__all__ = [
    "ARCHIVE_RECEPTIONS",
    "EVALUATION",
    "LABELS_FILE",
    "NotARawSnapshotError",
    "build_evaluation_dataset",
]

EVALUATION = "evaluation"
"""Evaluation datasets live under ``<datasets root>/evaluation/``."""

LABELS_FILE = "labels.jsonl"
ARCHIVE_RECEPTIONS = "archive_receptions.jsonl"


class NotARawSnapshotError(ValueError):
    """Labelling was pointed at something other than a raw snapshot."""


def build_evaluation_dataset(
    raw: SnapshotDirectory,
    config: LabelConfig,
    *,
    root: Path,
    created_at: datetime,
) -> PublishedDirectory:
    """Label a raw snapshot and publish the result.

    Args:
        raw: A raw snapshot, as :func:`read_directory` verified it.
        config: The labelling configuration.
        root: The datasets root; the dataset goes under ``root/evaluation``.
        created_at: When this run happened, recorded and never hashed.

    Returns:
        Where the dataset landed and its manifest. ``written`` is False when
        the same inputs had already been labelled — the gate, passing.

    Raises:
        NotARawSnapshotError: ``raw`` is an evaluation dataset.
        MalformedSnapshotError: A row lacks a field a label needs.
    """
    if raw.manifest.kind != "raw_snapshot":
        message = f"{raw.path} is an {raw.manifest.kind}, not a raw snapshot"
        raise NotARawSnapshotError(message)
    labelled = label_passes(
        parse_rows(raw.files), as_of=raw.manifest.as_of, config=config
    )
    files = {
        LABELS_FILE: b"".join(canonical_line(one.row()) for one in labelled),
        ARCHIVE_RECEPTIONS: _archive_receptions(raw),
    }
    manifest = Manifest(
        kind="evaluation_dataset",
        schema_revision=raw.manifest.schema_revision,
        since=raw.manifest.since,
        as_of=raw.manifest.as_of,
        files=tuple(file_entry(name, data) for name, data in sorted(files.items())),
        created_at=created_at,
        counts=label_counts(labelled),
        sources=raw.manifest.sources,
        derived_from=content_sha256(raw.manifest),
        transformation_version=TRANSFORMATION_VERSION,
        config_sha256=config_sha256(config),
        parameters=config.parameters(),
    )
    name = content_sha256(manifest).hex()[:12]
    return publish_directory(root / EVALUATION, name, manifest, files)


def _archive_receptions(raw: SnapshotDirectory) -> bytes:
    """Each archive reception beside the licence and terms it arrived under."""
    terms = {one.source_id: one for one in raw.manifest.sources}
    lines = []
    for line in raw.files["archive_observations.jsonl"].splitlines():
        row: Mapping[str, object] = json.loads(line)
        source = terms.get(str(row.get("source_id")))
        lines.append(canonical_line(dict(row) | _terms(source)))
    return b"".join(lines)


def _terms(source: SourceEntry | None) -> dict[str, object]:
    """The terms columns, null where the manifest names no such source."""
    return {
        "licence": None if source is None else source.licence,
        "terms_url": None if source is None else source.terms_url,
    }
