"""Reading one field of one snapshot row, the same way everywhere.

A snapshot file is JSON Lines, so a timestamp arrives as ``"...Z"`` text and a
boolean as whatever JSON says. The labeller (:mod:`snapshot_rows`), the labels
reader (:mod:`label_rows`) and the prediction module's feature rows all read
such rows, and each refusal has to name the field it found wrong. So the
helpers live once, here, and a row with the wrong shape is refused the same
way whichever reader met it.

Reference: docs/DECISIONS.md D-144, D-157.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime

__all__ = [
    "MalformedSnapshotError",
    "field",
    "flag",
    "instant",
    "integer",
    "jsonl_rows",
    "number",
    "optional_instant",
    "optional_number",
    "text",
]


class MalformedSnapshotError(ValueError):
    """A snapshot row without a field a reader needs, or with the wrong type."""


def jsonl_rows(data: bytes, name: str) -> list[Mapping[str, object]]:
    """Every row of one JSON Lines file, each checked to be an object."""
    rows: list[Mapping[str, object]] = []
    for number_, line in enumerate(data.splitlines(), start=1):
        decoded: object = json.loads(line)
        if not isinstance(decoded, dict):
            message = f"{name} line {number_} is not an object"
            raise MalformedSnapshotError(message)
        rows.append(decoded)
    return rows


def field(row: Mapping[str, object], name: str) -> object:
    """The field, or a refusal that names it."""
    try:
        return row[name]
    except KeyError as exc:
        message = f"a row has no {name!r}"
        raise MalformedSnapshotError(message) from exc


def text(row: Mapping[str, object], name: str) -> str:
    """A text field."""
    value = field(row, name)
    if not isinstance(value, str):
        message = f"{name} is {value!r}, not text"
        raise MalformedSnapshotError(message)
    return value


def integer(row: Mapping[str, object], name: str) -> int:
    """An integer field; a boolean is not one."""
    value = field(row, name)
    if isinstance(value, bool) or not isinstance(value, int):
        message = f"{name} is {value!r}, not an integer"
        raise MalformedSnapshotError(message)
    return value


def number(row: Mapping[str, object], name: str) -> float:
    """A numeric field, as a float."""
    value = field(row, name)
    if isinstance(value, bool) or not isinstance(value, int | float):
        message = f"{name} is {value!r}, not a number"
        raise MalformedSnapshotError(message)
    return float(value)


def flag(row: Mapping[str, object], name: str) -> bool:
    """A boolean field."""
    value = field(row, name)
    if not isinstance(value, bool):
        message = f"{name} is {value!r}, not true or false"
        raise MalformedSnapshotError(message)
    return value


def instant(row: Mapping[str, object], name: str) -> datetime:
    """A timestamp field, which canonical JSON always writes in UTC with a Z."""
    value = text(row, name)
    if not value.endswith("Z"):
        message = f"{name} is {value!r}, not a UTC timestamp"
        raise MalformedSnapshotError(message)
    return datetime.fromisoformat(value)


def optional_number(row: Mapping[str, object], name: str) -> float | None:
    """A numeric field that may be null."""
    return None if field(row, name) is None else number(row, name)


def optional_instant(row: Mapping[str, object], name: str) -> datetime | None:
    """A timestamp field that may be null."""
    return None if field(row, name) is None else instant(row, name)
