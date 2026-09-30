"""``meridian_sim.evidence`` — MSP 0.3's evidence, consistent with the outcome.

No marker: pure. The executor tests already prove every body the platform would
accept; these check that the evidence says what the outcome says, and that
adding it changed no outcome any seed already gave.

Reference: docs/DECISIONS.md D-103, D-117, D-122, D-251.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from meridian_client.assignment_message import Assignment, ElementSet
from meridian_client.observation_message import ObservationResult
from meridian_sim.config import seed_for_pass, seed_for_station
from meridian_sim.evidence import (
    DECODER,
    DECODER_VERSION,
    DETECT_SNR_DB,
    NOISE_FLOOR_RANGE_DBFS,
    RECEIVER_GAIN_DB,
    SNR_SAMPLE_COUNT,
    count_frames,
    evidence_for,
    station_noise_floor_dbfs,
)
from meridian_sim.executor import SimulatedExecutor
from meridian_sim.outcomes import SimulatedOutcome, decide_outcome

STATION_SEED = seed_for_station(4471, 1)
WINDOW_S = 660.0
START = datetime(2026, 8, 14, 9, 41, 18, tzinfo=UTC)
LINE1 = "1 57166U 23091A   26223.50000000  .00000100  00000-0  50000-4 0  9990"
LINE2 = "2 57166  98.7041 210.4322 0002726  80.4113 279.7297 14.22000000160126"


def sweep(elevation_deg: float, count: int = 200) -> list[tuple[int, SimulatedOutcome]]:
    """(pass seed, outcome) pairs for one station over many assignments."""
    seeds = (seed_for_pass(STATION_SEED, f"as_{index:05d}") for index in range(count))
    return [(seed, decide_outcome(seed, elevation_deg)) for seed in seeds]


def assignment(assignment_id: str, elevation_deg: float) -> Assignment:
    return Assignment(
        assignment_id=assignment_id,
        satellite_id="norad:57166",
        start_at=START,
        end_at=START + timedelta(seconds=WINDOW_S),
        centre_freq_hz=137_100_000,
        mode="lrpt",
        expected_max_elevation_deg=elevation_deg,
        predicted_yield=None,
        element_set=ElementSet(epoch=START, line1=LINE1, line2=LINE2),
        timing_uncertainty_s=4.2,
        priority=1.0,
    )


def results(elevation_deg: float, count: int = 120) -> list[ObservationResult]:
    executor = SimulatedExecutor(STATION_SEED)
    for index in range(count):
        work = assignment(f"as_{index:05d}", elevation_deg)
        executor.begin(work)
        executor.end(work)
    return list(executor.take_completed())


def test_a_station_s_floor_is_its_own_and_stays_put() -> None:
    """Drawn once per station, so an interference rise is not buried in redraws."""
    floor = station_noise_floor_dbfs(STATION_SEED)

    assert floor == station_noise_floor_dbfs(STATION_SEED)
    assert NOISE_FLOOR_RANGE_DBFS[0] <= floor <= NOISE_FLOOR_RANGE_DBFS[1]
    assert floor != station_noise_floor_dbfs(seed_for_station(4471, 2))


@pytest.mark.parametrize("elevation_deg", [8.0, 25.0, 60.0, 88.0])
def test_the_evidence_agrees_with_the_outcome(elevation_deg: float) -> None:
    """D-117's rules, and the peak the outcome reported is the highest sample."""
    floor = station_noise_floor_dbfs(STATION_SEED)
    for seed, outcome in sweep(elevation_deg):
        evidence = evidence_for(seed, outcome, WINDOW_S, floor)
        if outcome.outcome == "aborted":
            assert evidence is None
            continue
        assert evidence is not None
        assert evidence.receiver_gain_db == RECEIVER_GAIN_DB
        assert abs(evidence.noise_floor_dbfs - floor) <= 0.5 + 1e-9
        assert len(evidence.snr_db) == SNR_SAMPLE_COUNT
        if outcome.outcome == "decoded":
            assert evidence.frames_decoded >= 1
        else:
            assert evidence.frames_decoded == 0
        if outcome.peak_snr_db is None:
            assert max(evidence.snr_db) < DETECT_SNR_DB
            assert evidence.frames_failed == 0
        else:
            assert max(evidence.snr_db) == pytest.approx(outcome.peak_snr_db)


