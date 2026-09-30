"""Stage 25's four faults: drawn from a seed, and what each does to a pass.

No marker: pure. The track is supplied by hand, so each effect is checked
against a sky whose geometry the test states rather than one it has to find.

Reference: docs/DECISIONS.md D-105, D-253; docs/SCALE-AND-FAULTS.md
§ Ground-truth faults.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from meridian_sim.config import seed_for_pass, seed_for_station
from meridian_sim.evidence import (
    DETECT_SNR_DB,
    SNR_SAMPLE_COUNT,
    PassEvidence,
    evidence_for,
    station_noise_floor_dbfs,
)
from meridian_sim.fault_schedule import schedule_for
from meridian_sim.faults import (
    INTERFERENCE,
    OBSTRUCTION,
    SATELLITE_SILENT,
    SIGNAL_DEGRADATION,
)
from meridian_sim.outcomes import SimulatedOutcome, decide_outcome
from meridian_sim.sky_effects import PassContext, apply_sky_faults
from meridian_sim.sky_faults import (
    ActiveSkyFault,
    Degradation,
    Interference,
    Obstruction,
    Silence,
    SkyFault,
    SkyInForce,
    in_sector,
    silence_for,
    sky_faults_for,
)
from meridian_sim.sky_track import SkyPoint

STATION_SEED = seed_for_station(4471, 1)
START = datetime(2026, 9, 30, 21, 0, tzinfo=UTC)
WINDOW_S = 660.0
SATELLITE = "norad:57166"


def decoded_pass() -> tuple[int, SimulatedOutcome, PassEvidence]:
    """The first high pass this station decodes, with its clean evidence."""
    for index in range(200):
        seed = seed_for_pass(STATION_SEED, f"as_{index:05d}")
        outcome = decide_outcome(seed, 80.0)
        if outcome.outcome == "decoded":
            evidence = evidence_for(
                seed, outcome, WINDOW_S, station_noise_floor_dbfs(STATION_SEED)
            )
            assert evidence is not None
            return seed, outcome, evidence
    raise AssertionError("no decoded pass")


def unheard_pass() -> tuple[int, SimulatedOutcome, PassEvidence]:
    """A pass that heard nothing, with the evidence of hearing nothing."""
    for index in range(200):
        seed = seed_for_pass(STATION_SEED, f"as_{index:05d}")
        outcome = decide_outcome(seed, 6.0)
        if outcome.outcome == "no_signal":
            evidence = evidence_for(
                seed, outcome, WINDOW_S, station_noise_floor_dbfs(STATION_SEED)
            )
            assert evidence is not None
            return seed, outcome, evidence
    raise AssertionError("no unheard pass")


def context(
    seed: int,
    points: tuple[SkyPoint, ...] | None = None,
    satellite_id: str = SATELLITE,
) -> PassContext:
    """A pass starting at 21:00 UTC, its samples a minute apart or so."""
    step = WINDOW_S / (SNR_SAMPLE_COUNT - 1)
    instants = tuple(
        START + timedelta(seconds=i * step) for i in range(SNR_SAMPLE_COUNT)
    )

    def directions() -> tuple[SkyPoint, ...]:
        if points is None:
            raise AssertionError("the track was computed for a fault that needs none")
        return points

    return PassContext(seed, satellite_id, instants, WINDOW_S, directions)


def everywhere(azimuth: float, elevation: float) -> tuple[SkyPoint, ...]:
    return (SkyPoint(azimuth, elevation),) * SNR_SAMPLE_COUNT


def active(kind: str, shape: object, onset: datetime = START) -> ActiveSkyFault:
    return ActiveSkyFault(SkyFault(kind, shape, 0), onset)  # type: ignore[arg-type]


# --- drawn from a seed --------------------------------------------------------


def test_every_fault_is_reproducible_from_its_seed() -> None:
    """The roadmap's first test: the same seed, the same faults, the same shapes."""
    assert sky_faults_for(STATION_SEED, "sky") == sky_faults_for(STATION_SEED, "sky")
    assert silence_for(4471, "sky", SATELLITE) == silence_for(4471, "sky", SATELLITE)
    assert sky_faults_for(STATION_SEED, "sky") != sky_faults_for(
        seed_for_station(4471, 2), "sky"
    )


