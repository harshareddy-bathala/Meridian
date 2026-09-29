"""What a pass is worth to the schedule, term by term (D-168).

Each term is checked on a pass whose value can be worked out by hand, the
configurations are checked to weight by priority exactly where prediction's
table says they do, and each refusal names what it refused.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from meridian.prediction.configurations import CONFIGURATIONS, objective
from meridian.scheduler import Candidate
from meridian.scheduler.objective import (
    ELEVATION_PROXY,
    PRIORITY_WEIGHTED,
    Yield,
    elevation_proxy,
    frames_of,
    modelled,
    value_candidates,
)

T0 = datetime(2026, 9, 28, tzinfo=UTC)


def a_pass(
    pass_id: int = 1,
    *,
    minutes: float = 10.0,
    elevation: float = 45.0,
    priority: float = 2.0,
    margin_s: float = 30.0,
) -> Candidate:
    return Candidate(
        pass_id=pass_id,
        station_id="st_001",
        aos=T0,
        los=T0 + timedelta(minutes=minutes),
        margin_s=margin_s,
        max_elevation_deg=elevation,
        priority=priority,
        simulated=False,
    )


def test_the_value_is_yield_times_frames_times_priority_under_b_and_d() -> None:
    one = a_pass()
    yields = {1: modelled(0.25, "configured", "enough history")}

    values = {
        name: value_candidates(
            [one], configuration=name, frames_term="duration", yields=yields
        )[0][0].score
        for name in "ABCD"
    }

    assert values == {"A": 150.0, "B": 300.0, "C": 150.0, "D": 300.0}


def test_priority_weights_the_configurations_prediction_says_it_does() -> None:
    """One table in the scheduler, one in prediction; they may not drift."""
    weighted = {
        name for name, one in CONFIGURATIONS.items() if one.weighted_by_priority
    }

    assert weighted == PRIORITY_WEIGHTED == {"B", "D"}
    assert objective(CONFIGURATIONS["D"], 0.5, 3.0) == pytest.approx(1.5)


def test_frames_are_the_pass_not_the_widened_window() -> None:
    """The margin is time spent waiting for an uncertain pass, not signal."""
    assert frames_of(a_pass(minutes=12, margin_s=90.0), "duration") == 720.0
    assert frames_of(a_pass(minutes=12), "none") == 1.0


def test_every_term_is_kept_beside_the_score() -> None:
    scored, terms = value_candidates(
        [a_pass()],
        configuration="B",
        frames_term="duration",
        yields={1: modelled(0.5, "configured", "reads no history")},
    )

    held = terms[1]
    assert (held.expected.probability, held.frames, held.priority) == (0.5, 600.0, 2.0)
    assert held.priority_weighted
    assert held.expected.path == "configured"
    assert scored[0].score == held.value == 600.0


# --- the elevation proxy ------------------------------------------------------


def test_with_no_model_a_and_b_take_the_elevation_proxy_labelled_so() -> None:
    scored, terms = value_candidates(
        [a_pass(elevation=45.0)], configuration="A", frames_term="none", yields=None
    )

    assert terms[1].expected.source == ELEVATION_PROXY
    assert terms[1].expected.path is None
    assert "no model" in terms[1].expected.reason
    assert scored[0].score == 0.5


def test_the_proxy_is_a_probability_at_either_extreme() -> None:
    assert elevation_proxy(a_pass(elevation=90.0)).probability == 1.0
    assert elevation_proxy(a_pass(elevation=-0.5)).probability == 0.0


@pytest.mark.parametrize("configuration", ["C", "D"])
def test_a_learned_configuration_without_a_model_is_refused(configuration: str) -> None:
    with pytest.raises(ValueError, match="elevation proxy stands in for A and B"):
        value_candidates(
            [a_pass()], configuration=configuration, frames_term="none", yields=None
        )


# --- refusals -----------------------------------------------------------------


def test_a_candidate_the_model_did_not_score_is_refused() -> None:
    with pytest.raises(ValueError, match="gave pass 1 no yield"):
        value_candidates([a_pass()], configuration="D", frames_term="none", yields={})


@pytest.mark.parametrize("priority", [0.0, -1.0, float("inf"), float("nan")])
def test_a_priority_that_is_not_a_positive_number_is_refused(priority: float) -> None:
    with pytest.raises(ValueError, match="a priority is a positive number"):
        value_candidates(
            [a_pass(priority=priority)],
            configuration="A",
            frames_term="none",
            yields=None,
        )


@pytest.mark.parametrize("probability", [-0.01, 1.01, float("nan")])
def test_a_yield_that_is_not_a_probability_is_refused(probability: float) -> None:
    with pytest.raises(ValueError, match="is not a probability"):
        modelled(probability, "configured", "")


def test_an_unknown_yield_source_or_frames_term_is_refused() -> None:
    with pytest.raises(ValueError, match="yield source"):
        Yield(probability=0.5, source="guess", path=None, reason="")
    with pytest.raises(ValueError, match="frames must be one of"):
        frames_of(a_pass(), "bytes")
