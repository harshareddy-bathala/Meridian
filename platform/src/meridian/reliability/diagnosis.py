"""Why a reception was lost: one cause, or *undetermined* — the diagnosis itself.

Every failed or partial reception, and every held assignment whose window
passed with nothing reported, gets one (D-272). All five causes are tested
(:mod:`~meridian.reliability.diagnosis_causes`), and every test is recorded
whether it fired or not, so a reader sees what was considered as well as what
was concluded.

**Choosing** (D-273):

1. a cause another one explains steps aside: a timing fault explains a station
   the platform could not confirm was listening, because listening is judged by
   the platform's clock against the window, and a station whose clock was wrong
   listened at the wrong time;
2. no test fired → *undetermined*, because nothing showed;
3. one fired, or the best leads the next by at least the conflict margin → it;
4. otherwise → *undetermined*, because two causes conflict.

*Undetermined* is an answer, never a gap: "we looked and cannot say" and "we
have not looked" are different, and only the first is a row.

**What it never reads**: the simulator's ledger, an archive, or another
reception's diagnosis (D-102, D-105, D-108). The ground truth is joined to
diagnoses only afterwards, in the evaluation report.

Standard library only (D-180).

Reference: docs/DECISIONS.md D-102, D-104, D-105, D-272, D-273.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from meridian.reliability import diagnosis_causes
from meridian.reliability.config import DiagnosisConfig
from meridian.reliability.diagnosis_evidence import (
    CAUSES,
    Candidate,
    Cause,
    LossEvidence,
)

__all__ = ["METHOD", "Diagnosis", "choose", "diagnose"]

METHOD = "diagnosis-1"
"""Recorded with every diagnosis. Bumped whenever a rule here, in the causes or
in the loss map changes, so a stored row names the rules that produced it."""

_TESTS: dict[Cause, Callable[[LossEvidence, DiagnosisConfig], Candidate]] = {
    "satellite_silent": diagnosis_causes.satellite_silent,
    "station_not_listening": diagnosis_causes.station_not_listening,
    "obstruction": diagnosis_causes.obstruction,
    "interference": diagnosis_causes.interference,
    "timing_fault": diagnosis_causes.timing_fault,
}

EXPLAINS: dict[Cause, frozenset[Cause]] = {
    "timing_fault": frozenset({"station_not_listening"}),
}
"""Which causes a fired cause explains, so they step aside (D-273).

A raised floor explaining a sector's losses is the obstruction test's own
refusal, not a rule here, because it needs the floor the test already read.
"""


@dataclass(frozen=True, slots=True)
class Diagnosis:
    """One loss's cause, every candidate considered, and why this one."""

    cause: Cause
    reason: str
    """``named``, ``none`` when no test fired, or ``conflict``."""

    candidates: tuple[Candidate, ...]

    def candidates_json(self) -> list[dict[str, object]]:
        """Every candidate, as ``loss_diagnoses.candidates_json`` holds them."""
        return [
            {
                "cause": one.cause,
                "fired": one.fired,
                "support": one.support,
                "found": one.found,
            }
            for one in self.candidates
        ]


def diagnose(evidence: LossEvidence, config: DiagnosisConfig) -> Diagnosis:
    """Test every cause against one loss's evidence, and choose.

    Args:
        evidence: What Meridian's own records hold about the loss.
        config: The thresholds.

    Returns:
        The diagnosis.
    """
    candidates = tuple(_TESTS[cause](evidence, config) for cause in CAUSES)
    return choose(candidates, config.conflict_margin)


def choose(candidates: Sequence[Candidate], margin: float) -> Diagnosis:
    """The best-supported cause, or *undetermined*, by D-273's order.

    Args:
        candidates: One per cause, as the tests returned them.
        margin: The lead the best needs over the next.

    Returns:
        The diagnosis, with each explained candidate marked as such.
    """
    fired = {one.cause for one in candidates if one.fired}
    explained = {
        cause
        for explainer in fired
        for cause in EXPLAINS.get(explainer, frozenset())
        if cause in fired
    }
    recorded = tuple(
        replace(one, found={**one.found, "explained_by": _explainer(one.cause, fired)})
        if one.cause in explained
        else one
        for one in candidates
    )
    contenders = sorted(
        (one for one in recorded if one.fired and one.cause not in explained),
        key=lambda one: (-one.support, CAUSES.index(one.cause)),
    )
    if not contenders:
        return Diagnosis("undetermined", "none", recorded)
    best, *rest = contenders
    if rest and best.support - rest[0].support < margin:
        return Diagnosis("undetermined", "conflict", recorded)
    return Diagnosis(best.cause, "named", recorded)


def _explainer(cause: Cause, fired: set[Cause]) -> str:
    return next(
        explainer
        for explainer in CAUSES
        if explainer in fired and cause in EXPLAINS.get(explainer, frozenset())
    )
