"""Diagnose every settled loss not yet diagnosed, and record why.

The live half of Stage 27: find each lost reception with no diagnosis under
this method and configuration (D-272), gather its evidence
(:mod:`~meridian.reliability.diagnosis_gather`), choose its cause
(:mod:`~meridian.reliability.diagnosis`), and store the cause with every
candidate and what was read. Run by ``meridian diagnosis run`` and by the jobs
service.

**A loss waits for its pass's classification**, under the deployment's
classification method and configuration, so it has had Stage 20's settle
margin and its "not listening" is read rather than asked again.

**Bounded.** ``limit`` diagnoses the losses whose windows closed first and
leaves the rest for the next run.

**One unreadable loss does not stop the rest.** A loss whose evidence cannot be
gathered — an element set gone, a sample that is not a number — is left without
a row, counted and named in the report, and the losses behind it are diagnosed.
It has no diagnosis because nobody could look, which is what the absence of a
row means (D-104). A database error is not one of these: it ends the run.

**Nothing here reads the answer.** The simulator's ledger, the evaluation's
ground truth and any archive are out of reach of every module on this path
(D-102, D-105), which ``tests/unit/test_diagnosis_boundaries.py`` holds.

Reference: docs/DECISIONS.md D-102, D-105, D-180, D-272, D-273.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from meridian.orbit.service import OrbitService
from meridian.registry import Registry
from meridian.reliability import classification
from meridian.reliability.config import ReliabilityConfig
from meridian.reliability.diagnosis import METHOD, Diagnosis, diagnose
from meridian.reliability.diagnosis_evidence import CAUSES
from meridian.reliability.diagnosis_gather import StationHistory, gather
from meridian.store.loss_diagnoses import (
    DiagnosisSubject,
    NewDiagnosis,
    find_undiagnosed,
    insert_diagnosis,
)
from meridian.store.stations import Connection

__all__ = ["DiagnosisRunReport", "diagnose_settled"]

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DiagnosisRunReport:
    """What one run diagnosed."""

    method: str
    config_sha256: bytes
    verdict_method: str | None
    """The verdict a decode was read against; ``None`` diagnoses no decode."""

    diagnosed: int
    written: int
    """Rows stored; fewer than ``diagnosed`` only if another run raced it."""

    by_cause: dict[str, int] = field(default_factory=dict)
    simulated: int = 0
    """How many of those diagnosed were simulated stations' losses."""

    deferred: bool = False
    """Whether ``limit`` left losses for the next run."""

    unreadable: tuple[str, ...] = ()
    """Assignments whose evidence could not be gathered, each with why. They
    have no row, and are tried again by the next run."""


def diagnose_settled(  # noqa: PLR0913 — a run's collaborators, each by name
    conn: Connection,
    registry: Registry,
    orbit: OrbitService,
    *,
    config: ReliabilityConfig,
    verdict_method: str | None,
    limit: int | None = None,
) -> DiagnosisRunReport:
    """Diagnose every classified loss with no diagnosis under this configuration.

    Args:
        conn: An open connection, on which ``registry`` is also bound.
        registry: Answers whether each silent station was listening.
        orbit: Places samples in a station's sky.
        config: The deployment's reliability configuration: its classification
            says which classifications are read, its ``[diagnosis]`` table the
            thresholds.
        verdict_method: The method of the verdict model a decode is read
            against, or ``None`` when none is deployed.
        limit: At most this many losses, those whose windows closed first.

    Returns:
        What was diagnosed and stored.
    """
    if limit is not None and limit < 1:
        raise ValueError(f"limit must be at least 1, not {limit}")
    thresholds = config.diagnosis
    config_sha256 = thresholds.sha256()
    subjects = find_undiagnosed(
        conn,
        method=METHOD,
        config_sha256=config_sha256,
        classification_method=classification.METHOD,
        classification_sha256=config.classification.sha256(),
        verdict_method=verdict_method,
        limit=None if limit is None else limit + 1,
    )
    chosen = subjects if limit is None else subjects[:limit]
    history = StationHistory()
    by_cause: dict[str, int] = dict.fromkeys((*CAUSES, "undetermined"), 0)
    written = simulated = 0
    unreadable: list[str] = []
    for subject in chosen:
        try:
            evidence = gather(
                conn, registry, orbit, subject, config=thresholds, history=history
            )
        except (LookupError, ValueError, ArithmeticError) as exc:
            _log.warning("loss %s could not be read: %s", subject.assignment_id, exc)
            unreadable.append(f"{subject.assignment_id}: {exc}")
            continue
        found = diagnose(evidence, thresholds)
        by_cause[found.cause] += 1
        simulated += subject.simulated
        written += insert_diagnosis(
            conn,
            _row(subject, found, config, config_sha256, verdict_method),
        )
    return DiagnosisRunReport(
        method=METHOD,
        config_sha256=config_sha256,
        verdict_method=verdict_method,
        diagnosed=len(chosen) - len(unreadable),
        written=written,
        by_cause=by_cause,
        simulated=simulated,
        deferred=len(subjects) > len(chosen),
        unreadable=tuple(unreadable),
    )


def _row(
    subject: DiagnosisSubject,
    found: Diagnosis,
    config: ReliabilityConfig,
    config_sha256: bytes,
    verdict_method: str | None,
) -> NewDiagnosis:
    partial = subject.outcome == "decoded"
    evidence: dict[str, object] = {
        "reason": found.reason,
        "loss": "partial" if partial else ("failed" if subject.revision else "empty"),
        "classification": {
            "id": subject.classification_id,
            "class": subject.classification,
            "method": classification.METHOD,
        },
        "verdict": (
            {
                "method": verdict_method,
                "probability_usable": subject.probability_usable,
                "partial_below": subject.partial_below,
            }
            if partial
            else None
        ),
        "parameters": config.diagnosis.parameters(),
    }
    return NewDiagnosis(
        assignment_id=subject.assignment_id,
        revision=subject.revision,
        observation_started_at=subject.observation_started_at,
        station_id=subject.station_id,
        classification_id=subject.classification_id,
        cause=found.cause,
        candidates=found.candidates_json(),
        evidence=evidence,
        method=METHOD,
        config_sha256=config_sha256,
        verdict_method=verdict_method if partial else None,
        simulated=subject.simulated,
    )
