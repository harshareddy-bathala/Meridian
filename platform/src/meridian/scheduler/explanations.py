"""Why each decision went the way it did, as a record kept beside it — D-170.

``reason`` is one sentence. The explanation is what the sentence rests on,
stored as JSON on the decision when it is made:

* ``terms`` — the value, and the yield, frames and priority it is the product
  of, with where the yield came from (D-168);
* ``weighed_against`` — every pass this one could not share the station with,
  selected, skipped or already committed, with its value, best first;
* ``rule`` — for a skip, the constraint that decided it: ``overlap`` or
  ``eligible_cap`` (D-166). ``None`` for a selection;
* ``alternative`` — for a skip, the pass that took its slot; for a selection,
  the best pass it displaced. ``None`` where there was none;
* ``run`` — the solver's status and, for a model, the history's ``as_of``, so
  a reader of one decision knows whether the schedule it belongs to was proven
  best, and how old the record the model read was.

Pure: no I/O and no clock. Everything is written as JSON's own types, so what
is stored is what was built.

Reference: docs/DECISIONS.md D-165, D-166, D-168, D-170.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from meridian.scheduler import Candidate, Commitment, ScheduleOutcome
from meridian.scheduler.constraints import overlaps
from meridian.scheduler.objective import Terms

__all__ = ["RunFacts", "explain", "terms_json"]

Json = dict[str, object]


@dataclass(frozen=True, slots=True)
class RunFacts:
    """What every decision of one run shares."""

    status: str
    history_as_of: datetime | None


def terms_json(terms: Terms) -> Json:
    """The value and each term it is the product of."""
    return {
        "value": terms.value,
        "yield": terms.expected.probability,
        "yield_source": terms.expected.source,
        "yield_path": terms.expected.path,
        "yield_reason": terms.expected.reason,
        "frames": terms.frames,
        "frames_term": terms.frames_term,
        "priority": terms.priority,
        "priority_weighted": terms.priority_weighted,
    }


def explain(
    outcome: ScheduleOutcome,
    terms: Mapping[int, Terms],
    committed: Sequence[Commitment],
    *,
    turnaround_s: float,
    run: RunFacts,
) -> dict[int, Json]:
    """Every decision's explanation, by pass id.

    Args:
        outcome: The schedule: what was taken, and what lost and why.
        terms: Each decided candidate's terms.
        committed: The assignments earlier runs made, which bound this one.
        turnaround_s: The run's turnaround, which overlap is judged with.
        run: The solver's status and the history's ``as_of``.

    Returns:
        One JSON object per decided pass.
    """
    board = _Board(
        decided=[one.candidate for one in outcome.selected]
        + [one.scored.candidate for one in outcome.rejected],
        committed=committed,
        terms=terms,
        selected=frozenset(one.candidate.pass_id for one in outcome.selected),
        turnaround_s=turnaround_s,
    )
    run_json: Json = {
        "status": run.status,
        "history_as_of": None
        if run.history_as_of is None
        else _utc_text(run.history_as_of),
    }
    found: dict[int, Json] = {}
    for one in outcome.selected:
        against = board.against(one.candidate)
        displaced = next(
            (entry for entry in against if entry["decision"] == "skipped"), None
        )
        found[one.candidate.pass_id] = {
            "terms": terms_json(terms[one.candidate.pass_id]),
            "weighed_against": against,
            "rule": None,
            "alternative": displaced,
            "run": run_json,
        }
    for rejection in outcome.rejected:
        candidate = rejection.scored.candidate
        against = board.against(candidate)
        winner = next(
            (
                entry
                for entry in against
                if entry["pass_id"] == rejection.conflicts_with_pass_id
            ),
            None,
        )
        found[candidate.pass_id] = {
            "terms": terms_json(terms[candidate.pass_id]),
            "weighed_against": against,
            "rule": rejection.rule,
            "alternative": winner,
            "run": run_json,
        }
    return found


@dataclass(frozen=True, slots=True)
class _Board:
    """Everything one run weighed, to find what each pass was weighed against."""

    decided: Sequence[Candidate]
    committed: Sequence[Commitment]
    terms: Mapping[int, Terms]
    selected: frozenset[int]
    turnaround_s: float

    def against(self, one: Candidate) -> list[Json]:
        """Every pass ``one`` could not share the station with, best value first."""
        entries: list[Json] = [
            {
                "pass_id": other.pass_id,
                "decision": "scheduled"
                if other.pass_id in self.selected
                else "skipped",
                "value": self.terms[other.pass_id].value,
                "assignment_id": None,
            }
            for other in self.decided
            if other.pass_id != one.pass_id and overlaps(one, other, self.turnaround_s)
        ]
        ranked = sorted(entries, key=lambda entry: (-_value(entry), _pass_id(entry)))
        ranked.extend(
            {
                "pass_id": fixed.candidate.pass_id,
                "decision": "committed",
                "value": None,
                "assignment_id": fixed.assignment_id,
            }
            for fixed in sorted(self.committed, key=lambda held: held.candidate.pass_id)
            if overlaps(one, fixed.candidate, self.turnaround_s)
        )
        return ranked


def _utc_text(at: datetime) -> str:
    """ISO 8601 in UTC with a ``Z``, as the public API writes every instant.

    So what is published is exactly what was stored.
    """
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _value(entry: Json) -> float:
    value = entry["value"]
    return value if isinstance(value, float) else 0.0


def _pass_id(entry: Json) -> int:
    pass_id = entry["pass_id"]
    return pass_id if isinstance(pass_id, int) else 0
