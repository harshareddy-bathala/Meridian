"""The label "usable" for every observation revision a raw snapshot holds.

D-260 settles D-106. A reception is usable if a person looked at its decoded
product and said so, without seeing the verdict or anything the verdict reads.
The rules, in order:

1. **No product, unusable** (``no_product``). A revision that declared no
   product has nothing anyone could use or rate. This is the one part of the
   label that follows from what the station reported, which is why SC-7 is
   also reported on rated receptions alone (D-260).
2. **The latest rating** (``rating``). Ratings are append-only, and the most
   recent one of a revision is its label, by ``rated_at`` and then id.
3. **Otherwise unrated** (``unrated``), with no label. An unrated reception is
   left out of fitting and scoring. It is never guessed from its outcome.

A product is a row of ``products.jsonl``, the normalised manifest (D-176),
not an element of ``products_json`` that failed ingest's rule, which nobody
could locate to rate.

**A snapshot exported before migration 0027** holds no ratings file. It is read
as no reception rated, not refused, as Stage 25's reader treats a snapshot
made before its interval column.

Reference: docs/DECISIONS.md D-104, D-106, D-176, D-260.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from meridian.datasets.row_fields import (
    MalformedSnapshotError,
    flag,
    instant,
    integer,
    jsonl_rows,
    text,
)

__all__ = [
    "NO_PRODUCT",
    "RATING",
    "UNRATED",
    "UsableLabel",
    "read_usable_labels",
]

NO_PRODUCT = "no_product"
RATING = "rating"
UNRATED = "unrated"

Revision = tuple[str, int]
"""An observation revision: assignment id and revision number."""


@dataclass(frozen=True, slots=True)
class UsableLabel:
    """One revision's label, and where it came from."""

    usable: bool | None
    """``None`` when unrated: no label, so no training or test example."""

    source: str
    """``no_product``, ``rating`` or ``unrated``."""

    rubric: str | None = None
    """The rating instructions followed, for a rated revision."""


def read_usable_labels(files: Mapping[str, bytes]) -> dict[Revision, UsableLabel]:
    """Every observation revision's label, keyed by assignment and revision.

    Args:
        files: A raw snapshot's files, by name, as read and verified.

    Returns:
        One entry per observation revision in ``observations.jsonl``.

    Raises:
        MalformedSnapshotError: The observations or products file is missing,
            a row lacks a field, or a rating names a revision not held.
    """
    revisions = [
        (text(one, "assignment_id"), integer(one, "revision"))
        for one in _lines(files, "observations")
    ]
    with_products = {
        (text(one, "assignment_id"), integer(one, "revision"))
        for one in _lines(files, "products")
    }
    ratings = _latest_ratings(files, set(revisions))
    labels: dict[Revision, UsableLabel] = {}
    for revision in revisions:
        if revision not in with_products:
            labels[revision] = UsableLabel(usable=False, source=NO_PRODUCT)
        elif revision in ratings:
            labels[revision] = ratings[revision]
        else:
            labels[revision] = UsableLabel(usable=None, source=UNRATED)
    return labels


def _latest_ratings(
    files: Mapping[str, bytes], held: set[Revision]
) -> dict[Revision, UsableLabel]:
    """The most recent rating of each revision; none before migration 0027."""
    data = files.get("reception_ratings.jsonl")
    if data is None:
        return {}
    rows = sorted(
        jsonl_rows(data, "reception_ratings.jsonl"),
        key=lambda one: (instant(one, "rated_at"), integer(one, "id")),
    )
    latest: dict[Revision, UsableLabel] = {}
    for one in rows:
        revision = (text(one, "assignment_id"), integer(one, "revision"))
        if revision not in held:
            message = (
                f"a rating names {revision[0]} revision {revision[1]},"
                " which the snapshot does not hold"
            )
            raise MalformedSnapshotError(message)
        latest[revision] = UsableLabel(
            usable=flag(one, "usable"), source=RATING, rubric=text(one, "rubric")
        )
    return latest


def _lines(files: Mapping[str, bytes], name: str) -> list[Mapping[str, object]]:
    """Every row of one file, parsed, or a refusal naming the missing file."""
    try:
        data = files[f"{name}.jsonl"]
    except KeyError as exc:
        message = f"the raw snapshot has no {name}.jsonl"
        raise MalformedSnapshotError(message) from exc
    return jsonl_rows(data, f"{name}.jsonl")
