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
from meridian.datasets.seeds import derive
from meridian.reports.config import ConfigFile, report_config_sha256
from meridian.reports.data import DATA_FILE, data_rows
from meridian.reports.prediction import Destination, Fitted, fit_variants
from meridian.reports.prediction_rows import PREDICTION_FILE, prediction_rows
from meridian.reports.render import render_report
from meridian.reports.render_prediction import prediction_figures

__all__ = [
    "BOOTSTRAP_PREDICTION",
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

METHOD_VERSION = "report-2"
"""Bumped whenever a section's method changes, so two runs made under different
methods can never share a hash."""

REPORTS = "reports"
"""Runs published without ``--output`` live under ``<datasets root>/reports/``."""

RUN_FILE = "run.jsonl"
BOOTSTRAP_PREDICTION = "bootstrap.prediction"
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
        root: The datasets root the evaluation dataset and the models are
            published under.
        created_at: When this run happened, recorded and never hashed.

    Returns:
        The run, with an empty environment.

    Raises:
        NotARawSnapshotError: ``raw`` is not a raw snapshot.
        MalformedSnapshotError: A row lacks a field a label needs.
        ModuleNotFoundError: The ``fit`` extra is not installed.
    """
    if raw.manifest.kind != "raw_snapshot":
        message = f"{raw.path} is a {raw.manifest.kind}, not a raw snapshot"
        raise NotARawSnapshotError(message)
    labelled = build_evaluation_dataset(
        raw, config.config.labels, root=root, created_at=created_at
    )
    dataset = read_directory(labelled.path)
    computed = _sections(
        raw, dataset, config, seed, Destination(root=root, created_at=created_at)
    )
    files = _files(computed, config.text)
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
            "seeds": computed.seeds,
            "config": config.config.parameters(),
            "evaluation_dataset": content_sha256(dataset.manifest),
            "models": computed.models,
        },
    )
    return Run(manifest=manifest, files=files)


@dataclass(frozen=True, slots=True)
class _Computed:
    """Each results file's rows, the seeds they drew, and the models fitted."""

    rows: Mapping[str, Rows]
    seeds: Mapping[str, int]
    models: Mapping[str, bytes]


def _sections(
    raw: SnapshotDirectory,
    dataset: SnapshotDirectory,
    config: ConfigFile,
    seed: int,
    destination: Destination,
) -> _Computed:
    """Every section's rows. Each seed is derived here, by name, and recorded."""
    prediction = config.config.prediction
    seeds, outcomes, inputs = fit_variants(
        dataset, raw, prediction, seed=seed, destination=destination
    )
    seeds[BOOTSTRAP_PREDICTION] = derive(seed, BOOTSTRAP_PREDICTION)
    rows = {
        RUN_FILE: _run_rows(raw.manifest, config, seed, seeds),
        DATA_FILE: data_rows(raw, dataset),
        PREDICTION_FILE: prediction_rows(
            outcomes, inputs, prediction, seed=seeds[BOOTSTRAP_PREDICTION]
        ),
    }
    models = {
        one.variant.name: one.sha256 for one in outcomes if isinstance(one, Fitted)
    }
    return _Computed(rows=rows, seeds=dict(sorted(seeds.items())), models=models)


def _files(computed: _Computed, config_text: bytes) -> dict[str, bytes]:
    """The results files, and the report and figures rendered from them parsed."""
    files = {name: _lines(rows) for name, rows in computed.rows.items()}
    parsed = {name: _parsed(data) for name, data in files.items()}
    files[REPORT_FILE] = render_report(
        run=parsed[RUN_FILE],
        data=parsed[DATA_FILE],
        prediction=parsed[PREDICTION_FILE],
    )
    files |= prediction_figures(parsed[PREDICTION_FILE])
    files[CONFIG_FILE] = config_text
    return files


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


def _run_rows(
    raw: Manifest, config: ConfigFile, seed: int, seeds: Mapping[str, int]
) -> Rows:
    """The run record: method, inputs and seeds. Every derived seed has a row."""
    record: Rows = [
        {
            "row": "run",
            "method": METHOD_VERSION,
            "snapshot_sha256": content_sha256(raw),
            "config_sha256": report_config_sha256(config.config),
            "seed": seed,
        }
    ]
    return record + [
        {"row": "seed", "component": name, "seed": value}
        for name, value in sorted(seeds.items())
    ]


def _lines(rows: Rows) -> bytes:
    return b"".join(canonical_line(one) for one in rows)


def _parsed(data: bytes) -> list[dict[str, object]]:
    """A results file read back, as a reader of the directory would read it."""
    return [json.loads(line) for line in data.splitlines()]
