"""Sealing a regional report as a content-addressed directory, like every dataset.

A report is published under ``<datasets root>/regions/<hash prefix>/`` with a
manifest naming the raw snapshot it was computed from, the method version, and
the configuration's values and hash. Publishing the same snapshot under the
same configuration twice names the same directory and writes nothing the
second time — the regional half of rule 8, demonstrable at a prompt (D-229).

Reference: docs/DECISIONS.md D-144, D-229.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from meridian.datasets.manifest import Manifest, content_sha256, file_entry
from meridian.datasets.publish import PublishedDirectory, read_directory
from meridian.datasets.publish import publish_directory as _publish
from meridian.regions.config import RegionsConfig
from meridian.regions.report import RegionsReport, build_report, report_files
from meridian.regions.series import METHOD_VERSION

__all__ = ["REGIONS", "lineage_of", "publish_report"]

REGIONS = "regions"
"""Reports live under ``<datasets root>/regions/``."""


def lineage_of(snapshot_sha256: bytes, config: RegionsConfig) -> bytes:
    """The two hashes an alert id is derived from, joined."""
    return snapshot_sha256 + config.sha256


def publish_report(
    snapshot: Path, config: RegionsConfig, *, root: Path, created_at: datetime
) -> tuple[PublishedDirectory, RegionsReport]:
    """Compute a report from a raw snapshot and publish it.

    Args:
        snapshot: The raw snapshot's directory. Verified before it is read.
        config: The regional configuration.
        root: The datasets root.
        created_at: When this run happened, recorded and never hashed.

    Returns:
        Where the report landed, and what it says.

    Raises:
        DamagedSnapshotError: The raw snapshot is not what its manifest says.
        ValueError: It is not a raw snapshot.
    """
    raw = read_directory(snapshot)
    if raw.manifest.kind != "raw_snapshot":
        message = f"{snapshot} is a {raw.manifest.kind}, not a raw snapshot"
        raise ValueError(message)
    snapshot_sha = content_sha256(raw.manifest)
    report = build_report(raw.files, config)
    files = report_files(report, lineage_of(snapshot_sha, config))
    manifest = Manifest(
        kind="regions_report",
        schema_revision=raw.manifest.schema_revision,
        since=raw.manifest.since,
        as_of=raw.manifest.as_of,
        files=tuple(file_entry(name, data) for name, data in sorted(files.items())),
        created_at=created_at,
        counts=_counts(report),
        sources=raw.manifest.sources,
        derived_from=snapshot_sha,
        transformation_version=METHOD_VERSION,
        config_sha256=config.sha256,
        parameters=config.parameters(),
    )
    name = content_sha256(manifest).hex()[:12]
    return _publish(root / REGIONS, name, manifest, files), report


def _counts(report: RegionsReport) -> dict[str, int]:
    """Totals the manifest states, measured and simulated coverage apart."""
    simulated = sum(1 for one in report.coverage if one.simulated)
    return {
        "areas": len(report.areas),
        "areas.active": sum(1 for one in report.areas if one.active),
        "series": len(report.series),
        "changes": len(report.changes),
        "alerts": sum(1 for one in report.changes if one.verdict == "alert"),
        "coverage.measured": len(report.coverage) - simulated,
        "coverage.simulated": simulated,
        "imagery": len(report.imagery),
    }
