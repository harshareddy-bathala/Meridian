"""What each simulated loss truly was, judged from a sealed run's files alone.

The only place ground truth meets a diagnosis (D-105, D-278). The ledger says
which faults acted on which assignments; the run's cases say what each
assignment reported and what the outcome model would have said with nothing
wrong. From those, every diagnosed loss gets one **truth**:

* **a cause** — exactly one fault acted on the pass, it is one with a category,
  and the pass came out worse than its clean outcome: the fault lost it;
* ``control`` — the same, for a fault with no category (Stage 21's degraded
  decoder, Stage 25's degradation), whose right answer is *undetermined*;
* ``acted_not_cause`` — a fault acted and the pass is no worse than it would
  have been: whatever lost it, the fault did not;
* ``several`` — more than one fault acted, and which lost it is not known;
* ``none`` — no fault acted: the outcome model lost it, and the right answer
  is *undetermined*.

**"A fault acted" is not "a fault caused the loss".** Stage 25's ledger names a
pass a fault *changed* (D-253), and a stepped clock names every pass it moved
(D-277). Comparing the outcome with the clean one, recomputed from the seed, is
what makes a recall honest.

Reference: docs/DECISIONS.md D-105, D-253, D-277, D-278.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from meridian.datasets.diagnosis_runs import DiagnosisRun, NotADiagnosisRunError
from meridian.reliability.classification import OUTCOME_ORDER
from meridian.reliability.fault_ledger import InjectedFault

__all__ = ["CAUSE_OF", "CONTROLS", "TRUTHS", "Judged", "acting", "judge_run"]

CAUSE_OF: dict[str, str] = {
    "receiver_down": "station_not_listening",
    "satellite_silent": "satellite_silent",
    "obstruction": "obstruction",
    "interference": "interference",
    "clock_step": "timing_fault",
}
"""Each fault with a category, and the cause it should be diagnosed as."""

CONTROLS = frozenset({"decoder_degraded", "signal_degradation"})
"""Faults with no category: the right diagnosis of a loss they caused is
*undetermined*."""

TRUTHS = (
    "satellite_silent",
    "station_not_listening",
    "obstruction",
    "interference",
    "timing_fault",
    "control",
    "acted_not_cause",
    "several",
    "none",
)
"""Every truth, in the order the report's matrix lists its rows."""


@dataclass(frozen=True, slots=True)
class Judged:
    """One diagnosed loss, beside what was truly behind it."""

    assignment_id: str
    seed: int
    truth: str
    diagnosed: str
    kinds: tuple[str, ...]
    """Every fault that acted on the pass, sorted."""


def acting(faults: Iterable[InjectedFault]) -> dict[str, set[str]]:
    """Each assignment, and every fault the ledger says acted on it."""
    found: defaultdict[str, set[str]] = defaultdict(set)
    for fault in faults:
        for assignment_id in fault.assignment_ids:
            found[assignment_id].add(fault.kind)
    return found


def judge_run(run: DiagnosisRun) -> list[Judged]:
    """Every diagnosis of a sealed run, beside its truth.

    Returns:
        One per diagnosis, in the order the run holds them.

    Raises:
        NotADiagnosisRunError: A diagnosis of an assignment the run has no case
            for, or a run without a master seed: a damaged run is refused, not
            guessed, and with a sentence the report command can print.
    """
    acted = acting(run.faults)
    cases = {str(one.get("assignment_id")): one for one in run.cases}
    if "master_seed" not in run.run:
        raise NotADiagnosisRunError(f"{run.directory.path} records no master seed")
    seed = int(str(run.run["master_seed"]))
    judged = []
    for diagnosis in run.diagnoses:
        assignment_id = str(diagnosis.get("assignment_id"))
        case = cases.get(assignment_id)
        if case is None:
            message = (
                f"{run.directory.path} holds a diagnosis of {assignment_id}"
                " and no case for it"
            )
            raise NotADiagnosisRunError(message)
        kinds = tuple(sorted(acted.get(assignment_id, ())))
        worse = _rank(case.get("outcome")) > _rank(case.get("clean_outcome"))
        judged.append(
            Judged(
                assignment_id=assignment_id,
                seed=seed,
                truth=_truth(kinds, worse=worse),
                diagnosed=str(diagnosis["cause"]),
                kinds=kinds,
            )
        )
    return judged


def _truth(kinds: tuple[str, ...], *, worse: bool) -> str:
    """The truth of one loss, by the rules the module states, in their order."""
    if len(kinds) != 1:
        return "several" if kinds else "none"
    (kind,) = kinds
    if not worse:
        return "acted_not_cause"
    return "control" if kind in CONTROLS else CAUSE_OF.get(kind, "acted_not_cause")


def _rank(outcome: object) -> int:
    """Worse is higher; no report at all is the worst there is."""
    return (
        OUTCOME_ORDER.index(outcome) if outcome in OUTCOME_ORDER else len(OUTCOME_ORDER)
    )
