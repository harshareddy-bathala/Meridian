"""A simulated watch run, sealed: its ledger, its receptions and its warnings.

``deploy/tools/watch_runs.py`` flies a fleet clean and then under a scenario,
lets the platform watch every receive chain as the jobs service does, and
writes one of these under ``<datasets root>/watches/``, content-addressed:

* ``run.jsonl`` — one row: the scenario, the master seed, the fleet, when the
  clean stretch ended, and the methods and settings the watch ran under;
* ``ledger.jsonl`` — the run's fault ledger as the simulator wrote it, each
  degradation's onset and rate: the ground truth, which never entered the
  platform (D-105, D-189);
* ``cases.jsonl`` — every scheduled assignment of the fleet: its station's
  index, its window, what it reported, and what the outcome model would have
  said with nothing wrong, recomputed from the seed;
* ``warnings.jsonl`` — every receive-chain warning the platform raised, with
  when it was raised and cleared.

**Every row in it is simulated** (rule 5), and the manifest counts them so.
The evaluation report joins the ledger to the warnings from these files alone
(D-296).

Reference: docs/DECISIONS.md D-105, D-189, D-278, D-296.
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
    "LEDGER_FILE",
    "RUN_FILE",
    "WARNINGS_FILE",
    "WATCHES",
    "NotAWatchRunError",
    "WatchRun",
    "publish_watch_run",
    "read_watch_run",
]

WATCHES = "watches"
"""Watch runs live under ``<datasets root>/watches/``."""

RUN_FILE = "run.jsonl"
LEDGER_FILE = "ledger.jsonl"
CASES_FILE = "cases.jsonl"
WARNINGS_FILE = "warnings.jsonl"


class NotAWatchRunError(ValueError):
    """A directory named as a watch run that is not one."""


@dataclass(frozen=True, slots=True)
class WatchRun:
    """A verified watch run, ready for the report to judge."""

    directory: SnapshotDirectory
    run: dict[str, object]
    faults: tuple[InjectedFault, ...]
    cases: tuple[dict[str, object], ...]
    warnings: tuple[dict[str, object], ...]


def publish_watch_run(  # noqa: PLR0913 — a run's four files and its stamp
    ledger: str,
    run: Mapping[str, object],
    cases: Sequence[Mapping[str, object]],
    warnings: Sequence[Mapping[str, object]],
    *,
    root: Path,
    stamp: tuple[str, datetime, datetime],
) -> PublishedDirectory:
    """Seal one fleet's watch run under ``root/watches``.

    Args:
        ledger: The run's fault ledger, as the simulator wrote it.
        run: What the run was: scenario, seed, fleet, window, methods.
        cases: One row per scheduled assignment of the fleet.
        warnings: One row per receive-chain warning the platform raised.
        root: The datasets root.
        stamp: The schema revision, when the run began, and when it ended.

    Raises:
        ValueError: A row that is not simulated: nothing measured belongs here.
    """
    measured = [one for one in (*cases, *warnings) if one.get("simulated") is not True]
    if measured:
        raise ValueError(
            f"{len(measured)} rows are not simulated; a watch run holds only "
            "a simulated fleet's"
        )
    schema_revision, since, as_of = stamp
    files = {
        RUN_FILE: canonical_line(dict(run)),
        LEDGER_FILE: (
            ledger if ledger.endswith("\n") or not ledger else ledger + "\n"
        ).encode(),
        CASES_FILE: b"".join(canonical_line(dict(one)) for one in cases),
        WARNINGS_FILE: b"".join(canonical_line(dict(one)) for one in warnings),
    }
    manifest = Manifest(
        kind="watch_run",
        schema_revision=schema_revision,
        since=since,
        as_of=as_of,
        files=tuple(file_entry(name, data) for name, data in sorted(files.items())),
        created_at=as_of,
        counts={
            "cases.simulated": len(cases),
            "warnings.simulated": len(warnings),
        },
    )
    name = content_sha256(manifest).hex()[:12]
    return publish_directory(root / WATCHES, name, manifest, files)


def read_watch_run(path: Path) -> WatchRun:
    """Read a watch run back, verified.

    Raises:
        DamagedSnapshotError: The directory does not match its manifest.
        NotAWatchRunError: It is another kind of directory.
        FaultLedgerError: Its ledger cannot be read.
    """
    directory = read_directory(path)
    if directory.manifest.kind != "watch_run":
        kind = directory.manifest.kind.replace("_", " ")
        raise NotAWatchRunError(f"{path} is a {kind}, not a watch run")
    files = directory.files

    def rows(name: str) -> tuple[dict[str, object], ...]:
        return tuple(json.loads(line) for line in files[name].splitlines())

    (run,) = rows(RUN_FILE)
    return WatchRun(
        directory=directory,
        run=run,
        faults=read_fault_ledger(io.StringIO(files[LEDGER_FILE].decode("utf-8"))),
        cases=rows(CASES_FILE),
        warnings=rows(WARNINGS_FILE),
    )