def test_each_scenario_draws_only_its_own_kinds() -> None:
    assert sky_faults_for(STATION_SEED, "clean") == ()
    assert [one.kind for one in sky_faults_for(STATION_SEED, "sky")] == [
        SIGNAL_DEGRADATION,
        OBSTRUCTION,
        INTERFERENCE,
    ]
    assert [one.kind for one in sky_faults_for(STATION_SEED, "obstruction")] == [
        OBSTRUCTION
    ]
    assert silence_for(4471, "degradation", None) is None


def test_a_silence_with_no_satellite_named_is_refused() -> None:
    """A silent run that silenced nothing would look like a run with no fault."""
    with pytest.raises(ValueError, match="satellite"):
        silence_for(4471, "silent", None)


def test_a_silence_ends_and_the_station_faults_do_not() -> None:
    silence = silence_for(4471, "silent", SATELLITE)
    assert silence is not None
    assert silence.last_tick is not None
    assert not silence.active_at(silence.last_tick + 1)
    assert all(one.last_tick is None for one in sky_faults_for(STATION_SEED, "sky"))


def test_the_schedule_opens_each_sky_fault_at_its_onset() -> None:
    schedule = schedule_for(STATION_SEED, "sky")
    for one in schedule.sky:
        assert one.kind not in schedule.active_at(one.first_tick - 1)
        assert one.kind in schedule.active_at(one.first_tick)
        assert one.kind in schedule.active_at(one.first_tick + 10_000)
    windows = {window.kind for window in schedule.windows(1_000)}
    assert {SIGNAL_DEGRADATION, OBSTRUCTION, INTERFERENCE} <= windows


def test_an_onset_is_kept_while_a_fault_holds_and_forgotten_when_it_ends() -> None:
    fault = SkyFault(SATELLITE_SILENT, Silence(SATELLITE), 3, 5)
    in_force = SkyInForce()
    first = in_force.update((fault,), frozenset({SATELLITE_SILENT}), START)
    later = in_force.update(
        (fault,), frozenset({SATELLITE_SILENT}), START + timedelta(hours=1)
    )
    assert first[0].onset_at == later[0].onset_at == START

    assert in_force.update((fault,), frozenset(), START) == ()
    again = in_force.update(
        (fault,), frozenset({SATELLITE_SILENT}), START + timedelta(days=1)
    )
    assert again[0].onset_at == START + timedelta(days=1)


def test_a_sector_runs_clockwise_and_wraps_through_north() -> None:
    assert in_sector(355.0, 340.0, 40.0)
    assert in_sector(10.0, 340.0, 40.0)
    assert not in_sector(20.0, 340.0, 40.0)
    assert not in_sector(339.9, 340.0, 40.0)


# --- what each does to a pass -------------------------------------------------


def test_nothing_in_force_changes_nothing() -> None:
    seed, outcome, evidence = decoded_pass()

    result = apply_sky_faults(outcome, evidence, (), context(seed))

    assert (result.outcome, result.evidence, result.kinds) == (outcome, evidence, ())


def test_a_degradation_weakens_every_sample_by_what_it_has_accrued() -> None:
    """Two days at 1.5 dB a day: three decibels off every sample, floor unmoved."""
    seed, outcome, evidence = decoded_pass()
    fault = active(SIGNAL_DEGRADATION, Degradation(1.5), START - timedelta(days=2))

    result = apply_sky_faults(outcome, evidence, (fault,), context(seed))

    assert result.kinds == (SIGNAL_DEGRADATION,)
    assert result.evidence is not None
    assert result.evidence.noise_floor_dbfs == evidence.noise_floor_dbfs
    for before, after in zip(evidence.snr_db, result.evidence.snr_db, strict=True):
        assert after == pytest.approx(before - 3.0, abs=0.06)
    assert result.evidence.frames_decoded < evidence.frames_decoded


