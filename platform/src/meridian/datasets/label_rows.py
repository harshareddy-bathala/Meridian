"""``labels.jsonl``, read back as the labelled passes ``label`` wrote.

Stage 17 fits on an evaluation dataset, and the labels in it are the authority:
they were made once, under a configuration whose hash the manifest holds, and
relabelling to get them again would be a second answer to a question the
dataset already settled. So this reads them back, field for field, as
:meth:`LabelledPass.row` wrote them — and a row that is not that shape is
refused by name.

Reference: docs/DECISIONS.md D-144, D-146, D-156.
"""

from __future__ import annotations

from collections.abc import Mapping

from meridian.datasets.labels import LabelledPass
from meridian.datasets.row_fields import (
    MalformedSnapshotError,
    field,
    flag,
    instant,
    integer,
    jsonl_rows,
    text,
)

__all__ = ["LABELS_FILE", "read_labels"]

LABELS_FILE = "labels.jsonl"


def read_labels(files: Mapping[str, bytes]) -> tuple[LabelledPass, ...]:
    """Every labelled physical pass of an evaluation dataset, in file order.

    Args:
        files: The dataset's files, by name, as read and verified.

    Returns:
        One :class:`LabelledPass` per row.

    Raises:
        MalformedSnapshotError: The file is missing, or a row is not the shape
            ``label`` writes.
    """
    if LABELS_FILE not in files:
        message = f"the dataset has no {LABELS_FILE}; is it an evaluation dataset?"
        raise MalformedSnapshotError(message)
    return tuple(_labelled(one) for one in jsonl_rows(files[LABELS_FILE], LABELS_FILE))


def _labelled(row: Mapping[str, object]) -> LabelledPass:
    return LabelledPass(
        pass_id=integer(row, "pass_id"),
        pass_ids=tuple(_integers(row, "pass_ids")),
        station_id=text(row, "station_id"),
        satellite_id=text(row, "satellite_id"),
        aos=instant(row, "aos"),
        los=instant(row, "los"),
        label=_optional_text(row, "label"),
        exclusion_reason=_optional_text(row, "exclusion_reason"),
        source_outcome=_optional_text(row, "source_outcome"),
        listening_confirmed=None
        if field(row, "listening_confirmed") is None
        else flag(row, "listening_confirmed"),
        scheduled_by=tuple(_optional_texts(row, "scheduled_by")),
        simulated=flag(row, "simulated"),
    )


def _optional_text(row: Mapping[str, object], name: str) -> str | None:
    return None if field(row, name) is None else text(row, name)


def _list(row: Mapping[str, object], name: str) -> list[object]:
    value = field(row, name)
    if not isinstance(value, list):
        message = f"{name} is {value!r}, not a list"
        raise MalformedSnapshotError(message)
    return value


def _integers(row: Mapping[str, object], name: str) -> list[int]:
    return [integer({name: one}, name) for one in _list(row, name)]


def _optional_texts(row: Mapping[str, object], name: str) -> list[str | None]:
    return [_optional_text({name: one}, name) for one in _list(row, name)]
