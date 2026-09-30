"""A fault run, sealed: its ledger, the evidence read for it, and its verdicts.

``meridian reliability faults --publish`` writes one after judging a ledger, as
a content-addressed directory under ``<datasets root>/faults/``:

* ``ledger.jsonl`` — the ledger as the run wrote it, ending in a newline;
* ``evidence.jsonl`` — what the platform held about each fault when it was
  judged (:mod:`meridian.reliability.fault_record`);
* ``verdicts.jsonl`` — every verdict, each check with its latency.

It is read from the live database like a raw snapshot, so it names no parent;
its ``as_of`` is when the evidence was read. An evaluation report names it by
hash and judges it again from these files alone (D-240).

Reference: docs/DECISIONS.md D-144, D-189, D-192, D-240.
"""

from __future__ import annotations

import io
import json
from collections.abc import Sequence
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
from meridian.reliability.fault_model import FaultVerdict, Gathered
from meridian.reliability.fault_record import (
    evidence_rows,
    gathered_from_rows,
    verdict_rows,
)

__all__ = [
    "EVIDENCE_FILE",
    "FAULTS",
    "LEDGER_FILE",
    "VERDICTS_FILE",
    "FaultRun",
    "NotAFaultRunError",
    "publish_fault_run",
    "read_fault_run",
]

FAULTS = "faults"
"""Fault runs live under ``<datasets root>/faults/``."""

LEDGER_FILE = "ledger.jsonl"
EVIDENCE_FILE = "evidence.jsonl"
VERDICTS_FILE = "verdicts.jsonl"


class NotAFaultRunError(ValueError):
    """A directory named as a fault run that is not one."""


@dataclass(frozen=True, slots=True)
class FaultRun:
    """A verified fault run: its faults, the evidence for each, its verdicts."""

    directory: SnapshotDirectory
    faults: tuple[InjectedFault, ...]
    gathered: tuple[Gathered, ...]
    verdicts: tuple[dict[str, object], ...]
    """The verdicts as they were judged when the run was published."""


def publish_fault_run(
    ledger: str,
    gathered: Sequence[Gathered],
    verdicts: Sequence[FaultVerdict],
    *,
    root: Path,
    stamp: tuple[str, datetime],
) -> PublishedDirectory:
    """Seal a judged fault run under ``root/faults``.

    Args:
        ledger: The ledger's text, as read.
        gathered: Each fault's evidence, in ledger order.
        verdicts: Each fault's verdict, in the same order.
        root: The datasets root.
        stamp: The database's schema revision, and when the evidence was read.
    """
    schema_revision, as_of = stamp
    files = {
        LEDGER_FILE: (ledger if ledger.endswith("\n") else ledger + "\n").encode(),
        EVIDENCE_FILE: b"".join(canonical_line(one) for one in evidence_rows(gathered)),
        VERDICTS_FILE: b"".join(canonical_line(one) for one in verdict_rows(verdicts)),
    }
    opened = [one.fault.opened_at for one in gathered]
    manifest = Manifest(
        kind="fault_run",
        schema_revision=schema_revision,
        since=min(opened, default=as_of),
        as_of=as_of,
        files=tuple(file_entry(name, data) for name, data in sorted(files.items())),
        created_at=as_of,
        counts={
            "faults": len(verdicts),
            "faults.failed": sum(not one.passed for one in verdicts),
            "faults.platform": sum(one.fault.on_platform for one in verdicts),
            "faults.station": sum(not one.fault.on_platform for one in verdicts),
        },
    )
    name = content_sha256(manifest).hex()[:12]
    return publish_directory(root / FAULTS, name, manifest, files)


def read_fault_run(path: Path) -> FaultRun:
    """Read a fault run back, verified, with its evidence ready to judge.

    Raises:
        DamagedSnapshotError: The directory does not match its manifest.
        NotAFaultRunError: It is another kind of directory.
        FaultLedgerError: Its ledger cannot be read.
        FaultRecordError: Its evidence does not match its ledger.
    """
    directory = read_directory(path)
    if directory.manifest.kind != "fault_run":
        kind = directory.manifest.kind.replace("_", " ")
        message = f"{path} is a {kind}, not a fault run"
        raise NotAFaultRunError(message)
    files = directory.files
    faults = read_fault_ledger(io.StringIO(files[LEDGER_FILE].decode("utf-8")))
    evidence = [json.loads(line) for line in files[EVIDENCE_FILE].splitlines()]
    return FaultRun(
        directory=directory,
        faults=faults,
        gathered=gathered_from_rows(faults, evidence),
        verdicts=tuple(json.loads(line) for line in files[VERDICTS_FILE].splitlines()),
    )
