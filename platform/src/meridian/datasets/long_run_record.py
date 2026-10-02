"""A long run's own record, sealed inside its fault run as ``long_run.json``.

``deploy/tools/long_run.py`` judges a long run twice:
- **its own record** — whether the host slept, a container died or turned
  unhealthy, an alert fired with no cause or stayed silent when owed, an
  observation was left queued, memory climbed or disk ran short;
- **every fault** — ``meridian reliability faults``.

The second seals the first beside the ledger, the evidence and the verdicts, so
one hash names everything a reader needs to say the platform survived the run
(D-257).

The platform reads only what the acceptance needs, strictly:
- the run's ``started`` and ``ended`` instants, and every ``interruptions``
  gap its tool was stopped for, which together decide its length. A gap does
  not count: the platform ran on, but nothing injected a fault or looked;
- ``passed`` and the ``failures`` that decided it;
- the ``seed`` its faults were drawn from.

The rest of the record is kept as written and shown, never interpreted here.
The record is stored canonically, so the same record always seals to the same
bytes.

Reference: docs/DECISIONS.md D-198, D-240, D-257.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from meridian.datasets.canonical import canonical_bytes

__all__ = [
    "FORMAT_PREFIX",
    "RUN_RECORD_FILE",
    "LongRunRecord",
    "LongRunRecordError",
    "parse_long_run_record",
    "record_bytes",
]

RUN_RECORD_FILE = "long_run.json"
FORMAT_PREFIX = "meridian-long-run/"


class LongRunRecordError(ValueError):
    """A run record that is not one, said by the field that is wrong."""


@dataclass(frozen=True, slots=True)
class LongRunRecord:
    """What a long run recorded of itself."""

    started: datetime
    ended: datetime
    passed: bool
    failures: tuple[str, ...]
    seed: int
    interrupted_s: float
    """Seconds the tool was stopped for, summed over every gap."""
    document: Mapping[str, object]
    """The whole record, as the tool wrote it."""

    @property
    def hours(self) -> float:
        """How long the run was watched: start to end, less every gap."""
        span = (self.ended - self.started).total_seconds()
        return (span - self.interrupted_s) / 3600

    @property
    def interrupted_hours(self) -> float:
        """How long the tool was stopped for, which the run's length leaves out."""
        return self.interrupted_s / 3600


def parse_long_run_record(raw: bytes) -> LongRunRecord:
    """Read a run record, refusing one that cannot say what the acceptance needs.

    Raises:
        LongRunRecordError: Not JSON, not a run record, or a field is missing
            or of the wrong kind.
    """
    try:
        stored = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        message = f"the run record is not readable JSON: {exc}"
        raise LongRunRecordError(message) from exc
    if not isinstance(stored, dict):
        message = "the run record is not a JSON object"
        raise LongRunRecordError(message)
    if not str(stored.get("format", "")).startswith(FORMAT_PREFIX):
        message = f"the run record's format is {stored.get('format')!r}"
        raise LongRunRecordError(message)
    failures = stored.get("failures")
    if not isinstance(failures, list) or not all(
        isinstance(one, str) for one in failures
    ):
        message = "the run record's failures are not a list of reasons"
        raise LongRunRecordError(message)
    passed, seed = stored.get("passed"), stored.get("seed")
    if not isinstance(passed, bool) or passed == bool(failures):
        message = "the run record's passed must be true exactly when it has no failures"
        raise LongRunRecordError(message)
    if isinstance(seed, bool) or not isinstance(seed, int):
        message = f"the run record's seed is {seed!r}, not a whole number"
        raise LongRunRecordError(message)
    started, ended = _instant(stored, "started"), _instant(stored, "ended")
    if ended < started:
        message = "the run record ends before it starts"
        raise LongRunRecordError(message)
    gaps = _gaps(stored.get("interruptions", []), started, ended)
    return LongRunRecord(started, ended, passed, tuple(failures), seed, gaps, stored)


def record_bytes(record: LongRunRecord) -> bytes:
    """The record as it is sealed: canonical JSON, ending in a newline."""
    return canonical_bytes(dict(record.document)) + b"\n"


def _instant(stored: Mapping[str, object], key: str) -> datetime:
    value = stored.get(key)
    try:
        instant = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        message = f"the run record's {key} is {value!r}, not an instant"
        raise LongRunRecordError(message) from exc
    if instant.tzinfo is None:
        message = f"the run record's {key} names no timezone"
        raise LongRunRecordError(message)
    return instant


def _gaps(value: object, started: datetime, ended: datetime) -> float:
    """Seconds inside the run the tool was stopped for, each gap clipped to it."""
    if not isinstance(value, list):
        message = "the run record's interruptions are not a list"
        raise LongRunRecordError(message)
    total = 0.0
    for index, gap in enumerate(value):
        if not isinstance(gap, dict):
            message = f"the run record's interruption {index} is not an object"
            raise LongRunRecordError(message)
        start = max(_instant(gap, "from"), started)
        end = min(_instant(gap, "to"), ended)
        total += max(0.0, (end - start).total_seconds())
    return total
