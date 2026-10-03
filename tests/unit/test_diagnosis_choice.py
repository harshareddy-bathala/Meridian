"""Choosing one cause, or *undetermined*, from every candidate (D-273).

Reference: docs/DECISIONS.md D-104, D-273.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from meridian.reliability.config import DiagnosisConfig
from meridian.reliability.diagnosis import METHOD, choose, diagnose
from meridian.reliability.diagnosis_evidence import (
    CAUSES,
    Candidate,
    Cause,
    ListeningEvidence,
    LossEvidence,
    NoiseReading,
    SatelliteCounts,
    TimingEvidence,
)

MARGIN = DiagnosisConfig().conflict_margin


def candidates(**fired: float) -> tuple[Candidate, ...]:
    """One candidate per cause; those named fired with the support given."""
    return tuple(
        Candidate(cause, cause in fired, fired.get(cause, 0.0), {"looked": True})
        for cause in CAUSES
    )


def test_nothing_fired_is_undetermined_and_says_so() -> None:
    found = choose(candidates(), MARGIN)

    assert found.cause == "undetermined"
    assert found.reason == "none"


def test_one_cause_fired_is_named() -> None:
    found = choose(candidates(interference=0.6), MARGIN)

    assert (found.cause, found.reason) == ("interference", "named")


def test_the_best_supported_cause_wins_by_the_margin() -> None:
    found = choose(candidates(interference=0.95, obstruction=0.6), MARGIN)

    assert found.cause == "interference"


def test_two_causes_too_close_to_call_conflict() -> None:
    found = choose(candidates(interference=0.7, obstruction=0.6), MARGIN)

    assert (found.cause, found.reason) == ("undetermined", "conflict")


def test_two_categorical_answers_conflict() -> None:
    found = choose(candidates(satellite_silent=1.0, station_not_listening=1.0), MARGIN)

    assert found.cause == "undetermined"


def test_a_timing_fault_explains_a_station_not_confirmed_listening() -> None:
    """Listening is judged by the platform's clock; this station's was wrong."""
    found = choose(candidates(station_not_listening=1.0, timing_fault=0.6), MARGIN)

    assert found.cause == "timing_fault"
    (stepped,) = (
        one for one in found.candidates if one.cause == "station_not_listening"
    )
    assert stepped.fired
    assert stepped.found["explained_by"] == "timing_fault"


def test_not_listening_without_a_timing_fault_is_named() -> None:
    found = choose(candidates(station_not_listening=1.0), MARGIN)

    assert found.cause == "station_not_listening"


def test_every_candidate_is_recorded_whether_it_fired_or_not() -> None:
    found = choose(candidates(obstruction=0.8), MARGIN)
    recorded = found.candidates_json()

    assert [one["cause"] for one in recorded] == list(CAUSES)
    assert [one["fired"] for one in recorded] == [
        cause == "obstruction" for cause in CAUSES
    ]
    json.dumps(recorded)


@pytest.mark.parametrize("cause", CAUSES)
def test_any_cause_alone_can_be_named(cause: Cause) -> None:
    assert choose(candidates(**{cause: 0.75}), MARGIN).cause == cause


def test_diagnose_runs_every_test_in_order_and_abstains_on_nothing() -> None:
    """A confirmed silence of a satellite others heard, nothing else wrong: a
    miss with no cause any test can show, which is *undetermined*."""
    start = datetime(2026, 8, 12, 3, 0, tzinfo=UTC)
    end = start + timedelta(minutes=12)
    evidence = LossEvidence(
        listening=ListeningEvidence(7, "confirmed_miss", "no_signal", True, True),
        satellite=SatelliteCounts(True, signals=2, silences=0, peak_elevation_deg=60.0),
        noise=NoiseReading(-60.0, 30.0, -60.1, 20),
        timing=TimingEvidence(
            (start, end), 4.0, (start, end), (-0.1,), (), (start, end)
        ),
    )

    found = diagnose(evidence, DiagnosisConfig())

    assert [one.cause for one in found.candidates] == list(CAUSES)
    assert (found.cause, found.reason) == ("undetermined", "none")


def test_the_method_is_versioned() -> None:
    assert METHOD == "diagnosis-1"
