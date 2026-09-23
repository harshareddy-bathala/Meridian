"""A raw snapshot's rows, as the typed records the labeller reasons about.

The labeller reads JSON Lines, so a timestamp arrives as ``"...Z"`` text and a
boolean as whatever JSON says. Converting once, here, keeps the rules in
:mod:`meridian.datasets.labels` about passes and receptions rather than about
parsing — and a row with the wrong shape is refused by name instead of
producing a label from a ``KeyError`` somebody catches.

Only the columns a label depends on are read. The rest of each row stays in
the raw snapshot for Stage 17's features, and is not this module's business.

Reference: docs/DECISIONS.md D-143, D-146, D-147.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

__all__ = [
    "ArchiveReception",
    "AssignmentRow",
    "HeartbeatRow",
    "MalformedSnapshotError",
    "ObservationRow",
    "PassRow",
    "SnapshotRows",
    "parse_rows",
]


class MalformedSnapshotError(ValueError):
    """A raw snapshot row without a field a label needs, or with the wrong type."""


@dataclass(frozen=True, slots=True)
class PassRow:
    """A geometrically available pass: the unit every label is about."""

    pass_id: int
    station_id: str
    satellite_id: str
    aos: datetime
    los: datetime
    simulated: bool


@dataclass(frozen=True, slots=True)
class AssignmentRow:
    """One scheduling decision about a pass."""

    assignment_id: str
    pass_id: int
    station_id: str
    start_at: datetime
    end_at: datetime
    decision: str
    state: str
    model_config: str | None
    simulated: bool


@dataclass(frozen=True, slots=True)
class ObservationRow:
    """One revision of a station's report on an assignment."""

    assignment_id: str
    revision: int
    outcome: str
    simulated: bool


@dataclass(frozen=True, slots=True)
class HeartbeatRow:
    """A heartbeat received inside some assignment's window."""

    station_id: str
    received_at: datetime


@dataclass(frozen=True, slots=True)
class ArchiveReception:
    """An archive's reception, keyed the way D-147 matches it to our satellites."""

    satellite_key: str
    satellite_key_kind: str
    started_at: datetime
    archive_outcome: str


@dataclass(frozen=True, slots=True)
class SnapshotRows:
    """Every row a label can depend on."""

    passes: tuple[PassRow, ...]
    assignments: tuple[AssignmentRow, ...]
    observations: tuple[ObservationRow, ...]
    heartbeats: tuple[HeartbeatRow, ...]
    listening: Mapping[str, bool]
    """Assignment id to the registry's frozen answer (D-145)."""

    archive: tuple[ArchiveReception, ...]


def parse_rows(files: Mapping[str, bytes]) -> SnapshotRows:
    """Read the files a label needs out of a raw snapshot.

    Args:
        files: The snapshot's files, by name, as read and verified.

    Returns:
        The typed rows.

    Raises:
        MalformedSnapshotError: A needed file is missing, or a row lacks a
            field or has one of the wrong type.
    """
    return SnapshotRows(
        passes=tuple(_pass(one) for one in _lines(files, "passes")),
        assignments=tuple(_assignment(one) for one in _lines(files, "assignments")),
        observations=tuple(
            ObservationRow(
                assignment_id=_text(one, "assignment_id"),
                revision=_int(one, "revision"),
                outcome=_text(one, "outcome"),
                simulated=_bool(one, "simulated"),
            )
            for one in _lines(files, "observations")
        ),
        heartbeats=tuple(
            HeartbeatRow(
                station_id=_text(one, "station_id"),
                received_at=_instant(one, "received_at"),
            )
            for one in _lines(files, "heartbeats")
        ),
        listening={
            _text(one, "assignment_id"): _bool(one, "listening_confirmed")
            for one in _lines(files, "listening")
        },
        archive=tuple(
            ArchiveReception(
                satellite_key=_text(one, "satellite_key"),
                satellite_key_kind=_text(one, "satellite_key_kind"),
                started_at=_instant(one, "started_at"),
                archive_outcome=_text(one, "archive_outcome"),
            )
            for one in _lines(files, "archive_observations")
        ),
    )


def _pass(row: Mapping[str, object]) -> PassRow:
    return PassRow(
        pass_id=_int(row, "id"),
        station_id=_text(row, "station_id"),
        satellite_id=_text(row, "satellite_id"),
        aos=_instant(row, "aos"),
        los=_instant(row, "los"),
        simulated=_bool(row, "simulated"),
    )


def _assignment(row: Mapping[str, object]) -> AssignmentRow:
    config = row.get("model_config")
    if config is not None and not isinstance(config, str):
        message = f"model_config is {config!r}, not text or null"
        raise MalformedSnapshotError(message)
    return AssignmentRow(
        assignment_id=_text(row, "assignment_id"),
        pass_id=_int(row, "pass_id"),
        station_id=_text(row, "station_id"),
        start_at=_instant(row, "start_at"),
        end_at=_instant(row, "end_at"),
        decision=_text(row, "decision"),
        state=_text(row, "state"),
        model_config=config,
        simulated=_bool(row, "simulated"),
    )


def _lines(files: Mapping[str, bytes], name: str) -> list[Mapping[str, object]]:
    """Every row of one file, parsed."""
    try:
        data = files[f"{name}.jsonl"]
    except KeyError as exc:
        message = f"the raw snapshot has no {name}.jsonl"
        raise MalformedSnapshotError(message) from exc
    rows: list[Mapping[str, object]] = []
    for number, line in enumerate(data.splitlines(), start=1):
        decoded: object = json.loads(line)
        if not isinstance(decoded, dict):
            message = f"{name}.jsonl line {number} is not an object"
            raise MalformedSnapshotError(message)
        rows.append(decoded)
    return rows


def _field(row: Mapping[str, object], name: str) -> object:
    try:
        return row[name]
    except KeyError as exc:
        message = f"a row has no {name!r}"
        raise MalformedSnapshotError(message) from exc


def _text(row: Mapping[str, object], name: str) -> str:
    value = _field(row, name)
    if not isinstance(value, str):
        message = f"{name} is {value!r}, not text"
        raise MalformedSnapshotError(message)
    return value


def _int(row: Mapping[str, object], name: str) -> int:
    value = _field(row, name)
    if isinstance(value, bool) or not isinstance(value, int):
        message = f"{name} is {value!r}, not an integer"
        raise MalformedSnapshotError(message)
    return value


def _bool(row: Mapping[str, object], name: str) -> bool:
    value = _field(row, name)
    if not isinstance(value, bool):
        message = f"{name} is {value!r}, not true or false"
        raise MalformedSnapshotError(message)
    return value


def _instant(row: Mapping[str, object], name: str) -> datetime:
    value = _text(row, name)
    if not value.endswith("Z"):
        message = f"{name} is {value!r}, not a UTC timestamp"
        raise MalformedSnapshotError(message)
    return datetime.fromisoformat(value)
