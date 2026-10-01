"""Reception ratings — the label "usable", rated blind (D-106, D-260).

Writes and reads ``reception_ratings``
(``deploy/migrations/sql/0027_reception_ratings.sql``).

**The queue is blind by construction.** A rater sees which reception it is,
when, and where its products are kept. They never see the outcome, the SNR,
the frame counts, the noise floor or a verdict. The queue's ``select`` is the
only place that decides what a rater sees, so it names its columns and reads
no others. ``tests/unit/test_reception_ratings.py`` pins that list.

**What can be rated.** A measured observation revision that declared at least
one product. A simulated reception is refused: no simulated row is ever
training input (D-078, D-105), so a rating of one would be work for nothing.
A reception with no product has nothing to look at, and the labeller calls it
unusable without a rating (D-260).

Only the current revision is queued. An earlier revision can still be rated
by naming it, because a verdict belongs to one revision (D-104).

Reference: docs/DECISIONS.md D-078, D-104, D-105, D-260.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = [
    "NewRating",
    "QueuedProduct",
    "QueuedReception",
    "RatingRefusedError",
    "insert_rating",
    "unrated_receptions",
]

_QUEUE = """
    select o.assignment_id, o.revision, o.station_id, o.satellite_id,
           o.started_at, p.kind, p.sha256, p.uri
    from observations_current o
    join products p
      on p.assignment_id = o.assignment_id and p.revision = o.revision
    where not o.simulated
      and not exists (
          select 1 from reception_ratings r
          where r.assignment_id = o.assignment_id and r.revision = o.revision)
    order by o.started_at, o.assignment_id, p.element_index
"""

_TARGET = """
    select o.started_at, o.station_id, o.simulated,
           exists (
               select 1 from products p
               where p.assignment_id = o.assignment_id
                 and p.revision = o.revision) as has_products
    from observations o
    where o.assignment_id = %(assignment_id)s and o.revision = %(revision)s
"""

_INSERT = """
    insert into reception_ratings (
        assignment_id, revision, observation_started_at, station_id,
        usable, rubric, rater, simulated
    ) values (
        %(assignment_id)s, %(revision)s, %(started_at)s, %(station_id)s,
        %(usable)s, %(rubric)s, %(rater)s, false
    )
    returning id
"""


@dataclass(frozen=True, slots=True)
class _QueueRow:
    assignment_id: str
    revision: int
    station_id: str
    satellite_id: str
    started_at: datetime
    kind: str
    sha256: bytes
    uri: str | None


@dataclass(frozen=True, slots=True)
class _Target:
    started_at: datetime
    station_id: str
    simulated: bool
    has_products: bool


@dataclass(frozen=True, slots=True)
class _Inserted:
    id: int


class RatingRefusedError(ValueError):
    """A rating that cannot be recorded for this revision, with the reason."""


@dataclass(frozen=True, slots=True)
class NewRating:
    """One person's answer about one observation revision."""

    assignment_id: str
    revision: int
    usable: bool
    rubric: str
    """The written instructions the rater followed."""

    rater: str
    """The rater's tag, not a name."""


@dataclass(frozen=True, slots=True)
class QueuedProduct:
    """One product of a queued reception: what it is, and where it is kept."""

    kind: str
    sha256: bytes
    uri: str | None


@dataclass(frozen=True, slots=True)
class QueuedReception:
    """A reception waiting for a rating: its identity and its products only."""

    assignment_id: str
    revision: int
    station_id: str
    satellite_id: str
    started_at: datetime
    products: tuple[QueuedProduct, ...]


def unrated_receptions(conn: Connection) -> list[QueuedReception]:
    """Every current measured revision with products and no rating, oldest first.

    Args:
        conn: An open connection.

    Returns:
        The receptions, each with its products in submitted order.
    """
    with conn.cursor(row_factory=class_row(_QueueRow)) as cur:
        cur.execute(_QUEUE)
        rows = cur.fetchall()
    queued: dict[tuple[str, int], QueuedReception] = {}
    for row in rows:
        key = (row.assignment_id, row.revision)
        product = QueuedProduct(kind=row.kind, sha256=bytes(row.sha256), uri=row.uri)
        held = queued.get(key)
        queued[key] = QueuedReception(
            assignment_id=row.assignment_id,
            revision=row.revision,
            station_id=row.station_id,
            satellite_id=row.satellite_id,
            started_at=row.started_at,
            products=(*(held.products if held else ()), product),
        )
    return list(queued.values())


def insert_rating(conn: Connection, rating: NewRating) -> int:
    """Record one rating of one observation revision.

    Args:
        conn: An open connection; the caller commits.
        rating: The revision rated, and the answer.

    Returns:
        The new row's id.

    Raises:
        RatingRefusedError: No such revision, a simulated reception, or one
            with no product to rate.
    """
    assignment_id, revision = rating.assignment_id, rating.revision
    params = {"assignment_id": assignment_id, "revision": revision}
    with conn.cursor(row_factory=class_row(_Target)) as cur:
        cur.execute(_TARGET, params)
        found = cur.fetchone()
    if found is None:
        message = f"no observation {assignment_id} revision {revision}"
        raise RatingRefusedError(message)
    if found.simulated:
        message = (
            f"{assignment_id} revision {revision} is simulated, and no"
            " simulated reception is ever a label (D-078, D-105)"
        )
        raise RatingRefusedError(message)
    if not found.has_products:
        message = (
            f"{assignment_id} revision {revision} declared no product; with"
            " nothing to rate it is unusable by definition (D-260)"
        )
        raise RatingRefusedError(message)
    with conn.cursor(row_factory=class_row(_Inserted)) as cur:
        cur.execute(
            _INSERT,
            params
            | {
                "started_at": found.started_at,
                "station_id": found.station_id,
                "usable": rating.usable,
                "rubric": rating.rubric,
                "rater": rating.rater,
            },
        )
        inserted = cur.fetchone()
    if inserted is None:
        message = "the insert returned no id"
        raise RuntimeError(message)
    return inserted.id
