"""D-122's table, one row at a time, and the rules around it.

Every result here is also built into a body with the client's own function and
validated with the platform's request model, because a result the table produces
and the platform refuses would lose the pass permanently.

Marked as a unit test by living in ``tests/unit``: pure functions, no disk.

Reference: docs/MSP-SPEC.md §4.4; docs/DECISIONS.md D-032, D-072, D-100, D-117,
D-122.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from meridian.api.models.observation import ObservationRequestBody
from meridian_client.assignment_message import Assignment, ElementSet
from meridian_client.observation_message import (
    MAX_SNR_SAMPLES,
    ObservationResult,
    build_observation_body,
)
from meridian_client.reception.decode_report import (
    DecodeFailure,
    DecodeReport,
    SnrPoint,
)
from meridian_client.reception.outcome_rules import (
    OutcomePolicy,
    ReceptionFacts,
    coverage_of,
    derive_result,
    reduce_snr,
)
from meridian_client.reception.protocols import Recording

START_AT = datetime(2026, 8, 14, 9, 41, 18, tzinfo=UTC)
END_AT = START_AT + timedelta(minutes=11)
FIRST_SAMPLE_AT = START_AT - timedelta(seconds=4)
POLICY = OutcomePolicy(snr_threshold_db=3.0, minimum_coverage=0.8)

ASSIGNMENT = Assignment(
    assignment_id="as_44b2",
    satellite_id="norad:57166",
    start_at=START_AT,
    end_at=END_AT,
    centre_freq_hz=137_900_000,
    mode="lrpt",
    expected_max_elevation_deg=61.4,
    predicted_yield=None,
    element_set=ElementSet(
        epoch=datetime(2026, 8, 14, 2, 11, 0, tzinfo=UTC),
        line1="1 57166U 23091A   26223.50000000  .00000100  00000-0  50000-4 0  9990",
        line2="2 57166  98.7041 210.4322 0002726  80.4113 279.7297 14.22000000160126",
    ),
    timing_uncertainty_s=4.2,
    priority=1.0,
)


def recording(**changes: object) -> Recording:
    """A complete widened capture: 4 s before the window to 4 s after it."""
    duration_s = (END_AT - START_AT).total_seconds() + 8
    base = Recording(
        path=Path("/state/captures/as_44b2/recording.u8"),
        sample_rate_hz=1_000,
        sample_format="u8",
        centre_freq_hz=137_900_000,
        sample_count=int(duration_s * 1_000),
        first_sample_at=FIRST_SAMPLE_AT,
        stopped_at=FIRST_SAMPLE_AT + timedelta(seconds=duration_s),
        gain_db=32.8,
        interrupted=False,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def report(**changes: object) -> DecodeReport:
    base = DecodeReport(
        decoder="satdump",
        decoder_version="1.2.2",
        frames_decoded=412,
        frames_failed=37,
        first_frame_offset_s=35.0,
        snr=(SnrPoint(20.0, 1.0), SnrPoint(40.0, 3.5), SnrPoint(330.0, 11.4)),
        noise_floor_dbfs=-52.3,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def derive(**facts: object) -> ObservationResult:
    result = derive_result(ReceptionFacts(ASSIGNMENT, **facts), POLICY)  # type: ignore[arg-type]
    body = build_observation_body(result, "st_7fa3c1")
    ObservationRequestBody.model_validate(body)
    return result


def at(offset_s: float) -> datetime:
    return FIRST_SAMPLE_AT + timedelta(seconds=offset_s)


# --- the table, row by row ----------------------------------------------------


def test_row_1_a_reception_that_never_started_is_not_attempted() -> None:
    result = derive(reason="no decoder is configured for lrpt")

    assert result.outcome == "not_attempted"
    assert result.signal is None
    assert result.decode is None
    assert (result.started_at, result.ended_at) == (START_AT, END_AT)
    assert result.client_notes == "no decoder is configured for lrpt"


@pytest.mark.parametrize(
    "decode", [DecodeFailure("the decoder timed out after 900 s"), None]
)
def test_row_2_a_failed_decode_is_aborted_with_no_evidence(
    decode: DecodeFailure | None,
) -> None:
    result = derive(recording=recording(), decode=decode)

    assert result.outcome == "aborted"
    assert result.signal is None
    assert result.decode is None
    assert result.started_at == FIRST_SAMPLE_AT
    assert result.client_notes == (decode.reason if decode else "no decode ran")


def test_row_3_an_interrupted_capture_is_aborted_with_its_evidence() -> None:
    result = derive(recording=recording(interrupted=True), decode=report())

    assert result.outcome == "aborted"
    assert result.signal is not None
    assert result.signal.detected
    assert result.decode is not None
    assert result.decode.frames_decoded == 412
    assert "capture was interrupted" in (result.client_notes or "")


def test_row_3_a_capture_covering_too_little_of_the_window_is_aborted() -> None:
    short = recording(stopped_at=START_AT + timedelta(minutes=5))

    result = derive(recording=short, decode=report())

    assert result.outcome == "aborted"
    assert "covered 45% of the window" in (result.client_notes or "")


def test_row_4_frames_with_a_detection_instant_are_decoded() -> None:
    result = derive(recording=recording(), decode=report())

    assert result.outcome == "decoded"
    assert result.signal is not None
    assert result.signal.first_detection_at == at(35.0)
    assert result.client_notes == "detected by first decoded frame"


def test_row_4_an_earlier_snr_crossing_is_the_detection() -> None:
    """The earliest evidence wins, and the notes say which it was (D-100)."""
    result = derive(recording=recording(), decode=report(first_frame_offset_s=60.0))

    assert result.signal is not None
    assert result.signal.first_detection_at == at(40.0)
    assert result.client_notes == "detected by first SNR sample at or above 3 dB"


def test_row_5_frames_with_no_timing_are_aborted_without_a_signal() -> None:
    """MSP §4.4: the timing measurement admits no guessed instant."""
    result = derive(
        recording=recording(), decode=report(first_frame_offset_s=None, snr=None)
    )

    assert result.outcome == "aborted"
    assert result.signal is None
    assert result.decode is not None
    assert result.decode.frames_decoded == 412
    assert result.client_notes == "frames were decoded with no timing"


@pytest.mark.parametrize("frames", [0, None])
def test_row_6_a_signal_with_no_frames_is_signal_no_decode(frames: int | None) -> None:
    result = derive(
        recording=recording(),
        decode=report(
            frames_decoded=frames, frames_failed=None, first_frame_offset_s=None
        ),
    )

    assert result.outcome == "signal_no_decode"
    assert result.signal is not None
    assert result.signal.first_detection_at == at(40.0)


def test_row_7_zero_frames_and_nothing_above_the_threshold_is_no_signal() -> None:
    """Listening, verifiably, and nothing there: the measurement, with its floor."""
    quiet = report(
        frames_decoded=0,
        first_frame_offset_s=None,
        snr=(SnrPoint(20.0, 0.4), SnrPoint(300.0, 2.9)),
    )

    result = derive(recording=recording(), decode=quiet)

    assert result.outcome == "no_signal"
    assert result.signal is not None
    assert not result.signal.detected
    assert result.signal.peak_snr_db == 2.9
    assert result.signal.noise_floor_dbfs == -52.3
    assert result.signal.receiver_gain_db == 32.8
    assert result.client_notes is None


def test_row_7_no_signal_needs_no_snr_series() -> None:
    result = derive(
        recording=recording(),
        decode=report(frames_decoded=0, first_frame_offset_s=None, snr=None),
    )

    assert result.outcome == "no_signal"


@pytest.mark.parametrize(
    "snr", [None, (SnrPoint(20.0, 0.4),)], ids=["nothing-measured", "all-below"]
)
def test_row_8_no_frame_count_and_no_signal_cannot_establish_absence(
    snr: tuple[SnrPoint, ...] | None,
) -> None:
    """Not ``no_signal``: a decoder that counts no frames never said it found none."""
    result = derive(
        recording=recording(),
        decode=report(
            frames_decoded=None, frames_failed=None, first_frame_offset_s=None, snr=snr
        ),
    )

    assert result.outcome == "aborted"
    assert (
        result.client_notes
        == "no frames were counted and no signal reached the threshold"
    )


# --- evidence -----------------------------------------------------------------


def test_what_was_not_measured_is_omitted() -> None:
    bare = report(
        decoder_version=None, frames_failed=None, snr=None, noise_floor_dbfs=None
    )

    result = derive(recording=recording(gain_db=None), decode=bare)

    assert result.signal is not None
    assert result.signal.peak_snr_db is None
    assert result.signal.snr_samples is None
    assert result.signal.receiver_gain_db is None
    assert result.decode is not None
    assert result.decode.decoder_version is None


def test_a_noise_floor_is_dropped_when_no_gain_was_recorded() -> None:
    """D-117: a floor in dBFS means nothing without the gain it was taken at."""
    result = derive(recording=recording(gain_db=None), decode=report())

    assert result.signal is not None
    assert result.signal.noise_floor_dbfs is None


def test_snr_samples_are_placed_on_the_recordings_timeline() -> None:
    result = derive(recording=recording(), decode=report())

    assert result.signal is not None
    assert result.signal.snr_samples is not None
    assert [one.sampled_at for one in result.signal.snr_samples] == [
        at(20),
        at(40),
        at(330),
    ]


def test_a_recordings_notes_travel_with_its_result() -> None:
    """A replay says it was a replay (D-125)."""
    result = derive(recording=recording(notes="replay of pass.cf32"), decode=report())

    assert result.client_notes == "replay of pass.cf32; detected by first decoded frame"


# --- SNR reduction ------------------------------------------------------------


def test_two_thousand_snr_samples_reduce_to_512_and_keep_the_full_series_peak() -> None:
    """D-122: the peak is taken before the reduction, which may drop it."""
    series = tuple(
        SnrPoint(index * 0.33, 1.0 + (index % 7) * 0.1) for index in range(2_000)
    )
    peak_at = 1_001
    series = (
        *series[:peak_at],
        SnrPoint(series[peak_at].offset_s, 25.0),
        *series[peak_at + 1 :],
    )

    result = derive(recording=recording(), decode=report(snr=series))

    assert result.signal is not None
    assert result.signal.snr_samples is not None
    assert len(result.signal.snr_samples) <= MAX_SNR_SAMPLES
    assert result.signal.peak_snr_db == 25.0


def test_reduction_takes_raw_samples_nearest_each_bucket_centre_in_order() -> None:
    # Ten points in five buckets 1.8 s wide, centred at 0.9, 2.7, 4.5, 6.3, 8.1;
    # 4 and 5 are equally near 4.5, and the tie goes to the earlier.
    series = [SnrPoint(float(offset), float(offset)) for offset in range(10)]

    reduced = reduce_snr(series, limit=5)

    assert reduced == tuple(
        SnrPoint(float(offset), float(offset)) for offset in (1, 3, 4, 6, 8)
    )


def test_a_short_series_is_left_alone() -> None:
    series = [SnrPoint(1.0, 2.0), SnrPoint(2.0, 3.0)]

    assert reduce_snr(series, limit=5) == tuple(series)


# --- coverage and policy ------------------------------------------------------


def test_coverage_counts_only_the_part_of_the_window_recorded() -> None:
    assert coverage_of(recording(), ASSIGNMENT) == 1.0
    half = recording(stopped_at=START_AT + timedelta(minutes=5, seconds=30))
    assert coverage_of(half, ASSIGNMENT) == pytest.approx(0.5)


@pytest.mark.parametrize(
    "changes",
    [
        {"snr_threshold_db": float("nan")},
        {"minimum_coverage": 0.0},
        {"minimum_coverage": 1.5},
    ],
)
def test_a_policy_the_table_could_not_apply_is_refused(
    changes: dict[str, float],
) -> None:
    with pytest.raises(ValueError):
        OutcomePolicy(**changes)
