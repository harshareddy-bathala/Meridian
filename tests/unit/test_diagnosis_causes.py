"""Each cause's test, on evidence written by hand: when it fires, and when not.

The rules are pure (D-180), so every case here is a value in and a candidate
out. Each test has its positive case, its negative, and the case where the
evidence it needs is missing — which never fires, because a missing floor is
not a normal one and *undetermined* is a correct answer (D-273).

Reference: docs/DECISIONS.md D-147, D-273 to D-277.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from meridian.reliability.config import DiagnosisConfig
from meridian.reliability.diagnosis_causes import (
    interference,
    obstruction,
    satellite_silent,
    station_not_listening,
    support_of,
    timing_fault,
)
from meridian.reliability.diagnosis_evidence import (
    HistoryPass,
    HorizonFloor,
    ListeningEvidence,
    LossEvidence,
    NoiseReading,
    ProfileCell,
    SatelliteCounts,
    SkySample,
    TimingEvidence,
)
from meridian.reliability.obstruction_map import build_map, lost_where_heard

CONFIG = DiagnosisConfig()
HIGH = 60.0
"""A pass high enough that hearing nothing from it says something (D-276)."""
START = datetime(2026, 8, 12, 3, 0, tzinfo=UTC)
END = START + timedelta(minutes=12)
COUNT = 25


def sweep(
    *,
    peak_snr: float = 18.0,
    peak_el: float = 60.0,
    from_az: float = 100.0,
    to_az: float = 200.0,
    blocked: tuple[float, float, float] | None = None,
) -> tuple[SkySample, ...]:
    """One symmetric pass: up to ``peak_el`` and down, SNR following elevation.

    ``blocked`` is ``(azimuth from, width, below elevation)``: samples inside
    read noise, as behind an obstruction.
    """
    out = []
    for i in range(COUNT):
        x = i / (COUNT - 1)
        el = 5.0 + (peak_el - 5.0) * math.sin(math.pi * x)
        az = from_az + (to_az - from_az) * x
        snr = round(peak_snr * math.sin(math.pi * x) - 3.0, 1)
        if blocked is not None:
            start, width, below = blocked
            if (az - start) % 360.0 < width and el < below:
                snr = 0.4
        out.append(SkySample(START + timedelta(seconds=30 * i), snr, az, el))
    return tuple(out)


def evidence(**changes: object) -> LossEvidence:
    """A confirmed-listening, unheard pass with nothing else wrong."""
    base = LossEvidence(
        listening=ListeningEvidence(
            classification_id=7,
            classification="confirmed_miss",
            outcome="no_signal",
            heard_during_window=True,
            listening_confirmed=True,
        ),
        satellite=SatelliteCounts(
            catalogue_active=True, signals=3, silences=0, peak_elevation_deg=HIGH
        ),
        noise=NoiseReading(
            floor_dbfs=-60.0, gain_db=30.0, baseline_dbfs=-60.2, baseline_count=20
        ),
        timing=TimingEvidence(
            window=(START, END),
            uncertainty_s=4.0,
            listening_span=(START + timedelta(seconds=10), END - timedelta(seconds=5)),
            skews_s=(-0.2, -0.1, -0.3),
            reported_offsets=(),
            recording=(START, END),
        ),
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def listening(**changes: object) -> ListeningEvidence:
    return replace(evidence().listening, **changes)  # type: ignore[arg-type]


# --- station not listening ------------------------------------------------


@pytest.mark.parametrize(
    ("classification", "outcome", "heard", "fires"),
    [
        ("station_not_confirmed_listening", "no_signal", True, True),
        ("station_unavailable", "not_attempted", True, True),
        ("station_unavailable", None, True, True),
        ("station_unavailable", None, False, True),
        ("station_unavailable", "aborted", True, False),
        ("confirmed_miss", "no_signal", True, False),
        ("signal_no_decode", "signal_no_decode", True, False),
    ],
)
def test_not_listening_is_stage_20_s_answer_read_not_restated(
    classification: str, outcome: str | None, heard: bool, fires: bool
) -> None:
    found = station_not_listening(
        evidence(
            listening=listening(
                classification=classification,
                outcome=outcome,
                heard_during_window=heard,
            )
        ),
        CONFIG,
    )

    assert found.fired is fires
    assert found.support == (1.0 if fires else 0.0)
    assert found.found["classification_id"] == 7


# --- satellite silent -----------------------------------------------------


def test_a_satellite_nobody_heard_while_others_listened_is_silent() -> None:
    found = satellite_silent(
        evidence(
            satellite=SatelliteCounts(
                True, signals=0, silences=2, peak_elevation_deg=HIGH
            )
        ),
        CONFIG,
    )

    assert found.fired
    assert found.support == support_of(2.0)
    assert found.found["state"] == "silent"


def test_one_other_station_hearing_it_says_it_was_transmitting() -> None:
    found = satellite_silent(
        evidence(
            satellite=SatelliteCounts(
                True, signals=1, silences=4, peak_elevation_deg=HIGH
            )
        ),
        CONFIG,
    )

    assert not found.fired
    assert found.found["state"] == "transmitting"


def test_no_other_attempt_is_no_evidence_either_way() -> None:
    found = satellite_silent(
        evidence(
            satellite=SatelliteCounts(
                True, signals=0, silences=0, peak_elevation_deg=HIGH
            )
        ),
        CONFIG,
    )

    assert not found.fired
    assert found.found["state"] == "indeterminate"


def test_a_low_pass_hearing_nothing_says_nothing_about_the_satellite() -> None:
    """Most passes under 30° hear nothing anyway, satellite or not."""
    found = satellite_silent(
        evidence(
            satellite=SatelliteCounts(
                True, signals=0, silences=4, peak_elevation_deg=25.0
            )
        ),
        CONFIG,
    )

    assert not found.fired
    assert found.found["reason"] == "too low for its silence to say anything"


def test_the_catalogue_saying_it_is_off_is_categorical() -> None:
    found = satellite_silent(
        evidence(
            satellite=SatelliteCounts(
                False, signals=5, silences=0, peak_elevation_deg=HIGH
            )
        ),
        CONFIG,
    )

    assert found.fired
    assert found.support == 1.0
    assert found.found["reason"] == "catalogue"


@pytest.mark.parametrize(
    "heard", [{"outcome": "signal_no_decode"}, {"listening_confirmed": False}]
)
def test_only_a_confirmed_silence_can_be_the_satellite_s(
    heard: dict[str, object],
) -> None:
    found = satellite_silent(
        evidence(
            listening=listening(**heard),
            satellite=SatelliteCounts(
                True, signals=0, silences=3, peak_elevation_deg=HIGH
            ),
        ),
        CONFIG,
    )

    assert not found.fired


# --- obstruction ----------------------------------------------------------

BLOCK = (90.0, 40.0, 40.0)
"""A sector from 90° to 130°, blocked below 40°: the rise of every sweep."""


def blocked_history(count: int = 3) -> tuple[HistoryPass, ...]:
    return tuple(HistoryPass(sweep(blocked=BLOCK), -60.0) for _ in range(count))


def test_a_clean_symmetric_pass_loses_nothing_where_it_was_heard() -> None:
    assert lost_where_heard(sweep(), CONFIG) == ()


def test_samples_lost_on_one_side_only_are_lost_where_heard() -> None:
    lost = lost_where_heard(sweep(blocked=BLOCK), CONFIG)
    samples = sweep(blocked=BLOCK)

    assert lost
    assert all(samples[i].azimuth_deg < 130.0 for i in lost)


def test_a_sector_is_marked_only_from_enough_passes() -> None:
    one = build_map(blocked_history(1), baseline_dbfs=-60.2, declared=(), config=CONFIG)
    two = build_map(blocked_history(2), baseline_dbfs=-60.2, declared=(), config=CONFIG)

    assert one.ceilings == {}
    assert two.ceilings
    assert all(90 <= index * 10 < 130 for index in two.ceilings)


def test_a_pass_with_a_raised_floor_does_not_mark_the_map() -> None:
    raised = tuple(HistoryPass(sweep(blocked=BLOCK), -55.0) for _ in range(3))

    built = build_map(raised, baseline_dbfs=-60.2, declared=(), config=CONFIG)

    assert built.ceilings == {}


def test_a_heard_pass_losing_samples_in_marked_sectors_is_obstructed() -> None:
    found = obstruction(
        evidence(sky=sweep(blocked=BLOCK), history=blocked_history()), CONFIG
    )

    assert found.fired
    assert found.support >= 0.5
    assert found.found["lost_inside"] >= CONFIG.obstruction_min_samples


def test_a_silent_pass_wholly_inside_marked_sectors_is_obstructed() -> None:
    low = tuple(
        replace(s, snr_db=0.2) for s in sweep(peak_el=25.0, from_az=110.0, to_az=119.0)
    )

    found = obstruction(evidence(sky=low, history=blocked_history()), CONFIG)

    assert found.fired
    assert found.found["share"] == 1.0


def test_a_silent_pass_elsewhere_in_the_sky_is_not() -> None:
    elsewhere = tuple(replace(s, snr_db=0.2) for s in sweep(from_az=250.0, to_az=330.0))

    found = obstruction(evidence(sky=elsewhere, history=blocked_history()), CONFIG)

    assert not found.fired


def test_no_history_marks_nothing_and_names_nothing() -> None:
    found = obstruction(evidence(sky=sweep(blocked=BLOCK)), CONFIG)

    assert not found.fired
    assert found.found["marked"] == []


def test_a_loss_behind_the_declared_mask_is_an_obstruction() -> None:
    declared = (HorizonFloor(90.0, 40.0, 40.0),)

    found = obstruction(evidence(sky=sweep(blocked=BLOCK), declared=declared), CONFIG)

    assert found.fired


def test_a_raised_floor_explains_the_losses_instead() -> None:
    found = obstruction(
        evidence(
            sky=sweep(blocked=BLOCK),
            history=blocked_history(),
            noise=NoiseReading(-55.0, 30.0, -60.2, 20),
        ),
        CONFIG,
    )

    assert not found.fired
    assert found.found["reason"] == "floor raised"


def test_without_placed_samples_there_is_nothing_to_place() -> None:
    found = obstruction(evidence(history=blocked_history()), CONFIG)

    assert not found.fired
    assert found.found["reason"] == "no placed samples"


# --- interference ---------------------------------------------------------


def test_a_floor_raised_against_the_station_s_own_is_interference() -> None:
    found = interference(evidence(noise=NoiseReading(-56.0, 30.0, -60.0, 20)), CONFIG)

    assert found.fired
    assert found.found["lift_db"] == 4.0
    assert found.support == support_of(2.0)


def test_a_floor_within_the_threshold_is_not() -> None:
    found = interference(evidence(noise=NoiseReading(-59.0, 30.0, -60.0, 20)), CONFIG)

    assert not found.fired


@pytest.mark.parametrize(
    "noise",
    [
        NoiseReading(None, 30.0, -60.0, 20),
        NoiseReading(-50.0, 30.0, None, 0),
        NoiseReading(-50.0, 30.0, -60.0, 4),
    ],
)
def test_without_a_floor_or_a_baseline_it_cannot_say(noise: NoiseReading) -> None:
    assert not interference(evidence(noise=noise), CONFIG).fired


def test_the_profile_cell_is_cited_with_whether_its_gain_matches() -> None:
    cell = ProfileCell(3, 90.0, 45.0, 20, 4, 1.5, 12, 28.0, 32.0)

    found = interference(
        evidence(noise=NoiseReading(-56.0, 30.0, -60.0, 20, cell)), CONFIG
    )

    assert found.found["cell"] == {
        "profile_id": 3,
        "azimuth_deg": 90.0,
        "hour_start": 20,
        "noise_lift_db": 1.5,
        "sample_count": 12,
        "same_gain": True,
    }


# --- timing fault ---------------------------------------------------------


def timing(**changes: object) -> TimingEvidence:
    return replace(evidence().timing, **changes)  # type: ignore[arg-type]


def test_a_clean_clock_and_window_are_no_timing_fault() -> None:
    found = timing_fault(evidence(), CONFIG)

    assert not found.fired
    assert found.found["allowed_s"] == 34.0


@pytest.mark.parametrize("step", [timedelta(minutes=20), -timedelta(minutes=20)])
def test_listening_somewhere_else_in_time_is_a_timing_fault(step: timedelta) -> None:
    found = timing_fault(
        evidence(timing=timing(listening_span=(START - step, END - step))), CONFIG
    )

    assert found.fired
    assert found.found["listening_s"] == 1200.0


def test_a_clock_stamping_heartbeats_minutes_off_is_a_timing_fault() -> None:
    found = timing_fault(
        evidence(timing=timing(skews_s=(1200.1, 1199.9, 1200.0))), CONFIG
    )

    assert found.fired
    assert found.found["clock_skew_s"] == 1200.0
    assert found.support == support_of(1200.0 / 34.0)


def test_one_late_heartbeat_does_not_move_the_median() -> None:
    found = timing_fault(evidence(timing=timing(skews_s=(-0.2, -0.1, -95.0))), CONFIG)

    assert not found.fired


def test_a_reported_offset_beyond_its_uncertainty_is_a_timing_fault() -> None:
    found = timing_fault(
        evidence(timing=timing(reported_offsets=((-90.0, 2.0),))), CONFIG
    )

    assert found.fired
    assert found.found["reported_offset_s"] == 88.0


def test_a_recording_shifted_at_both_ends_is_a_timing_fault() -> None:
    late = timedelta(minutes=2)
    found = timing_fault(
        evidence(timing=timing(recording=(START + late, END + late))), CONFIG
    )

    assert found.fired
    assert found.found["recording_s"] == 120.0


def test_a_recording_trimmed_at_one_end_is_not_a_shift() -> None:
    found = timing_fault(
        evidence(timing=timing(recording=(START, END - timedelta(minutes=3)))), CONFIG
    )

    assert not found.fired


def test_a_station_that_left_no_trace_of_its_clock_cannot_be_judged() -> None:
    found = timing_fault(
        evidence(timing=timing(listening_span=None, skews_s=(), recording=None)),
        CONFIG,
    )

    assert not found.fired
    assert set(found.found) == {"allowed_s"}


# --- the support scale ----------------------------------------------------


def test_support_is_a_half_at_the_threshold_and_rises_towards_one() -> None:
    assert support_of(1.0) == 0.5
    assert 0.5 < support_of(2.0) < support_of(40.0) < 1.0