def test_enough_degradation_loses_the_pass_entirely() -> None:
    seed, outcome, evidence = decoded_pass()
    fault = active(SIGNAL_DEGRADATION, Degradation(8.0), START - timedelta(days=5))

    result = apply_sky_faults(outcome, evidence, (fault,), context(seed))

    assert result.outcome.outcome == "no_signal"
    assert result.outcome.peak_snr_db is None
    assert result.evidence is not None
    assert (result.evidence.frames_decoded, result.evidence.frames_failed) == (0, 0)


def test_a_degradation_before_its_onset_did_nothing() -> None:
    seed, outcome, evidence = decoded_pass()
    fault = active(SIGNAL_DEGRADATION, Degradation(8.0), START + timedelta(hours=1))

    assert apply_sky_faults(outcome, evidence, (fault,), context(seed)).kinds == ()


def test_an_obstruction_blocks_only_samples_behind_it() -> None:
    """A sector that holds every low sample: the pass hears nothing there."""
    seed, outcome, evidence = decoded_pass()
    fault = active(OBSTRUCTION, Obstruction(90.0, 60.0, 30.0))
    points = tuple(
        SkyPoint(120.0, 20.0) if i < 8 else SkyPoint(300.0, 60.0)
        for i in range(SNR_SAMPLE_COUNT)
    )

    result = apply_sky_faults(outcome, evidence, (fault,), context(seed, points))

    assert result.kinds == (OBSTRUCTION,)
    assert result.evidence is not None
    assert all(value < DETECT_SNR_DB for value in result.evidence.snr_db[:8])
    assert result.evidence.snr_db[8:] == evidence.snr_db[8:]
    assert result.evidence.noise_floor_dbfs == evidence.noise_floor_dbfs


def test_a_pass_that_never_crosses_the_obstruction_is_untouched() -> None:
    seed, outcome, evidence = decoded_pass()
    fault = active(OBSTRUCTION, Obstruction(90.0, 60.0, 30.0))

    result = apply_sky_faults(
        outcome, evidence, (fault,), context(seed, everywhere(300.0, 20.0))
    )

    assert result.kinds == ()


def test_interference_raises_the_floor_in_its_sector_and_hours() -> None:
    """21:00 UTC is inside a window from 20:00 for three hours."""
    seed, outcome, evidence = decoded_pass()
    fault = active(INTERFERENCE, Interference(200.0, 60.0, 20, 3, 10.0))

    result = apply_sky_faults(
        outcome, evidence, (fault,), context(seed, everywhere(230.0, 40.0))
    )

    assert result.kinds == (INTERFERENCE,)
    assert result.evidence is not None
    assert result.evidence.noise_floor_dbfs == pytest.approx(
        evidence.noise_floor_dbfs + 10.0, abs=0.06
    )
    assert max(result.evidence.snr_db) == pytest.approx(
        max(evidence.snr_db) - 10.0, abs=0.06
    )


def test_interference_outside_its_hours_does_nothing() -> None:
    seed, outcome, evidence = decoded_pass()
    fault = active(INTERFERENCE, Interference(200.0, 60.0, 3, 3, 10.0))

    result = apply_sky_faults(
        outcome, evidence, (fault,), context(seed, everywhere(230.0, 40.0))
    )

    assert result.kinds == ()


def test_interference_on_a_pass_that_heard_nothing_still_shows_in_the_floor() -> None:
    """The floor is the evidence, heard or not."""
    seed, outcome, evidence = unheard_pass()
    fault = active(INTERFERENCE, Interference(200.0, 60.0, 20, 3, 10.0))

    result = apply_sky_faults(
        outcome, evidence, (fault,), context(seed, everywhere(230.0, 40.0))
    )

    assert result.kinds == (INTERFERENCE,)
    assert result.outcome.outcome == "no_signal"
    assert result.evidence is not None
    assert result.evidence.noise_floor_dbfs > evidence.noise_floor_dbfs + 9.9


