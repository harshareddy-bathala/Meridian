"""``meridian_sim.outcomes`` — the model, as a table a reader can check.

No marker: numbers in, numbers out. The statistical assertions below sweep many
seeds rather than testing one draw, because a probability model cannot be
checked by a single sample and a test that tried would be a test that passed by
luck.

Reference: docs/DECISIONS.md D-077, D-078.
"""

from __future__ import annotations

import pytest

from meridian_sim.config import seed_for_pass, seed_for_station
from meridian_sim.outcomes import (
    DOPPLER_SAMPLE_COUNT,
    MAX_DOPPLER_OFFSET_HZ,
    SimulatedOutcome,
    decide_outcome,
)

MASTER_SEED = 4471
DETECTED = frozenset({"decoded", "signal_no_decode"})


def sweep(elevation_deg: float, count: int = 400) -> list[SimulatedOutcome]:
    """``count`` independent passes at one elevation, from distinct seeds."""
    station = seed_for_station(MASTER_SEED, 1)
    return [
        decide_outcome(seed_for_pass(station, f"as_{index:05d}"), elevation_deg)
        for index in range(count)
    ]


def decode_rate(outcomes: list[SimulatedOutcome]) -> float:
    """Fraction of a sweep that decoded."""
    return sum(one.outcome == "decoded" for one in outcomes) / len(outcomes)


def test_one_seed_and_one_elevation_give_one_answer() -> None:
    """The claim the whole stage rests on, at its smallest."""
    assert decide_outcome(12345, 42.0) == decide_outcome(12345, 42.0)


def test_a_different_seed_gives_a_different_pass() -> None:
    """A fleet whose passes were all identical would exercise one code path."""
    results = {decide_outcome(seed, 42.0) for seed in range(200)}

    assert len(results) > 100


def test_decode_rate_rises_with_elevation() -> None:
    """D-078's hazard, stated as the property that creates it.

    This is the correlation that makes simulated traffic worth generating and
    the same correlation that bars it from any evaluation set. Asserting it here
    is deliberate: if it ever stops holding, the exclusion rule stops being
    necessary and somebody should be told.
    """
    low = decode_rate(sweep(8.0))
    middle = decode_rate(sweep(30.0))
    high = decode_rate(sweep(75.0))

    assert low < middle < high


def test_a_grazing_pass_is_never_heard() -> None:
    """Below the detection floor there is nothing to hear, seed regardless."""
    assert all(one.outcome not in DETECTED for one in sweep(4.0))


def test_a_zenith_pass_is_essentially_always_decoded() -> None:
    """Above both ceilings only the abort draw can spoil it."""
    outcomes = sweep(89.0)

    assert decode_rate(outcomes) > 0.95
    assert all(one.outcome in {"decoded", "aborted"} for one in outcomes)


def test_every_outcome_the_sky_can_produce_is_reachable() -> None:
    """Four of MSP §4.4's five, across a range of geometries."""
    seen = {
        one.outcome for elevation in (8.0, 20.0, 45.0, 80.0) for one in sweep(elevation)
    }

    assert seen == {"decoded", "signal_no_decode", "no_signal", "aborted"}


def test_geometry_never_produces_not_attempted() -> None:
    """It means a station took the work and failed to start — not something the
    sky does. It belongs to the fault schedule, and inventing it here would
    record an operational failure that never happened."""
    for elevation in (1.0, 5.0, 15.0, 45.0, 90.0):
        assert all(one.outcome != "not_attempted" for one in sweep(elevation))


@pytest.mark.parametrize("elevation_deg", [8.0, 25.0, 60.0, 88.0])
def test_a_detection_always_carries_its_measurements(elevation_deg: float) -> None:
    """The platform refuses a body whose outcome contradicts its signal block.

    A result asserting a detection with no timestamp would be built into a body
    the endpoint answers `malformed`, and the station would set it aside — one
    pass silently lost per inconsistency.
    """
    for one in sweep(elevation_deg):
        if one.outcome in DETECTED:
            assert one.detection_offset_s is not None
            assert one.peak_snr_db is not None
            assert one.doppler_offsets_hz is not None


@pytest.mark.parametrize("elevation_deg", [8.0, 25.0, 60.0, 88.0])
def test_nothing_heard_carries_no_measurements(elevation_deg: float) -> None:
    """`no_signal` and `aborted` must not report an SNR nobody measured."""
    for one in sweep(elevation_deg):
        if one.outcome not in DETECTED:
            assert one.detection_offset_s is None
            assert one.peak_snr_db is None
            assert one.doppler_offsets_hz is None


def test_the_doppler_series_sweeps_from_approaching_to_receding() -> None:
    """Positive at acquisition, negative at loss, zero near culmination.

    The shape a closing-then-opening range produces. It is a shape and not a
    computation — the platform's `orbit.doppler` is what derives the real thing
    from a range rate.
    """
    detected = next(one for one in sweep(80.0) if one.outcome in DETECTED)
    samples = detected.doppler_offsets_hz
    assert samples is not None

    assert len(samples) == DOPPLER_SAMPLE_COUNT
    assert samples[0] > 2000
    assert samples[-1] < -2000
    assert abs(samples[len(samples) // 2]) < 500


def test_the_doppler_series_stays_inside_the_excursion_it_claims() -> None:
    """±3 kHz at 137 MHz, plus the jitter a real estimate carries."""
    for one in sweep(70.0):
        if one.doppler_offsets_hz is not None:
            assert all(
                abs(sample) <= MAX_DOPPLER_OFFSET_HZ + 100
                for sample in one.doppler_offsets_hz
            )


def test_the_doppler_series_is_well_under_the_protocol_cap() -> None:
    """MSP §6 caps the array at 512, which is a limit rather than a target."""
    assert DOPPLER_SAMPLE_COUNT < 512


def test_doppler_offsets_are_whole_hertz() -> None:
    """Frequencies are integers in this project — never floats, never megahertz."""
    detected = next(one for one in sweep(70.0) if one.outcome in DETECTED)
    assert detected.doppler_offsets_hz is not None

    assert all(isinstance(sample, int) for sample in detected.doppler_offsets_hz)


def test_a_higher_pass_is_heard_sooner_and_louder() -> None:
    """Both measurements track the geometry, not just the outcome does."""
    low = [one for one in sweep(15.0) if one.outcome in DETECTED]
    high = [one for one in sweep(85.0) if one.outcome in DETECTED]

    assert _mean(one.detection_offset_s for one in low) > _mean(
        one.detection_offset_s for one in high
    )
    assert _mean(one.peak_snr_db for one in low) < _mean(
        one.peak_snr_db for one in high
    )


def _mean(values: object) -> float:
    """Mean of an iterable of measurements known to be present."""
    collected = [one for one in values if one is not None]  # type: ignore[union-attr]
    return sum(collected) / len(collected)
