"""Building a run: a raw snapshot, a configuration and a seed, made a sealed directory.

:func:`build_run` is a function of its inputs. It reads the raw snapshot
through :func:`~meridian.datasets.publish.read_directory`, so an edited
snapshot stops a run rather than changing its numbers; it labels the snapshot
into an evaluation dataset under the datasets root, as ``meridian snapshot
label`` would, which is idempotent; and it builds each section's rows, writes
them as canonical JSON Lines, and renders ``report.md`` **from those files
parsed back**, never from the objects that made them.

No clock, database or socket. ``created_at`` is handed in and never hashed,
and the environment block is attached afterwards by
:func:`with_environment`, because it describes the machine rather than the
result (D-235).

Reference: docs/DECISIONS.md D-235, D-236.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from meridian.datasets.canonical import canonical_line
from meridian.datasets.evaluation import build_evaluation_dataset
from meridian.datasets.manifest import Manifest, content_sha256, file_entry
from meridian.datasets.publish import (
    DamagedSnapshotError,
    PublishedDirectory,
    SnapshotDirectory,
    publish_directory,
    read_directory,
)
from meridian.reports.config import ConfigFile, report_config_sha256
from meridian.reports.data import DATA_FILE, data_rows
from meridian.reports.render import render_report

__all__ = [
    "CONFIG_FILE",
    "METHOD_VERSION",
    "REPORTS",
    "REPORT_FILE",
    "RUN_FILE",
    "NotARawSnapshotError",
    "Run",
    "RunExistsError",
    "build_run",
    "publish_run",
    "with_environment",
]

METHOD_VERSION = "report-1"
"""Bumped whenever a section's method changes, so two runs made under different
methods can never share a hash."""

REPORTS = "reports"
"""Runs published without ``--output`` live under ``<datasets root>/reports/``."""

RUN_FILE = "run.jsonl"
REPORT_FILE = "report.md"
CONFIG_FILE = "config.toml"

Rows = list[dict[str, object]]


class NotARawSnapshotError(ValueError):
    """A run was pointed at something other than a raw snapshot."""


class RunExistsError(RuntimeError):
    """The output already holds a different run, which is never overwritten."""


@dataclass(frozen=True, slots=True)
class Run:
    """A run's files and the manifest that names them, not yet on disk."""

    manifest: Manifest
    files: Mapping[str, bytes]


def build_run(
    raw: SnapshotDirectory,
    config: ConfigFile,
    *,
    seed: int,
    root: Path,
    created_at: datetime,
) -> Run:
    """Every section, from one raw snapshot, one configuration and one seed.

    Args:
        raw: The raw snapshot, as :func:`read_directory` verified it.
        config: The configuration, beside the bytes it was read from.
        seed: The master seed every component's seed is derived from.
        root: The datasets root the evaluation dataset is published under.
        created_at: When this run happened, recorded and never hashed.

    Returns:
        The run, with an empty environment.

    Raises:
        NotARawSnapshotError: ``raw`` is not a raw snapshot.
        MalformedSnapshotError: A row lacks a field a label needs.
    """
    if raw.manifest.kind != "raw_snapshot":
        message = f"{raw.path} is a {raw.manifest.kind}, not a raw snapshot"
        raise NotARawSnapshotError(message)
    labelled = build_evaluation_dataset(
        raw, config.config.labels, root=root, created_at=created_at
    )
    dataset = read_directory(labelled.path)
    sections: dict[str, Rows] = {
        RUN_FILE: _run_rows(raw.manifest, config, seed),
        DATA_FILE: data_rows(raw, dataset),
    }
    files = {name: _lines(rows) for name, rows in sections.items()}
    files[REPORT_FILE] = render_report(
        run=_parsed(files[RUN_FILE]), data=_parsed(files[DATA_FILE])
    )
    files[CONFIG_FILE] = config.text
    manifest = Manifest(
        kind="evaluation_report",
        schema_revision=raw.manifest.schema_revision,
        since=raw.manifest.since,
        as_of=raw.manifest.as_of,
        files=tuple(file_entry(name, data) for name, data in sorted(files.items())),
        created_at=created_at,
        counts=dict(raw.manifest.counts),
        sources=raw.manifest.sources,
        derived_from=content_sha256(raw.manifest),
        transformation_version=METHOD_VERSION,
        config_sha256=report_config_sha256(config.config),
        parameters={
            "seed": seed,
            "seeds": {},
            "config": config.config.parameters(),
            "evaluation_dataset": content_sha256(dataset.manifest),
        },
    )
    return Run(manifest=manifest, files=files)


def with_environment(run: Run, environment: Mapping[str, object]) -> Run:
    """The same run, recording the machine that made it. The hash is unchanged."""
    return replace(run, manifest=replace(run.manifest, environment=environment))


def publish_run(run: Run, output: Path) -> PublishedDirectory:
    """Write a run once, atomically, at ``output``.

    Raises:
        RunExistsError: ``output`` already holds a different run, or one that
            no longer matches its own manifest.
    """
    try:
        return publish_directory(output.parent, output.name, run.manifest, run.files)
    except DamagedSnapshotError as exc:
        message = f"{output} was not written: {exc}"
        raise RunExistsError(message) from exc


def _run_rows(raw: Manifest, config: ConfigFile, seed: int) -> Rows:
    """The run record: method, inputs and seeds. Every derived seed has a row."""
    return [
        {
            "row": "run",
            "method": METHOD_VERSION,
            "snapshot_sha256": content_sha256(raw),
            "config_sha256": report_config_sha256(config.config),
            "seed": seed,
        }
    ]


def _lines(rows: Rows) -> bytes:
    return b"".join(canonical_line(one) for one in rows)


def _parsed(data: bytes) -> list[dict[str, object]]:
    """A results file read back, as a reader of the directory would read it."""
    return [json.loads(line) for line in data.splitlines()]
