"""The fault ledger: what a run injected, written down as it happened.

Ground truth about a fault lives here and nowhere else. It is **never sent over
MSP and never stored by the platform** (D-189): a platform that could read what
was done to it would be graded on its own answer key. The platform proves what
it detected from its own records, and ``meridian reliability faults`` joins that
proof to this file afterwards (D-192).

One JSON object per line, appended and flushed as each event happens, so a run
that dies part-way leaves a ledger that is true up to the moment it died. Three
events:

``open``
    A fault came into force on a target.
``close``
    It stopped. A fault with no ``close`` was still in force when the run ended,
    which for a revoked token is forever.
``act``
    Something the fault did to a named assignment while it was open — work a
    declining station let go of, a pass a dead receiver never began, a decode
    that failed.

Every line carries ``ledger`` (the format version), ``run_id``, ``kind``,
``target`` and ``at``. A station target is ``station:<index>`` and carries
``station_id`` and ``seed``; a platform target is ``platform:<component>`` and
carries neither. ``tick`` is the supervisor's round, absent for a platform
fault, which ``deploy/tools/chaos.py`` injects on the wall clock. ``detail`` is
an object, and ``assignment_ids`` a list, on ``act`` lines.

This file is the format's definition and the platform parses it independently,
because ``meridian-sim`` and ``meridian`` share no code (D-138). The version is
on every line so that a reader meeting a line it does not know refuses it
rather than guessing.

Reference: docs/DECISIONS.md D-105, D-189, D-192; docs/EVALUATION.md §11.2.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

__all__ = [
    "LEDGER_VERSION",
    "FaultLedger",
    "FaultRecord",
    "MalformedLedgerError",
    "read_ledger",
]

LEDGER_VERSION = 1
"""The format every line of a ledger is written in."""

_Line = dict[str, object]
_T = TypeVar("_T")

_EVENTS = frozenset({"open", "close", "act"})


class MalformedLedgerError(ValueError):
    """A ledger line that cannot be read as the format this module writes."""


@dataclass(frozen=True, slots=True)
class FaultRecord:
    """One fault window on one target, folded from its ledger lines."""

    run_id: str
    kind: str
    target: str
    opened_at: datetime
    closed_at: datetime | None = None
    station_id: str | None = None
    seed: int | None = None
    first_tick: int | None = None
    last_tick: int | None = None
    detail: Mapping[str, object] = field(default_factory=dict)
    assignment_ids: tuple[str, ...] = ()


class FaultLedger:
    """An append-only ledger file, written by the run injecting the faults.

    Args:
        path: Where the ledger lives. Created, with its parent directories, on
            the first write; appended to after that, so a restarted run
            continues the same ledger.
        run_id: The run every line is stamped with.
    """

    def __init__(self, path: Path, run_id: str) -> None:
        """Bind a ledger to its file. Nothing is written until an event."""
        self.path = path
        self._run_id = run_id

    def open(  # noqa: PLR0913 — one line's fields, named at the call site
        self,
        kind: str,
        target: str,
        at: datetime,
        *,
        tick: int | None = None,
        station_id: str | None = None,
        seed: int | None = None,
        detail: Mapping[str, object] | None = None,
    ) -> None:
        """Record that ``kind`` came into force on ``target``."""
        self._append(
            "open",
            kind,
            target,
            at,
            tick=tick,
            station_id=station_id,
            seed=seed,
            detail=detail,
        )

    def close(
        self,
        kind: str,
        target: str,
        at: datetime,
        *,
        tick: int | None = None,
    ) -> None:
        """Record that ``kind`` stopped on ``target``."""
        self._append("close", kind, target, at, tick=tick)

    def act(
        self,
        kind: str,
        target: str,
        at: datetime,
        assignment_ids: tuple[str, ...],
        *,
        tick: int | None = None,
    ) -> None:
        """Record what an open fault did to named assignments."""
        self._append("act", kind, target, at, tick=tick, assignment_ids=assignment_ids)

    def _append(  # noqa: PLR0913
        self,
        event: str,
        kind: str,
        target: str,
        at: datetime,
        *,
        tick: int | None = None,
        station_id: str | None = None,
        seed: int | None = None,
        detail: Mapping[str, object] | None = None,
        assignment_ids: tuple[str, ...] | None = None,
    ) -> None:
        """Write one line, and get it to disk before returning.

        Flushed and synced per line: a fault is rare next to a heartbeat, and a
        ledger missing the fault that crashed the run is missing the one line
        the run existed to write.
        """
        line: _Line = {
            "ledger": LEDGER_VERSION,
            "event": event,
            "run_id": self._run_id,
            "kind": kind,
            "target": target,
            "at": _render(at),
        }
        if tick is not None:
            line["tick"] = tick
        if station_id is not None:
            line["station_id"] = station_id
        if seed is not None:
            line["seed"] = seed
        if detail:
            line["detail"] = dict(detail)
        if assignment_ids is not None:
            line["assignment_ids"] = list(assignment_ids)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def read_ledger(path: Path) -> tuple[FaultRecord, ...]:
    """Fold a ledger into one record per fault window, in the order they opened.

    Args:
        path: The ledger file.

    Returns:
        Every window, closed or still open.

    Raises:
        MalformedLedgerError: A line is not JSON, is in another format version, names an
            unknown event, or closes or acts on a window that is not open.
            Refused rather than skipped, because a ledger is ground truth and a
            reader that dropped the lines it did not understand would grade the
            platform against part of the truth.
    """
    records: list[FaultRecord] = []
    open_at: dict[tuple[str, str, str], int] = {}

    for number, line in _lines(path):
        run_id, kind, target = (
            _text(line, name, number) for name in ("run_id", "kind", "target")
        )
        key = (run_id, target, kind)
        event = line["event"]
        at = _parse(_text(line, "at", number), number)
        if event == "open":
            if key in open_at:
                raise _refuse(number, f"{key} opened twice")
            open_at[key] = len(records)
            detail = line.get("detail", {})
            records.append(
                FaultRecord(
                    run_id=run_id,
                    kind=kind,
                    target=target,
                    opened_at=at,
                    station_id=_optional(line, "station_id", str, number),
                    seed=_optional(line, "seed", int, number),
                    first_tick=_optional(line, "tick", int, number),
                    detail=detail if isinstance(detail, dict) else {},
                )
            )
            continue
        if key not in open_at:
            raise _refuse(number, f"{event} of {key}, which is not open")
        index = open_at[key]
        current = records[index]
        if event == "act":
            ids = line.get("assignment_ids", [])
            if not isinstance(ids, list):
                raise _refuse(number, "assignment_ids is not a list")
            records[index] = replace(
                current,
                assignment_ids=current.assignment_ids + tuple(str(one) for one in ids),
            )
        else:
            records[index] = replace(
                current,
                closed_at=at,
                last_tick=_optional(line, "tick", int, number),
            )
            del open_at[key]

    return tuple(records)


def _refuse(number: int, reason: str) -> MalformedLedgerError:
    """The error for line ``number`` of a ledger, saying what is wrong with it."""
    return MalformedLedgerError(f"line {number}: {reason}")


def _text(line: _Line, name: str, number: int) -> str:
    """A field every line must carry, as text."""
    value = line.get(name)
    if not isinstance(value, str):
        raise _refuse(number, f"{name} is missing or not text")
    return value


def _optional(line: _Line, name: str, kind: type[_T], number: int) -> _T | None:
    """A field a line may carry, checked to be of ``kind`` when it does."""
    value = line.get(name)
    if value is None:
        return None
    if not isinstance(value, kind) or isinstance(value, bool):
        raise _refuse(number, f"{name} is not {kind.__name__}")
    return value


def _lines(path: Path) -> Iterator[tuple[int, _Line]]:
    """Every non-blank line of the ledger, parsed and checked, with its number."""
    with path.open(encoding="utf-8") as handle:
        for number, raw in enumerate(handle, start=1):
            if not raw.strip():
                continue
            try:
                line = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise _refuse(number, f"not JSON: {exc}") from exc
            if not isinstance(line, dict) or line.get("ledger") != LEDGER_VERSION:
                raise _refuse(number, f"not a version {LEDGER_VERSION} line")
            if line.get("event") not in _EVENTS:
                raise _refuse(number, f"unknown event {line.get('event')!r}")
            yield number, line


def _render(at: datetime) -> str:
    """An instant as the ledger writes it: UTC, with a ``Z``."""
    if at.tzinfo is None:
        raise ValueError("a ledger instant must be timezone-aware")
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse(text: str, number: int) -> datetime:
    """An instant as the ledger wrote it."""
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _refuse(number, f"bad instant {text!r}") from exc
