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
from meridian.datasets.diagnosis_runs import DiagnosisRun
from meridian.datasets.evaluation import build_evaluation_dataset
from meridian.datasets.fault_runs import FaultRun
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
from meridian.reports.detections import detections
from meridian.reports.diagnosis import DIAGNOSIS_FILE, diagnosis_rows
from meridian.reports.fault_rows import fault_rows
from meridian.reports.orbit import ORBIT_FILE, orbit_rows
from meridian.reports.prediction import (
    MODEL_SEED_RANGE,
    Destination,
    Fitted,
    fit_variants,
    fitted_paths,
)
from meridian.reports.prediction_rows import PREDICTION_FILE, prediction_rows
from meridian.reports.reliability import RELIABILITY_FILE, snapshot_rows
from meridian.reports.render import render_report
from meridian.reports.render_orbit import orbit_figures
from meridian.reports.render_prediction import prediction_figures
from meridian.reports.render_reliability import reliability_figures
from meridian.reports.render_scheduling import scheduling_figures
from meridian.reports.render_verdict import verdict_figures
from meridian.reports.scheduling import (
    SCHEDULING_FILE,
    SOLVER_SEED_RANGE,
    scheduling_section,
)
from meridian.reports.verdict import VERDICT_FILE, verdict_section

__all__ = [
    "BOOTSTRAP_ORBIT",
    "BOOTSTRAP_PREDICTION",
    "BOOTSTRAP_SCHEDULING",
    "BOOTSTRAP_VERDICT",
    "CONFIG_FILE",
    "METHOD_VERSION",
    "REPORTS",
    "REPORT_FILE",
    "RUN_FILE",
    "VERDICT",
    "NotARawSnapshotError",
    "Run",
    "RunExistsError",
    "RunInputs",
    "build_run",
    "publish_run",
    "with_environment",
]

METHOD_VERSION = "report-8"
"""Bumped whenever a section's method changes, so two runs made under different
methods can never share a hash. ``report-8`` added Stage 27's loss diagnosis
(D-278)."""

REPORTS = "reports"
"""Runs published without ``--output`` live under ``<datasets root>/reports/``."""

RUN_FILE = "run.jsonl"
BOOTSTRAP_PREDICTION = "bootstrap.prediction"
BOOTSTRAP_SCHEDULING = "bootstrap.scheduling"
BOOTSTRAP_ORBIT = "bootstrap.orbit"
VERDICT = "verdict"
BOOTSTRAP_VERDICT = "bootstrap.verdict"
SOLVER = "solver"
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


@dataclass(frozen=True, slots=True)
class RunInputs:
    """What a run is computed from, besides its seed."""

    raw: SnapshotDirectory
    """The raw snapshot, as :func:`read_directory` verified it."""

    config: ConfigFile
    """The configuration, beside the bytes it was read from."""

    faults: tuple[FaultRun, ...] = ()
    """Sealed fault runs the reliability section judges again (D-240)."""

    diagnoses: tuple[DiagnosisRun, ...] = ()
    """Sealed simulated fleets the loss-diagnosis section judges (D-278)."""


def build_run(
    inputs: RunInputs,
    *,
    seed: int,
    root: Path,
    created_at: datetime,
) -> Run:
    """Every section, from one raw snapshot, one configuration and one seed.

    Args:
        inputs: The raw snapshot, the configuration and any fault runs.
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
    raw, config = inputs.raw, inputs.config
    if raw.manifest.kind != "raw_snapshot":
        message = f"{raw.path} is a {raw.manifest.kind}, not a raw snapshot"
        raise NotARawSnapshotError(message)
    labelled = build_evaluation_dataset(
        raw, config.config.labels, root=root, created_at=created_at
    )
    dataset = read_directory(labelled.path)
    computed = _sections(
        inputs, dataset, seed, Destination(root=root, created_at=created_at)
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
            "fault_runs": [
                content_sha256(one.directory.manifest) for one in inputs.faults
            ],
            "diagnosis_runs": [
                content_sha256(one.directory.manifest) for one in inputs.diagnoses
            ],
        },
        environment=computed.measured,
    )
    return Run(manifest=manifest, files=files)


@dataclass(frozen=True, slots=True)
class _Computed:
    """Each results file's rows, the seeds they drew, and the models fitted."""

    rows: Mapping[str, Rows]
    seeds: Mapping[str, int]
    models: Mapping[str, bytes]
    measured: Mapping[str, object]
    """What the run measured about the machine: recorded, never hashed."""


