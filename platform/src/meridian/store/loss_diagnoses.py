"""Loss diagnoses — what to diagnose, and the rows that record each diagnosis.

Reads and writes ``loss_diagnoses``
(``deploy/migrations/sql/0029_loss_diagnoses.sql``).

**What is diagnosed** (D-272). A scheduled assignment the station was left with
— never revoked, never expired — whose pass Stage 20 has classified under the
deployment's classification method and configuration, and whose current
report:
- did not decode;
- decoded, below the partial threshold of the verdict the given method wrote;
- or does not exist, the station having held the work to the end of its window.

**Append-only.** An insert that meets an existing (assignment, revision,
method, configuration) does nothing, so a re-run writes nothing and two writers
cannot disagree about a row.

Reference: docs/DECISIONS.md D-008, D-104, D-171, D-180, D-272.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row
from psycopg.types.json import Jsonb

from meridian.store.stations import Connection

__all__ = [
    "DiagnosisSubject",
    "NewDiagnosis",
    "StoredDiagnosis",
    "find_diagnoses",
    "find_undiagnosed",
    "insert_diagnosis",
]


@dataclass(frozen=True, slots=True)
class DiagnosisSubject:
    """One lost reception, with what the store holds about it directly."""

    assignment_id: str
    station_id: str
    satellite_id: str
    pass_id: int
    element_set_id: int
    start_at: datetime
    end_at: datetime
    aos: datetime
    los: datetime
    centre_freq_hz: int
    mode: str
    timing_uncertainty_s: float | None
    max_elevation_deg: float
    simulated: bool
    classification_id: int
    classification: str
    classification_evidence: dict[str, object]
    revision: int | None
    observation_started_at: datetime | None
    observation_ended_at: datetime | None
    outcome: str | None
    noise_floor_dbfs: float | None
    receiver_gain_db: float | None
    snr_samples: list[dict[str, object]] | None
    probability_usable: float | None
    partial_below: float | None


_UNDIAGNOSED = """
    with classified as (
        select c.classification_id, c.classification, c.evidence,
               unnest(c.assignment_ids) as assignment_id
        from pass_classifications c
        where c.method = %(classification_method)s
          and c.config_sha256 = %(classification_sha256)s
    )
    select a.assignment_id, a.station_id, p.satellite_id, p.id as pass_id,
           p.element_set_id, a.start_at, a.end_at, p.aos, p.los,
           a.centre_freq_hz, a.mode, a.timing_uncertainty_s,
           p.max_elevation_deg, a.simulated,
           c.classification_id, c.classification,
           c.evidence as classification_evidence,
           o.revision, o.started_at as observation_started_at,
           o.ended_at as observation_ended_at, o.outcome,
           o.noise_floor_dbfs, o.receiver_gain_db, o.snr_samples,
           v.probability_usable, v.partial_below
    from classified c
    join assignments a on a.assignment_id = c.assignment_id
    join passes p on p.id = a.pass_id
    left join observations_current o on o.assignment_id = a.assignment_id
    left join reception_verdicts v
           on v.assignment_id = o.assignment_id
          and v.revision = o.revision
          and v.method = %(verdict_method)s
    where a.decision = 'scheduled'
      and a.revoked_reason is null
      and a.state not in ('expired', 'revoked')
      and (
          (o.assignment_id is null and a.state in ('held', 'in_progress'))
          or o.outcome <> 'decoded'
          or (o.outcome = 'decoded' and v.probability_usable < v.partial_below)
      )
      and not exists (
          select 1 from loss_diagnoses d
          where d.assignment_id = a.assignment_id
            and d.revision is not distinct from o.revision
            and d.method = %(method)s
            and d.config_sha256 = %(config_sha256)s)
    order by a.end_at, a.assignment_id
"""


def find_undiagnosed(  # noqa: PLR0913 — one query's parameters, each by name
    conn: Connection,
    *,
    method: str,
    config_sha256: bytes,
    classification_method: str,
    classification_sha256: bytes,
    verdict_method: str | None,
    limit: int | None = None,
) -> list[DiagnosisSubject]:
    """Every lost reception with no diagnosis by this method and configuration.

    Args:
        conn: An open connection.
        method: The diagnosis method, ``diagnosis-N``.
        config_sha256: The ``[diagnosis]`` table's hash.
        classification_method: The classification method a loss must be
            classified under, since it is read from that row.
        classification_sha256: That classification's configuration hash.
        verdict_method: The verdict a decode is read against, or ``None`` when
            no verdict model is deployed, which leaves no decode partial.
        limit: The most to return, oldest window first; ``None`` for all.

    Returns:
        The subjects, in window order.
    """
    sql = _UNDIAGNOSED + (" limit %(limit)s" if limit is not None else "")
    with conn.cursor(row_factory=class_row(DiagnosisSubject)) as cur:
        cur.execute(
            sql,
            {
                "method": method,
                "config_sha256": config_sha256,
                "classification_method": classification_method,
                "classification_sha256": classification_sha256,
                "verdict_method": verdict_method,
                "limit": limit,
            },
        )
        return cur.fetchall()


@dataclass(frozen=True, slots=True)
class NewDiagnosis:
    """One diagnosis, as written."""

    assignment_id: str
    revision: int | None
    observation_started_at: datetime | None
    station_id: str
    classification_id: int
    cause: str
    candidates: list[dict[str, object]]
    evidence: dict[str, object]
    method: str
    config_sha256: bytes
    verdict_method: str | None
    simulated: bool


def insert_diagnosis(conn: Connection, row: NewDiagnosis) -> bool:
    """Write one diagnosis, unless this method and configuration already did.

    Returns:
        Whether a row was written.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into loss_diagnoses (
                assignment_id, revision, observation_started_at, station_id,
                classification_id, cause, candidates_json, evidence_json,
                method, config_sha256, verdict_method, simulated)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict on constraint loss_diagnosis_once do nothing
            """,
            (
                row.assignment_id,
                row.revision,
                row.observation_started_at,
                row.station_id,
                row.classification_id,
                row.cause,
                Jsonb(row.candidates),
                Jsonb(row.evidence),
                row.method,
                row.config_sha256,
                row.verdict_method,
                row.simulated,
            ),
        )
        return cur.rowcount > 0


@dataclass(frozen=True, slots=True)
class StoredDiagnosis:
    """One stored diagnosis, as ``meridian diagnosis explain`` prints it."""

    diagnosis_id: int
    assignment_id: str
    revision: int | None
    station_id: str
    classification_id: int
    cause: str
    candidates_json: list[dict[str, object]]
    evidence_json: dict[str, object]
    method: str
    config_sha256: bytes
    verdict_method: str | None
    computed_at: datetime
    simulated: bool


def find_diagnoses(conn: Connection, assignment_id: str) -> list[StoredDiagnosis]:
    """Every diagnosis of one assignment, oldest first."""
    with conn.cursor(row_factory=class_row(StoredDiagnosis)) as cur:
        cur.execute(
            """
            select diagnosis_id, assignment_id, revision, station_id,
                   classification_id, cause, candidates_json, evidence_json,
                   method, config_sha256, verdict_method, computed_at, simulated
            from loss_diagnoses
            where assignment_id = %s
            order by computed_at, diagnosis_id
            """,
            (assignment_id,),
        )
        return cur.fetchall()
