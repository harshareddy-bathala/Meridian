"""A simulated diagnosis run, sealed: its ledger, its cases and its diagnoses.

``deploy/tools/diagnosis_runs.py`` runs a fleet under a fault scenario, lets the
platform classify and diagnose every loss, and writes one of these under
``<datasets root>/diagnoses/``, content-addressed:

* ``run.jsonl`` — one row: the scenario, the master seed, the fleet, the
  window, and the methods and thresholds the diagnoses were made under;
* ``ledger.jsonl`` — the run's fault ledger as the simulator wrote it, the
  ground truth, which never entered the platform (D-105, D-189);
* ``cases.jsonl`` — every scheduled assignment of the fleet: its station's
  index, what became of it, what it reported, and what the outcome model would
  have said with nothing wrong, recomputed from the seed;
* ``diagnoses.jsonl`` — every diagnosis the platform wrote, without the instant
  it was written.

**Every row in it is simulated** (rule 5), and the manifest counts them so.
The evaluation report joins the ledger to the diagnoses from these files alone,
which is the only place ground truth meets a diagnosis (D-278).

Reference: docs/DECISIONS.md D-105, D-189, D-240, D-278.
"""

from __future__ import annotations

import io
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from meridian.datasets.canonical import canonical_line
from meridian.datasets.manifest import Manifest, content_sha256, file_entry
from meridian.datasets.publish import (
    PublishedDirectory,
    SnapshotDirectory,
    publish_directory,
    read_directory,
)
from meridian.reliability.fault_ledger import InjectedFault, read_fault_ledger

__all__ = [
    "CASES_FILE",
    "DIAGNOSES",
    "DIAGNOSES_FILE",
    "LEDGER_FILE",
    "RUN_FILE",
    "DiagnosisRun",
    "NotADiagnosisRunError",
    "publish_diagnosis_run",
    "read_diagnosis_run",
]

DIAGNOSES = "diagnoses"
"""Diagnosis runs live under ``<datasets root>/diagnoses/``."""

RUN_FILE = "run.jsonl"
LEDGER_FILE = "ledger.jsonl"
CASES_FILE = "cases.jsonl"
DIAGNOSES_FILE = "diagnoses.jsonl"


class NotADiagnosisRunError(ValueError):
    """A directory named as a diagnosis run that is not one."""


@dataclass(frozen=True, slots=True)
class DiagnosisRun:
    """A verified diagnosis run, ready for the report to judge."""

    directory: SnapshotDirectory
    run: dict[str, object]
    faults: tuple[InjectedFault, ...]
    cases: tuple[dict[str, object], ...]
    diagnoses: tuple[dict[str, object], ...]


def publish_diagnosis_run(  # noqa: PLR0913 — a run's four files and its stamp
    ledger: str,
    run: Mapping[str, object],
    cases: Sequence[Mapping[str, object]],
    diagnoses: Sequence[Mapping[str, object]],
    *,
    root: Path,
    stamp: tuple[str, datetime, datetime],
) -> PublishedDirectory:
    """Seal one fleet's diagnosis run under ``root/diagnoses``.

    Args:
        ledger: The run's fault ledger, as the simulator wrote it.
        run: What the run was: scenario, seed, fleet, window, methods.
        cases: One row per scheduled assignment of the fleet.
        diagnoses: One row per diagnosis the platform wrote.
        root: The datasets root.
        stamp: The schema revision, when the run began, and when it was read.

    Raises:
        ValueError: A row that is not simulated: nothing measured belongs here.
    """
    measured = [one for one in (*cases, *diagnoses) if one.get("simulated") is not True]
    if measured:
        raise ValueError(
            f"{len(measured)} rows are not simulated; a diagnosis run holds only "
            "a simulated fleet's"
        )
    schema_revision, since, as_of = stamp
    files = {
        RUN_FILE: canonical_line(dict(run)),
        LEDGER_FILE: (ledger if ledger.endswith("\n") else ledger + "\n").encode(),
        CASES_FILE: b"".join(canonical_line(dict(one)) for one in cases),
        DIAGNOSES_FILE: b"".join(canonical_line(dict(one)) for one in diagnoses),
    }
    manifest = Manifest(
        kind="diagnosis_run",
        schema_revision=schema_revision,
        since=since,
        as_of=as_of,
        files=tuple(file_entry(name, data) for name, data in sorted(files.items())),
        created_at=as_of,
        counts={
            "cases.simulated": len(cases),
            "diagnoses.simulated": len(diagnoses),
        },
    )
    name = content_sha256(manifest).hex()[:12]
    return publish_directory(root / DIAGNOSES, name, manifest, files)


def read_diagnosis_run(path: Path) -> DiagnosisRun:
    """Read a diagnosis run back, verified.

    Raises:
        DamagedSnapshotError: The directory does not match its manifest.
        NotADiagnosisRunError: It is another kind of directory.
        FaultLedgerError: Its ledger cannot be read.
    """
    directory = read_directory(path)
    if directory.manifest.kind != "diagnosis_run":
        kind = directory.manifest.kind.replace("_", " ")
        raise NotADiagnosisRunError(f"{path} is a {kind}, not a diagnosis run")
    files = directory.files

    def rows(name: str) -> tuple[dict[str, object], ...]:
        return tuple(json.loads(line) for line in files[name].splitlines())

    (run,) = rows(RUN_FILE)
    return DiagnosisRun(
        directory=directory,
        run=run,
        faults=read_fault_ledger(io.StringIO(files[LEDGER_FILE].decode("utf-8"))),
        cases=rows(CASES_FILE),
        diagnoses=rows(DIAGNOSES_FILE),
    )
