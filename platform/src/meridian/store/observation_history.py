"""Recent observations, as the public API lists them.

Read from ``observations_current`` — one row per assignment, its latest revision
(D-015) — so a report corrected twice is listed once, as corrected.

**The projection leaves out what is not for publication.** ``first_detection_at``
and ``doppler_samples`` are exact functions of the pass geometry, and publishing
them would undo D-093's coarsening of the window they sit in. ``client_notes`` is
free text from the station and ``products_json`` names files on it; neither has
been reviewed for publication. None of them is selected, so none can leak.

**Products are published from their rows, and only three things about each**
(D-176):
- the sha256, which is the identity Stage 30 refers to;
- the size;
- the kind, but only when it is a short lowercase token, and ``other``
  otherwise, because a kind is text a station chose.

A product's ``uri`` is where one station keeps a file, and is never selected.

Paged newest first on ``(started_at, assignment_id)``, with a cursor naming only
the assignment id.

Reference: docs/DATA-MODEL.md ``observations``; docs/DECISIONS.md D-015, D-085,
D-093, D-176.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = ["HistoricObservation", "PublishedProduct", "find_recent_observations"]

PUBLISHED_KIND = "^[a-z][a-z0-9_-]{0,31}$"
"""A kind published as it was declared. Anything else is published as ``other``."""


@dataclass(frozen=True, slots=True)
class PublishedProduct:
    """One product of an observation, as much of it as is published."""

    kind: str
    sha256: str
    """Lowercase hexadecimal."""
    size_bytes: int | None


@dataclass(frozen=True, slots=True)
class HistoricObservation:
    """The current revision of one observation, without its private parts."""

    observation_id: str
    assignment_id: str
    revision: int
    station_id: str
    satellite_id: str
    started_at: datetime
    ended_at: datetime
    outcome: str
    signal_detected: bool
    peak_snr_db: float | None
    provenance: str
    submitted_at: datetime
    simulated: bool
    products: tuple[PublishedProduct, ...] = ()
    """In the order the station declared them."""


def find_recent_observations(
    conn: Connection,
    *,
    station_id: str | None,
    before_assignment_id: str | None,
    limit: int,
) -> list[HistoricObservation]:
    """One page of observations, most recently started first.

    Args:
        conn: An open connection. Read-only; opens no transaction of its own.
        station_id: Only this station's observations, or all when ``None``.
        before_assignment_id: The last observation's assignment on the previous
            page, or ``None`` for the first.
        limit: The most rows to return; callers ask for one more than they need.

    Returns:
        Observations in descending ``(started_at, assignment_id)`` order. A
        soft-deleted station's are excluded.
    """
    with conn.cursor(row_factory=class_row(_Row)) as cur:
        cur.execute(
            """
            select o.observation_id, o.assignment_id, o.revision, o.station_id,
                   o.satellite_id, o.started_at, o.ended_at, o.outcome,
                   o.signal_detected, o.peak_snr_db, o.provenance,
                   o.submitted_at, o.simulated,
                   coalesce((
                     select jsonb_agg(jsonb_build_array(
                              case when p.kind ~ %(kind)s then p.kind
                                   else 'other' end,
                              encode(p.sha256, 'hex'),
                              p.size_bytes)
                            order by p.element_index)
                     from products p
                     where p.assignment_id = o.assignment_id
                       and p.revision = o.revision
                   ), '[]'::jsonb) as products
            from observations_current o
            join stations s on s.station_id = o.station_id
            where s.deleted_at is null
              and (%(station_id)s::text is null or o.station_id = %(station_id)s)
              and (
                %(before)s::text is null
                or (o.started_at, o.assignment_id) < (
                  select started_at, assignment_id from observations_current
                  where assignment_id = %(before)s
                )
              )
            order by o.started_at desc, o.assignment_id desc
            limit %(limit)s
            """,
            {
                "station_id": station_id,
                "before": before_assignment_id,
                "limit": limit,
                "kind": PUBLISHED_KIND,
            },
        )
        rows = cur.fetchall()
    return [one.published() for one in rows]


@dataclass(frozen=True, slots=True)
class _Row:
    """A fetched row, its products still the ``[kind, sha256, size]`` triples."""

    observation_id: str
    assignment_id: str
    revision: int
    station_id: str
    satellite_id: str
    started_at: datetime
    ended_at: datetime
    outcome: str
    signal_detected: bool
    peak_snr_db: float | None
    provenance: str
    submitted_at: datetime
    simulated: bool
    products: list[list[str | int | None]]

    def published(self) -> HistoricObservation:
        return HistoricObservation(
            observation_id=self.observation_id,
            assignment_id=self.assignment_id,
            revision=self.revision,
            station_id=self.station_id,
            satellite_id=self.satellite_id,
            started_at=self.started_at,
            ended_at=self.ended_at,
            outcome=self.outcome,
            signal_detected=self.signal_detected,
            peak_snr_db=self.peak_snr_db,
            provenance=self.provenance,
            submitted_at=self.submitted_at,
            simulated=self.simulated,
            products=tuple(
                PublishedProduct(
                    kind=str(kind),
                    sha256=str(sha256),
                    size_bytes=None if size is None else int(size),
                )
                for kind, sha256, size in self.products
            ),
        )
