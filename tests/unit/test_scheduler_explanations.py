"""Why each decision went the way it did, stored beside it (D-170).

Each explanation is checked on a schedule whose reasons can be read off by hand,
then against the public API's model, which it must validate as and serialise
back to unchanged: the two are one contract, written in two places.

Reference: docs/DECISIONS.md D-166, D-168, D-170.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from meridian.api.public.models.assignments import Explanation
from meridian.scheduler import Candidate, Commitment
from meridian.scheduler.assignment_records import PassFacts, Stamp, to_assignment_rows
from meridian.scheduler.constraints import Rules
from meridian.scheduler.explanations import RunFacts, explain
from meridian.scheduler.objective import Terms, modelled, value_candidates
from meridian.scheduler.optimiser import SolverSettings, optimise

T0 = datetime(2026, 9, 28, tzinfo=UTC)
AS_OF = datetime(2026, 9, 27, 6, tzinfo=UTC)
RUN = RunFacts(status="optimal", history_as_of=AS_OF)


def a_pass(pass_id: int, *, minute: float, elevation: float) -> Candidate:
    aos = T0 + timedelta(minutes=minute)
    return Candidate(
        pass_id=pass_id,
        station_id="st_a",
        aos=aos,
        los=aos + timedelta(minutes=11),
        margin_s=0.0,
        max_elevation_deg=elevation,
        priority=1.0,
        simulated=False,
    )


def decided(
    candidates: list[Candidate],
    committed: tuple[Commitment, ...] = (),
    *,
    rules: Rules | None = None,
) -> tuple[dict[int, dict[str, object]], dict[int, Terms], object]:
    rules = rules or Rules(turnaround_s=0.0)
    scored, terms = value_candidates(
        candidates, configuration="A", frames_term="duration", yields=None
    )
    outcome = optimise(
        scored,
        rules=rules,
        settings=SolverSettings(time_limit_s=10.0),
        committed=committed,
    ).outcome
    found = explain(outcome, terms, committed, turnaround_s=rules.turnaround_s, run=RUN)
    return found, terms, outcome


def test_the_two_a_high_pass_lost_to_are_named_and_valued() -> None:
    """The textbook case: 70° between two 45° passes it overlaps. The two are
    worth more together, so the high pass is skipped, naming the better of them
    — the earlier on a tie — and each of the two names it as displaced."""
    early = a_pass(1, minute=0, elevation=45)
    high = a_pass(2, minute=10, elevation=70)
    late = a_pass(3, minute=20, elevation=45)

    found, terms, _ = decided([early, high, late])

    lost = found[2]
    assert lost["rule"] == "overlap"
    assert lost["alternative"] == {
        "pass_id": 1,
        "decision": "scheduled",
        "value": terms[1].value,
        "assignment_id": None,
    }
    assert [entry["pass_id"] for entry in lost["weighed_against"]] == [1, 3]  # type: ignore[attr-defined]
    for kept in (1, 3):
        assert found[kept]["rule"] is None
        assert found[kept]["alternative"] == {
            "pass_id": 2,
            "decision": "skipped",
            "value": terms[2].value,
            "assignment_id": None,
        }


def test_what_a_pass_was_weighed_against_is_best_value_first() -> None:
    """A high pass beats two lower ones that overlap it and each other; the
    better of the two is what it displaced."""
    high = a_pass(1, minute=0, elevation=80)
    better = a_pass(2, minute=5, elevation=60)
    worse = a_pass(3, minute=8, elevation=50)

    found, _, _ = decided([worse, high, better])

    assert [entry["pass_id"] for entry in found[1]["weighed_against"]] == [2, 3]  # type: ignore[attr-defined]
    assert found[1]["alternative"]["pass_id"] == 2  # type: ignore[index]


def test_the_terms_are_the_value_s_own() -> None:
    found, terms, _ = decided([a_pass(1, minute=0, elevation=45)])

    stored = found[1]["terms"]
    assert stored == {
        "value": 330.0,
        "yield": 0.5,
        "yield_source": "elevation_proxy",
        "yield_path": None,
        "yield_reason": "no model configured: peak elevation over 90°",
        "frames": 660.0,
        "frames_term": "duration",
        "priority": 1.0,
        "priority_weighted": False,
    }
    assert terms[1].value == 330.0
    assert found[1]["run"] == {
        "status": "optimal",
        "history_as_of": "2026-09-27T06:00:00Z",
    }
    assert found[1]["weighed_against"] == []
    assert found[1]["alternative"] is None


def test_a_commitment_that_blocked_a_pass_is_its_alternative() -> None:
    held = Commitment(a_pass(9, minute=-5, elevation=10), "as_held")

    found, _, _ = decided([a_pass(1, minute=0, elevation=80)], (held,))

    assert found[1]["rule"] == "overlap"
    assert found[1]["alternative"] == {
        "pass_id": 9,
        "decision": "committed",
        "value": None,
        "assignment_id": "as_held",
    }


def test_a_pass_the_cap_turned_away_names_no_alternative() -> None:
    passes = [a_pass(n, minute=12 * n, elevation=80 - n) for n in range(3)]

    found, _, _ = decided(passes, rules=Rules(turnaround_s=0.0, most_eligible=2))

    assert found[2]["rule"] == "eligible_cap"
    assert found[2]["alternative"] is None


def test_what_a_pass_is_weighed_against_includes_the_turnaround() -> None:
    """A minute apart: clear with no turnaround, in each other's way with two."""
    first, second = (
        a_pass(1, minute=0, elevation=60),
        a_pass(2, minute=12, elevation=50),
    )

    clear, _, _ = decided([first, second])
    crowded, _, _ = decided([first, second], rules=Rules(turnaround_s=120.0))

    assert clear[1]["weighed_against"] == []
    assert [entry["pass_id"] for entry in crowded[1]["weighed_against"]] == [2]  # type: ignore[attr-defined]


