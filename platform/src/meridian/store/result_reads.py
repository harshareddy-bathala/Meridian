"""Every settled pass across the network, and what became of it.

One read: the stored classifications under the deployment's method and
configuration (D-182), newest first, each with its report's outcome, whether a
station was confirmed listening, and the cause of its loss where one was
diagnosed under the deployment's diagnosis (D-272). It decides nothing; what a
class means for SC-4 is ``meridian.reliability``'s.

Reference: docs/DECISIONS.md D-182, D-272, D-365.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = ["ListedResult", "find_results"]


@dataclass(frozen=True, slots=True)
class ListedResult:
    """One settled pass and its classification."""

    classification_id: int
    assignment_id: str
    station_id: str
    satellite_id: str
    window_start: datetime
    window_end: datetime
    classification: str
    listening_confirmed: bool
    outcome: str | None
    simulated: bool
    diagnosis_id: int | None
    cause: str | None


def find_results(  # noqa: PLR0913 — one query's parameters, each by name
    conn: Connection,
    *,
    classified_under: tuple[str, bytes],
    diagnosed_under: tuple[str, bytes],
    station_id: str | None,
    before_classification_id: int | None,
    limit: int,
) -> list[ListedResult]:
    """Classified passes, the most recently classified first.

    Args:
        conn: An open connection. Read-only.
        classified_under: The deployment's classification method and hash.
        diagnosed_under: The deployment's diagnosis method and hash. A pass's
            latest diagnosis under them gives its cause; another
            configuration's is its own record.
        station_id: Only this station's passes, or all when ``None``.
        before_classification_id: The last id on the previous page, or
            ``None`` for the first.
        limit: The most rows to return; callers ask for one more than they need.

    Returns:
        Results in descending ``classification_id`` order. A pass is classified
        once its settle margin has passed, in window order within a round, so
        this is close to newest first. A soft-deleted station's are excluded.
    """
    classification_method, classification_sha = classified_under
    diagnosis_method, diagnosis_sha = diagnosed_under
    with conn.cursor(row_factory=class_row(ListedResult)) as cur:
        cur.execute(
            """
            select c.classification_id, c.assignment_id, c.station_id,
                   c.satellite_id, c.window_start, c.window_end, c.classification,
                   coalesce((c.evidence ->> 'listening_confirmed')::boolean, false)
                       as listening_confirmed,
                   c.evidence -> 'report' ->> 'outcome' as outcome,
                   c.simulated, d.diagnosis_id, d.cause
            from pass_classifications c
            join stations s on s.station_id = c.station_id
            left join lateral (
                select one.diagnosis_id, one.cause
                from loss_diagnoses one
                -- assignment_id first: the unique key's index leads with it.
                where one.assignment_id = c.assignment_id
                  and one.classification_id = c.classification_id
                  and one.method = %(diagnosis_method)s
                  and one.config_sha256 = %(diagnosis_sha)s
                order by coalesce(one.revision, 0) desc, one.diagnosis_id desc
                limit 1
            ) d on true
            where s.deleted_at is null
              and c.method = %(classification_method)s
              and c.config_sha256 = %(classification_sha)s
              and (%(station_id)s::text is null or c.station_id = %(station_id)s)
              and (%(before)s::bigint is null or c.classification_id < %(before)s)
            order by c.classification_id desc
            limit %(limit)s
            """,
            {
                "classification_method": classification_method,
                "classification_sha": classification_sha,
                "diagnosis_method": diagnosis_method,
                "diagnosis_sha": diagnosis_sha,
                "station_id": station_id,
                "before": before_classification_id,
                "limit": limit,
            },
        )
        return cur.fetchall()