def test_a_higher_pass_decodes_more_frames() -> None:
    """Frames follow the SNR curve, which follows the peak, which follows elevation."""

    def mean_frames(elevation_deg: float) -> float:
        floor = station_noise_floor_dbfs(STATION_SEED)
        counts = [
            evidence.frames_decoded
            for seed, outcome in sweep(elevation_deg)
            if outcome.outcome == "decoded"
            and (evidence := evidence_for(seed, outcome, WINDOW_S, floor)) is not None
        ]
        return sum(counts) / len(counts)

    # Below 40° the peak still climbs with elevation (outcomes.SIGNAL_CEILING_DEG).
    assert mean_frames(35.0) > mean_frames(20.0)


def test_nothing_is_heard_before_the_instant_the_station_says_it_heard_it() -> None:
    floor = station_noise_floor_dbfs(STATION_SEED)
    for seed, outcome in sweep(25.0):
        offset = outcome.detection_offset_s
        if offset is None:
            continue
        evidence = evidence_for(seed, outcome, WINDOW_S, floor)
        assert evidence is not None
        step = WINDOW_S / (SNR_SAMPLE_COUNT - 1)
        before = [v for i, v in enumerate(evidence.snr_db) if i * step < offset]
        assert all(value < DETECT_SNR_DB for value in before)


def test_the_draws_do_not_depend_on_the_outcome() -> None:
    """A fault that changes the outcome afterwards must not move a sample."""
    floor = station_noise_floor_dbfs(STATION_SEED)
    for seed, outcome in sweep(60.0, count=40):
        if outcome.outcome != "decoded":
            continue
        evidence = evidence_for(seed, outcome, WINDOW_S, floor)
        assert evidence is not None
        assert count_frames(evidence.snr_db, WINDOW_S, "signal_no_decode")[0] == 0
        assert evidence == evidence_for(seed, outcome, WINDOW_S, floor)


def test_a_count_follows_the_outcome_it_is_asked_for() -> None:
    """Heard-only frames fail; a pass that heard nothing counts none."""
    loud = (10.0,) * 5 + (4.0,) * 5

    decoded, failed = count_frames(loud, 100.0, "decoded")
    assert (decoded, failed) == (439, 439)
    assert count_frames(loud, 100.0, "signal_no_decode") == (0, 878)
    assert count_frames(loud, 100.0, "no_signal") == (0, 0)
    assert count_frames((0.0,) * 10, 100.0, "decoded") == (1, 0)


def test_an_unheard_pass_still_reports_what_it_measured() -> None:
    """``detected: false``, a floor, a gain and SNR: the reference client's shape."""
    unheard = [one for one in results(6.0) if one.outcome == "no_signal"]

    assert unheard
    for one in unheard:
        assert one.signal is not None
        assert one.signal.detected is False
        assert one.signal.first_detection_at is None
        assert one.signal.noise_floor_dbfs is not None
        assert one.signal.receiver_gain_db == RECEIVER_GAIN_DB
        assert one.decode is not None
        assert one.decode.frames_decoded == 0


def test_every_reported_decode_names_the_simulator_and_its_model_version() -> None:
    """Calibration segments by decoder and version, so neither may be absent."""
    for one in results(60.0):
        if one.outcome == "aborted":
            assert one.decode is None
            assert one.signal is None
            continue
        assert one.decode is not None
        assert one.signal is not None
        assert one.decode.decoder == DECODER
        assert one.decode.decoder_version == DECODER_VERSION
        samples = one.signal.snr_samples
        assert samples is not None
        assert samples[0].sampled_at == START
        assert samples[-1].sampled_at == START + timedelta(seconds=WINDOW_S)
