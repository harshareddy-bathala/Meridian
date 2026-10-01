"""``reception_ratings`` — the label "usable", rated blind (D-106, D-260).

Marked ``integration`` by the directory hook. A database shows what a unit test
cannot: the queue reads only measured current revisions with products, a rating
is refused for what cannot be rated, a second rating appends, and ratings
travel with their observation into a raw snapshot and come back as labels.

Reference: docs/DECISIONS.md D-104, D-260.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.datasets.export import export_snapshot, read_snapshot  # noqa: E402
from meridian.datasets.publish import read_directory  # noqa: E402
from meridian.datasets.usable_labels import (  # noqa: E402
    NO_PRODUCT,
    RATING,
    UNRATED,
    read_usable_labels,
)
from meridian.registry import ListeningQuery  # noqa: E402
from meridian.store.ratings import (  # noqa: E402
    NewRating,
    RatingRefusedError,
    insert_rating,
    unrated_receptions,
)

pytestmark = pytest.mark.integration

SINCE = datetime(2026, 8, 1, tzinfo=UTC)
CREATED = datetime(2026, 9, 23, 7, 0, tzinfo=UTC)


class Listening:
    def was_listening(self, query: ListeningQuery) -> bool:
        return query is not None


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes — see test_store_observations.py's twin."""
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def root(tmp_path: Path) -> Iterator[Path]:
    yield tmp_path
    for path in sorted(tmp_path.rglob("*"), reverse=True):
        path.chmod(0o700 if path.is_dir() else 0o600)


def product(
    conn: Any, assignment_id: str, *, revision: int = 1, index: int = 0
) -> None:
    conn.execute(
        "insert into products (assignment_id, revision, observation_started_at,"
        " station_id, element_index, kind, sha256, size_bytes, uri, simulated)"
        " select assignment_id, revision, started_at, station_id, %s, 'image',"
        " %s, 2048, 'station:products/ab', simulated"
        " from observations where assignment_id = %s and revision = %s",
        (index, bytes([index + 1]) * 32, assignment_id, revision),
    )


@pytest.fixture
def world(rollback: Any, schedule_rows: Any) -> Any:
    """Four receptions on one measured station and one simulated reception.

    ``as_image`` decoded with two products, ``as_empty`` decoded with none,
    ``as_quiet`` heard nothing, ``as_twice`` was resubmitted with a product on
    its second revision only, and ``as_sim`` is simulated with a product.
    """
    measured = schedule_rows.station("st_rated", simulated=False)
    simulated = schedule_rows.station("st_sim", simulated=True)
    element_set = schedule_rows.satellite()
    for minute, (name, station) in enumerate(
        (
            ("as_image", measured),
            ("as_empty", measured),
            ("as_quiet", measured),
            ("as_twice", measured),
            ("as_sim", simulated),
        )
    ):
        aos = datetime(2026, 8, 14, 9, 0, tzinfo=UTC).replace(minute=minute * 11)
        pass_id = schedule_rows.pass_(station, aos, element_set_id=element_set)
        schedule_rows.assignment(name, pass_id)
    schedule_rows.observation("as_image")
    product(rollback, "as_image")
    product(rollback, "as_image", index=1)
    schedule_rows.observation("as_empty")
    schedule_rows.observation("as_quiet", outcome="no_signal")
    schedule_rows.observation("as_twice")
    schedule_rows.observation("as_twice", revision=2)
    product(rollback, "as_twice", revision=2)
    schedule_rows.observation("as_sim")
    product(rollback, "as_sim")
    return rollback


def rate(conn: Any, assignment_id: str, revision: int = 1, *, usable: bool) -> int:
    return insert_rating(
        conn, NewRating(assignment_id, revision, usable, rubric="usable-1", rater="hr")
    )


def test_the_queue_is_measured_current_revisions_with_products(world: Any) -> None:
    queued = unrated_receptions(world)

    assert [(one.assignment_id, one.revision) for one in queued] == [
        ("as_image", 1),
        ("as_twice", 2),
    ]
    image = queued[0]
    assert [one.kind for one in image.products] == ["image", "image"]
    assert image.products[0].sha256 == bytes([1]) * 32
    assert image.products[0].uri == "station:products/ab"


def test_a_rated_reception_leaves_the_queue(world: Any) -> None:
    rate(world, "as_image", usable=True)

    assert [one.assignment_id for one in unrated_receptions(world)] == ["as_twice"]


@pytest.mark.parametrize(
    ("assignment_id", "revision", "reason"),
    [
        ("as_empty", 1, "declared no product"),
        ("as_quiet", 1, "declared no product"),
        ("as_sim", 1, "is simulated"),
        ("as_nothing", 1, "no observation"),
        ("as_twice", 3, "no observation"),
    ],
)
def test_what_cannot_be_rated_is_refused_by_name(
    world: Any, assignment_id: str, revision: int, reason: str
) -> None:
    with pytest.raises(RatingRefusedError, match=reason):
        rate(world, assignment_id, revision, usable=True)


def test_a_second_rating_appends_and_keeps_the_first(world: Any) -> None:
    first = rate(world, "as_image", usable=True)
    second = rate(world, "as_image", usable=False)

    rows = world.execute(
        "select id, usable, simulated from reception_ratings"
        " where assignment_id = 'as_image' order by id"
    ).fetchall()
    assert rows == [(first, True, False), (second, False, False)]


@pytest.mark.parametrize(
    ("column", "value"),
    [("rater", "Harsha Reddy"), ("rater", ""), ("rubric", "Usable 1")],
)
def test_a_rater_is_a_tag_and_a_rubric_a_slug(
    world: Any, column: str, value: str
) -> None:
    """A name with a space or capitals is refused: §16 holds no personal data."""
    values = {"rater": "hr", "rubric": "usable-1"} | {column: value}
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_rating(world, NewRating("as_image", 1, usable=True, **values))


def test_ratings_travel_into_a_snapshot_and_come_back_as_labels(
    world: Any, root: Path
) -> None:
    rate(world, "as_image", usable=False)
    rate(world, "as_image", usable=True)

    read = read_snapshot(world, Listening(), since=SINCE)
    published = export_snapshot(read, root=root, created_at=CREATED)
    files = read_directory(published.path).files
    labels = read_usable_labels(files)

    assert labels[("as_image", 1)].usable is True
    assert labels[("as_image", 1)].source == RATING
    assert labels[("as_image", 1)].rubric == "usable-1"
    assert labels[("as_empty", 1)].source == NO_PRODUCT
    assert labels[("as_quiet", 1)].usable is False
    assert labels[("as_twice", 1)].source == NO_PRODUCT
    assert labels[("as_twice", 2)].source == UNRATED
    assert labels[("as_twice", 2)].usable is None
    assert published.manifest.counts["reception_ratings.measured"] == 2
    assert published.manifest.counts["reception_ratings.simulated"] == 0
