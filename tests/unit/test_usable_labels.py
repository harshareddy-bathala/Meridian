"""``read_usable_labels`` — D-260's label rules, read from snapshot files.

Reference: docs/DECISIONS.md D-106, D-260.
"""

from __future__ import annotations

import json

import pytest

from meridian.datasets.row_fields import MalformedSnapshotError
from meridian.datasets.usable_labels import (
    NO_PRODUCT,
    RATING,
    UNRATED,
    UsableLabel,
    read_usable_labels,
)


def jsonl(*rows: dict[str, object]) -> bytes:
    return b"".join(json.dumps(one).encode() + b"\n" for one in rows)


def observation(assignment_id: str, revision: int = 1) -> dict[str, object]:
    return {"assignment_id": assignment_id, "revision": revision}


def rating(
    rating_id: int,
    assignment_id: str,
    usable: bool,
    *,
    revision: int = 1,
    at: str = "2026-08-14T10:00:00Z",
) -> dict[str, object]:
    return {
        "id": rating_id,
        "assignment_id": assignment_id,
        "revision": revision,
        "usable": usable,
        "rubric": "usable-1",
        "rated_at": at,
    }


def files(
    observations: list[dict[str, object]],
    products: list[dict[str, object]],
    ratings: list[dict[str, object]] | None,
) -> dict[str, bytes]:
    held = {
        "observations.jsonl": jsonl(*observations),
        "products.jsonl": jsonl(*products),
    }
    if ratings is not None:
        held["reception_ratings.jsonl"] = jsonl(*ratings)
    return held


def test_no_product_is_unusable_whatever_a_rating_says() -> None:
    labels = read_usable_labels(
        files([observation("as_a")], [], [rating(1, "as_a", usable=True)])
    )

    assert labels[("as_a", 1)] == UsableLabel(usable=False, source=NO_PRODUCT)


def test_the_latest_rating_is_the_label() -> None:
    labels = read_usable_labels(
        files(
            [observation("as_a")],
            [observation("as_a")],
            [
                rating(2, "as_a", usable=False, at="2026-08-15T10:00:00Z"),
                rating(1, "as_a", usable=True, at="2026-08-14T10:00:00Z"),
            ],
        )
    )

    assert labels[("as_a", 1)] == UsableLabel(
        usable=False, source=RATING, rubric="usable-1"
    )


def test_ratings_at_the_same_instant_are_ordered_by_id() -> None:
    labels = read_usable_labels(
        files(
            [observation("as_a")],
            [observation("as_a")],
            [rating(9, "as_a", usable=True), rating(3, "as_a", usable=False)],
        )
    )

    assert labels[("as_a", 1)].usable is True


def test_a_product_and_no_rating_is_unrated_not_guessed() -> None:
    held = files([observation("as_a")], [observation("as_a")], [])

    labels = read_usable_labels(held)

    assert labels[("as_a", 1)] == UsableLabel(usable=None, source=UNRATED)


def test_each_revision_has_its_own_label() -> None:
    labels = read_usable_labels(
        files(
            [observation("as_a"), observation("as_a", 2)],
            [observation("as_a", 2)],
            [rating(1, "as_a", usable=True, revision=2)],
        )
    )

    assert labels[("as_a", 1)].source == NO_PRODUCT
    assert labels[("as_a", 2)].source == RATING


def test_a_snapshot_from_before_0027_reads_as_nothing_rated() -> None:
    held = files([observation("as_a")], [observation("as_a")], None)

    labels = read_usable_labels(held)

    assert labels[("as_a", 1)].source == UNRATED


def test_a_rating_of_a_revision_not_held_is_refused() -> None:
    with pytest.raises(MalformedSnapshotError, match="as_b revision 1"):
        read_usable_labels(
            files([observation("as_a")], [], [rating(1, "as_b", usable=True)])
        )


@pytest.mark.parametrize("missing", ["observations.jsonl", "products.jsonl"])
def test_a_missing_file_is_refused_by_name(missing: str) -> None:
    held = files([observation("as_a")], [], [])
    del held[missing]

    with pytest.raises(MalformedSnapshotError, match=missing):
        read_usable_labels(held)
