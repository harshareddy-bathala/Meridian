"""The fault ledger, as the platform reads it.

A run's ledger is ground truth about what it injected, kept by the simulator and
the operator's tools and never by the platform (D-189). The platform cannot
import ``meridian_sim``, whose ``ledger.py`` defines the format (D-138), so it
reads the format itself; ``tests/unit/test_reliability_faults.py`` reads a
ledger the simulator wrote, so the two readers cannot drift apart unseen.

Reference: docs/DECISIONS.md D-138, D-189, D-192.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime

__all__ = ["LEDGER_VERSION", "FaultLedgerError", "InjectedFault", "read_fault_ledger"]

LEDGER_VERSION = 1
"""The ledger format this module reads; ``meridian_sim/ledger.py`` defines it."""


class FaultLedgerError(ValueError):
    """A ledger line this module cannot read as the format it expects."""


@dataclass(frozen=True, slots=True)
class InjectedFault:
    """One fault window from the ledger."""

    run_id: str
    kind: str
    target: str
    opened_at: datetime
    closed_at: datetime | None = None
    station_id: str | None = None
    assignment_ids: tuple[str, ...] = ()
    acted_at: Mapping[str, datetime] = field(default_factory=dict)
    """When the fault first acted on each of :attr:`assignment_ids`."""

    detail: Mapping[str, object] = field(default_factory=dict)

    @property
    def on_platform(self) -> bool:
        """Whether this fault was done to the platform rather than a station."""
        return self.target.startswith("platform:")


def read_fault_ledger(lines: Iterable[str]) -> tuple[InjectedFault, ...]:
    """Fold a ledger's lines into one fault per window, in the order they opened.

    Read independently of ``meridian_sim.ledger``, which the platform cannot
    import (D-138); ``tests/unit/test_reliability_faults.py`` reads a ledger the
    simulator wrote, so the two readers cannot drift apart unseen.

    Raises:
        FaultLedgerError: A line is not JSON, not this version, names an unknown
            event, or closes or acts on a window that is not open. Refused
            rather than skipped: a verdict against part of the truth is a
            verdict against a different run.
    """
    folding = _Folding()
    for number, raw in enumerate(lines, start=1):
        if raw.strip():
            folding.take(_parse_line(raw, number), number)
    return tuple(folding.faults)


class _Folding:
    """A ledger part-read: the windows so far, and which of them are open."""

    def __init__(self) -> None:
        self.faults: list[InjectedFault] = []
        self._open: dict[tuple[str, str, str], int] = {}

    def take(self, line: Mapping[str, object], number: int) -> None:
        """Apply one line to the windows read so far."""
        key = (
            _text(line, "run_id", number),
            _text(line, "target", number),
            _text(line, "kind", number),
        )
        at = _instant(_text(line, "at", number), number)
        event = line.get("event")
        if event == "open":
            self._opened(key, at, line, number)
        elif event in {"close", "act"}:
            if key not in self._open:
                raise FaultLedgerError(f"line {number}: {event} of an unopened {key}")
            index = self._open[key]
            if event == "close":
                self.faults[index] = replace(self.faults[index], closed_at=at)
                del self._open[key]
            else:
                current = self.faults[index]
                ids = _ids(line, number)
                self.faults[index] = replace(
                    current,
                    assignment_ids=current.assignment_ids + ids,
                    acted_at={**{one: at for one in ids}, **current.acted_at},
                )
        else:
            raise FaultLedgerError(f"line {number}: unknown event {event!r}")

    def _opened(
        self,
        key: tuple[str, str, str],
        at: datetime,
        line: Mapping[str, object],
        number: int,
    ) -> None:
        if key in self._open:
            raise FaultLedgerError(f"line {number}: {key} opened twice")
        self._open[key] = len(self.faults)
        station_id = line.get("station_id")
        detail = line.get("detail", {})
        self.faults.append(
            InjectedFault(
                run_id=key[0],
                target=key[1],
                kind=key[2],
                opened_at=at,
                station_id=station_id if isinstance(station_id, str) else None,
                detail=detail if isinstance(detail, dict) else {},
            )
        )


def _ids(line: Mapping[str, object], number: int) -> tuple[str, ...]:
    """The assignment ids an ``act`` line names."""
    ids = line.get("assignment_ids", [])
    if not isinstance(ids, list):
        raise FaultLedgerError(f"line {number}: assignment_ids is not a list")
    return tuple(str(one) for one in ids)


def _parse_line(raw: str, number: int) -> dict[str, object]:
    """One ledger line, parsed and checked to be this module's format."""
    try:
        line = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FaultLedgerError(f"line {number}: not JSON: {exc}") from exc
    if not isinstance(line, dict) or line.get("ledger") != LEDGER_VERSION:
        raise FaultLedgerError(f"line {number}: not a version {LEDGER_VERSION} line")
    return line


def _text(line: Mapping[str, object], name: str, number: int) -> str:
    value = line.get(name)
    if not isinstance(value, str):
        raise FaultLedgerError(f"line {number}: {name} is missing or not text")
    return value


def _instant(text: str, number: int) -> datetime:
    try:
        at = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FaultLedgerError(f"line {number}: bad instant {text!r}") from exc
    if at.tzinfo is None:
        raise FaultLedgerError(f"line {number}: instant {text!r} has no zone")
    return at