def test_every_explanation_is_what_the_public_api_publishes() -> None:
    """Validated by the API's model and serialised back, byte for byte."""
    held = Commitment(a_pass(9, minute=-5, elevation=10), "as_held")
    candidates = [
        a_pass(1, minute=0, elevation=45),
        a_pass(2, minute=10, elevation=70),
        a_pass(3, minute=20, elevation=45),
    ]

    found, _, _ = decided(candidates, (held,))

    for explanation in found.values():
        stored = json.loads(json.dumps(explanation))
        published = Explanation.model_validate(stored).model_dump(
            mode="json", by_alias=True
        )
        assert published == stored


# --- stamped on the rows ------------------------------------------------------


def facts(one: Candidate) -> PassFacts:
    return PassFacts(
        centre_freq_hz=137_100_000,
        mode="lrpt",
        aos=one.aos,
        los=one.los,
        timing_uncertainty_s=0.0,
    )


def test_a_run_stamps_its_id_model_and_explanation_on_every_row() -> None:
    candidates = [a_pass(1, minute=0, elevation=45), a_pass(2, minute=5, elevation=30)]
    found, _, outcome = decided(candidates)
    stamp = Stamp(
        run_id="sr_0123456789ab",
        model_sha256=bytes(32),
        explanations=found,
        predicted_yields={1: 0.8},
    )

    rows = {
        row.pass_id: row
        for row in to_assignment_rows(
            outcome,  # type: ignore[arg-type]
            {one.pass_id: facts(one) for one in candidates},
            "A",
            stamp,
        )
    }

    assert {row.schedule_run_id for row in rows.values()} == {"sr_0123456789ab"}
    assert rows[1].explanation == found[1]
    assert rows[2].explanation == found[2]
    assert (rows[1].predicted_yield, rows[2].predicted_yield) == (0.8, None)
    assert rows[1].model_sha256 == bytes(32)


def test_a_model_s_yield_is_carried_into_the_terms() -> None:
    one = a_pass(1, minute=0, elevation=45)
    _, terms = value_candidates(
        [one],
        configuration="D",
        frames_term="duration",
        yields={1: modelled(0.25, "configured", "enough history")},
    )

    assert terms[1].expected.path == "configured"
    assert terms[1].value == 0.25 * 660.0
