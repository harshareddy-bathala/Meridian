"""Lost passes and their diagnoses, as the public API lists them.

``meridian.store.loss_diagnoses`` serves the diagnosis run: what is still
undiagnosed, and writing a row once. This module is the reader's side, every
stored diagnosis under the deployment's method and configuration, newest first,
with the classified pass it is a diagnosis of.

**Only the deployment's own diagnoses.** A refit threshold writes every
diagnosis again beside the old ones (D-272), so a list that read every method
would show a pass diagnosed twice. The caller names the method and the hash.

**The latest observation revision only.** A resubmitted reception is diagnosed
again under the same method (D-272), and the newer row is the one that stands.

Windows come back exact; the public model widens them (D-093). Paging is keyset
on ``diagnosis_id``, which the body publishes, so the cursor carries nothing the
body does not.

Reference: docs/DECISIONS.md D-085, D-093, D-272, D-331.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = ["ListedLoss", "find_losses"]


@dataclass(frozen=True, slots=True)
class ListedLoss:
    """One diagnosed loss, with the classified pass it belongs to."""

    diagnosis_id: int
    assignment_id: str
    station_id: str
    satellite_id: str
    window_start: datetime
    window_end: datetime
    classification: str
    cause: str
    candidates_json: list[dict[str, object]]
    method: str
    computed_at: datetime
    simulated: bool


def find_losses(  # noqa: PLR0913 — one query's parameters, each by name
    conn: Connection,
    *,
    method: str,
    config_sha256: bytes,
    station_id: str | None,
    before_diagnosis_id: int | None,
    limit: int,
) -> list[ListedLoss]:
    """One page of diagnosed losses, the most recently diagnosed first.

    Args:
        conn: An open connection. Read-only; opens no transaction of its own.
        method: The deployment's diagnosis method, ``diagnosis-1``.
        config_sha256: The hash of the deployment's ``[diagnosis]`` table.
        station_id: Only this station's losses, or every station's when ``None``.
        before_diagnosis_id: The last diagnosis on the previous page, or
            ``None`` for the first.
        limit: The most rows to return; callers ask for one more than they need.

    Returns:
        Diagnoses in descending ``diagnosis_id`` order. A soft-deleted station's
        are excluded, as every public list excludes them.
    """
    with conn.cursor(row_factory=class_row(ListedLoss)) as cur:
        cur.execute(
            """
            select d.diagnosis_id, d.assignment_id, d.station_id, c.satellite_id,
                   c.window_start, c.window_end, c.classification, d.cause,
                   d.candidates_json, d.method, d.computed_at, d.simulated
            from loss_diagnoses d
            join pass_classifications c on c.classification_id = d.classification_id
            join stations s on s.station_id = d.station_id
            where s.deleted_at is null
              and d.method = %(method)s
              and d.config_sha256 = %(config_sha256)s
              and (%(station_id)s::text is null or d.station_id = %(station_id)s)
              and (%(before)s::bigint is null or d.diagnosis_id < %(before)s)
              and not exists (
                select 1 from loss_diagnoses later
                where later.assignment_id = d.assignment_id
                  and later.method = d.method
                  and later.config_sha256 = d.config_sha256
                  and coalesce(later.revision, 0) > coalesce(d.revision, 0)
              )
            order by d.diagnosis_id desc
            limit %(limit)s
            """,
            {
                "method": method,
                "config_sha256": config_sha256,
                "station_id": station_id,
                "before": before_diagnosis_id,
                "limit": limit,
            },
        )
        return cur.fetchall()
