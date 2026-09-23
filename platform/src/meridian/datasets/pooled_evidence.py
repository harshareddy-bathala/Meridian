"""What a physical pass's assignments add up to — the evidence D-146 labels.

Several assignments can claim one rise: configurations A and B are scheduled
over the same horizon to be compared, and a later run may schedule a newer
prediction of it (D-148). The reception is physical, so their evidence is
pooled before any rule reads it:

* every assignment that scheduled any prediction of the rise, in id order;
* the report: each assignment's latest revision, and where several reported,
  the most informative in :data:`OUTCOME_ORDER`;
* listening: confirmed if the registry confirmed it for any of them (D-145);
* simulated: if the pass, any assignment or any report is.

Reference: docs/DECISIONS.md D-145, D-146, D-148.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from meridian.datasets.physical_passes import PhysicalPass
from meridian.datasets.snapshot_rows import AssignmentRow, ObservationRow, SnapshotRows

__all__ = ["OUTCOME_ORDER", "PooledEvidence", "pool_evidence"]

OUTCOME_ORDER = ("decoded", "signal_no_decode", "no_signal", "aborted", "not_attempted")
"""Most informative first: where two assignments of one pass both reported."""


@dataclass(frozen=True, slots=True)
class PooledEvidence:
    """What one physical pass's scheduled assignments add up to."""

    scheduled: tuple[AssignmentRow, ...]
    report: ObservationRow | None
    listening: bool | None
    simulated: bool


def pool_evidence(
    physical: Iterable[PhysicalPass], rows: SnapshotRows
) -> dict[int, PooledEvidence]:
    """Each physical pass's pooled evidence, keyed by its representative's id."""
    by_pass = _group(rows.assignments)
    latest = _latest_reports(rows.observations)
    return {
        one.representative.pass_id: _pool(
            one,
            tuple(held for member in one.pass_ids for held in by_pass.get(member, ())),
            latest,
            rows.listening,
        )
        for one in physical
    }


def _pool(
    physical: PhysicalPass,
    assignments: tuple[AssignmentRow, ...],
    latest: Mapping[str, ObservationRow],
    listening: Mapping[str, bool],
) -> PooledEvidence:
    """Pool a physical pass's scheduled assignments into one body of evidence.

    Assignments to any prediction of the rise count, in assignment-id order.
    """
    scheduled = tuple(
        sorted(
            (one for one in assignments if one.decision == "scheduled"),
            key=lambda one: one.assignment_id,
        )
    )
    reports = [
        latest[one.assignment_id] for one in scheduled if one.assignment_id in latest
    ]
    report = min(reports, key=_informativeness, default=None)
    answers = [
        listening[one.assignment_id]
        for one in scheduled
        if one.assignment_id in listening
    ]
    return PooledEvidence(
        scheduled=scheduled,
        report=report,
        listening=any(answers) if answers else None,
        simulated=physical.simulated
        or any(one.simulated for one in scheduled)
        or any(one.simulated for one in reports),
    )


def _informativeness(report: ObservationRow) -> tuple[int, str]:
    """Rank a report by :data:`OUTCOME_ORDER`, then by id, so ties are stable."""
    rank = (
        OUTCOME_ORDER.index(report.outcome)
        if report.outcome in OUTCOME_ORDER
        else len(OUTCOME_ORDER)
    )
    return rank, report.assignment_id


def _latest_reports(
    observations: Iterable[ObservationRow],
) -> dict[str, ObservationRow]:
    """Each assignment's latest revision. The export kept only those by ``as_of``."""
    latest: dict[str, ObservationRow] = {}
    for one in observations:
        held = latest.get(one.assignment_id)
        if held is None or one.revision > held.revision:
            latest[one.assignment_id] = one
    return latest


def _group(
    assignments: Iterable[AssignmentRow],
) -> dict[int, tuple[AssignmentRow, ...]]:
    """Assignments by pass, each group in assignment-id order."""
    grouped: dict[int, list[AssignmentRow]] = {}
    for one in sorted(assignments, key=lambda one: one.assignment_id):
        grouped.setdefault(one.pass_id, []).append(one)
    return {pass_id: tuple(held) for pass_id, held in grouped.items()}