def _sections(
    inputs: RunInputs,
    dataset: SnapshotDirectory,
    seed: int,
    destination: Destination,
) -> _Computed:
    """Every section's rows. Each seed is derived here, by name, and recorded."""
    raw, config = inputs.raw, inputs.config
    reliability = config.config.reliability
    prediction = config.config.prediction
    seeds, outcomes, examples = fit_variants(
        dataset, raw, prediction, seed=seed, destination=destination
    )
    seeds[BOOTSTRAP_PREDICTION] = derive(seed, BOOTSTRAP_PREDICTION)
    seeds[SOLVER] = derive(seed, SOLVER) % SOLVER_SEED_RANGE
    seeds[BOOTSTRAP_SCHEDULING] = derive(seed, BOOTSTRAP_SCHEDULING)
    seeds[BOOTSTRAP_ORBIT] = derive(seed, BOOTSTRAP_ORBIT)
    seeds[VERDICT] = derive(seed, VERDICT) % MODEL_SEED_RANGE
    seeds[BOOTSTRAP_VERDICT] = derive(seed, BOOTSTRAP_VERDICT)
    scheduled = scheduling_section(
        dataset,
        raw,
        fitted_paths(outcomes),
        config.config.scheduling,
        seeds=(seeds[SOLVER], seeds[BOOTSTRAP_SCHEDULING]),
    )
    verdict = verdict_section(
        raw,
        config.config.verdict,
        seeds=(seeds[VERDICT], seeds[BOOTSTRAP_VERDICT]),
        destination=destination,
    )
    rows = {
        RUN_FILE: _run_rows(raw.manifest, config, seed, seeds),
        DATA_FILE: data_rows(raw, dataset),
        PREDICTION_FILE: prediction_rows(
            outcomes, examples, prediction, seed=seeds[BOOTSTRAP_PREDICTION]
        ),
        SCHEDULING_FILE: scheduled.rows,
        ORBIT_FILE: orbit_rows(
            detections(raw.files), config.config.orbit, seed=seeds[BOOTSTRAP_ORBIT]
        ),
        RELIABILITY_FILE: [
            *snapshot_rows(dataset, reliability),
            *fault_rows(
                inputs.faults, detection_max_s=reliability.slo.failure_detection_max_s
            ),
        ],
        VERDICT_FILE: verdict.rows,
        DIAGNOSIS_FILE: diagnosis_rows(inputs.diagnoses, raw),
    }
    models = {
        one.variant.name: one.sha256 for one in outcomes if isinstance(one, Fitted)
    }
    if verdict.model_sha256 is not None:
        models[VERDICT] = verdict.model_sha256
    runtimes = dict(scheduled.runtimes)
    return _Computed(
        rows=rows,
        seeds=dict(sorted(seeds.items())),
        models=models,
        measured={
            "solver": runtimes.pop("solver"),
            "runtime_s": {"scheduling": runtimes.get("schedulers", {})},
            "fault_run_paths": {
                content_sha256(one.directory.manifest).hex(): str(one.directory.path)
                for one in inputs.faults
            },
            "diagnosis_run_paths": {
                content_sha256(one.directory.manifest).hex(): str(one.directory.path)
                for one in inputs.diagnoses
            },
        },
    )


def _files(computed: _Computed, config_text: bytes) -> dict[str, bytes]:
    """The results files, and the report and figures rendered from them parsed."""
    files = {name: _lines(rows) for name, rows in computed.rows.items()}
    parsed = {name: _parsed(data) for name, data in files.items()}
    files[REPORT_FILE] = render_report(
        {name.removesuffix(".jsonl"): rows for name, rows in parsed.items()}
    )
    files |= prediction_figures(parsed[PREDICTION_FILE])
    files |= scheduling_figures(parsed[SCHEDULING_FILE])
    files |= orbit_figures(parsed[ORBIT_FILE])
    files |= reliability_figures(parsed[RELIABILITY_FILE])
    files |= verdict_figures(parsed[VERDICT_FILE])
    files[CONFIG_FILE] = config_text
    return files


def with_environment(run: Run, environment: Mapping[str, object]) -> Run:
    """The same run, recording the machine that made it. The hash is unchanged.

    Merged into what the build itself measured, and ``runtime_s`` merged key
    by key, so the build's own time sits beside the solver's.
    """
    held = dict(run.manifest.environment)
    runtimes = {
        **_table(held.get("runtime_s")),
        **_table(environment.get("runtime_s")),
    }
    merged = held | dict(environment) | ({"runtime_s": runtimes} if runtimes else {})
    return replace(run, manifest=replace(run.manifest, environment=merged))


def _table(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


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