def test_part_of_a_pass_raises_the_floor_by_its_share_of_the_power() -> None:
    """Thirteen of 25 samples ten decibels up: the mean power, about 7.5 dB up."""
    seed, outcome, evidence = unheard_pass()
    fault = active(INTERFERENCE, Interference(200.0, 60.0, 20, 3, 10.0))
    points = tuple(
        SkyPoint(230.0, 40.0) if i % 2 == 0 else SkyPoint(10.0, 40.0)
        for i in range(SNR_SAMPLE_COUNT)
    )

    result = apply_sky_faults(outcome, evidence, (fault,), context(seed, points))

    assert result.evidence is not None
    assert result.evidence.noise_floor_dbfs == pytest.approx(
        evidence.noise_floor_dbfs + 7.6, abs=0.15
    )


def test_a_silent_satellite_is_heard_by_nobody() -> None:
    seed, outcome, evidence = decoded_pass()
    fault = active(SATELLITE_SILENT, Silence(SATELLITE))

    result = apply_sky_faults(outcome, evidence, (fault,), context(seed))

    assert result.kinds == (SATELLITE_SILENT,)
    assert result.outcome.outcome == "no_signal"
    assert result.evidence is not None
    assert max(result.evidence.snr_db) < DETECT_SNR_DB
    assert result.evidence.noise_floor_dbfs == evidence.noise_floor_dbfs


def test_another_satellite_falling_silent_does_nothing_to_this_pass() -> None:
    seed, outcome, evidence = decoded_pass()
    fault = active(SATELLITE_SILENT, Silence("norad:59051"))

    assert apply_sky_faults(outcome, evidence, (fault,), context(seed)).kinds == ()


def test_the_same_faults_give_the_same_pass_every_time() -> None:
    seed, outcome, evidence = decoded_pass()
    faults = (
        active(SIGNAL_DEGRADATION, Degradation(2.0), START - timedelta(days=1)),
        active(OBSTRUCTION, Obstruction(90.0, 60.0, 30.0)),
        active(INTERFERENCE, Interference(200.0, 60.0, 20, 3, 10.0)),
    )
    points = tuple(
        SkyPoint(100.0 + 10 * i, 5.0 + 3 * i) for i in range(SNR_SAMPLE_COUNT)
    )
    run: Callable[[], object] = lambda: apply_sky_faults(  # noqa: E731
        outcome, evidence, faults, context(seed, points)
    )

    assert run() == run()


def test_an_aborted_pass_measured_nothing_a_fault_could_change() -> None:
    aborted = SimulatedOutcome("aborted", None, None, None)
    fault = active(SATELLITE_SILENT, Silence(SATELLITE))

    result = apply_sky_faults(aborted, None, (fault,), context(1))

    assert (result.evidence, result.kinds) == (None, ())


def test_a_faint_pass_the_fault_took_nothing_from_is_still_heard() -> None:
    """Peak 2.5 dB, under the bar the outcome model's own draw cleared.

    Interference in the first few samples, far from the peak, touches the pass
    and takes nothing from its peak: it must not turn a heard pass into one
    that heard nothing, nor fail to find a detection instant.
    """
    faint = SimulatedOutcome("signal_no_decode", 120.0, 2.5, (0,) * 24)
    snr = tuple(2.5 if i == 12 else -5.0 for i in range(SNR_SAMPLE_COUNT))
    evidence = PassEvidence(-55.0, 30.0, snr, 0, 0)
    fault = active(INTERFERENCE, Interference(200.0, 60.0, 20, 3, 10.0))
    points = tuple(
        SkyPoint(230.0, 40.0) if i < 3 else SkyPoint(10.0, 40.0)
        for i in range(SNR_SAMPLE_COUNT)
    )

    result = apply_sky_faults(faint, evidence, (fault,), context(7, points))

    assert result.kinds == (INTERFERENCE,)
    assert result.outcome.outcome == "signal_no_decode"
    assert result.outcome.detection_offset_s == 120.0
    assert result.outcome.peak_snr_db == 2.5
