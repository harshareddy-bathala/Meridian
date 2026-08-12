"""``meridian_client.observation_message`` — the §4.4 body, and its answer.

Pure, so no marker and no infrastructure. The expected bodies are written out
rather than computed, because a test that builds its expectation with the
function under test passes when both are wrong together.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from meridian_client.observation_message import (
    MAX_DOPPLER_SAMPLES,
    DopplerSample,
    MalformedAcknowledgementError,
    ObservationResult,
    Signal,
    build_observation_body,
    parse_observation_ack,
)

STATION_ID = "st_7fa3c1"
STARTED_AT = datetime(2026, 8, 14, 9, 41, 18, tzinfo=UTC)
ENDED_AT = datetime(2026, 8, 14, 9, 52, 10, tzinfo=UTC)
DETECTED_AT = datetime(2026, 8, 14, 9, 41, 53, tzinfo=UTC)


def decoded_result() -> ObservationResult:
    """MSP §4.4's worked example, as an executor would produce it."""
    return ObservationResult(
        assignment_id="as_44b2",
        started_at=STARTED_AT,
        ended_at=ENDED_AT,
        outcome="decoded",
        signal=Signal(
            detected=True,
            first_detection_at=DETECTED_AT,
            peak_snr_db=11.4,
            doppler_samples=(
                DopplerSample(DETECTED_AT, 3140),
                DopplerSample(datetime(2026, 8, 14, 9, 46, 44, tzinfo=UTC), 12),
            ),
        ),
        products=({"kind": "waterfall", "uri": "file:///w.png", "sha256": "aa"},),
        client_notes="rotator lagged 2s at AOS",
    )


def test_the_body_is_the_one_the_specification_shows() -> None:
    """MSP §4.4's example, field for field."""
    body = build_observation_body(decoded_result(), STATION_ID)

    assert body == {
        "assignment_id": "as_44b2",
        "station_id": "st_7fa3c1",
        "started_at": "2026-08-14T09:41:18Z",
        "ended_at": "2026-08-14T09:52:10Z",
        "outcome": "decoded",
        "signal": {
            "detected": True,
            "first_detection_at": "2026-08-14T09:41:53Z",
            "peak_snr_db": 11.4,
            "doppler_samples": [
                {"t": "2026-08-14T09:41:53Z", "offset_hz": 3140},
                {"t": "2026-08-14T09:46:44Z", "offset_hz": 12},
            ],
        },
        "products": [{"kind": "waterfall", "uri": "file:///w.png", "sha256": "aa"}],
        "client_notes": "rotator lagged 2s at AOS",
    }


def test_instants_carry_a_z_suffix_rather_than_an_offset() -> None:
    """The same instant, a different string — and a parser may accept only one."""
    body = build_observation_body(decoded_result(), STATION_ID)

    assert str(body["started_at"]).endswith("Z")
    assert "+00:00" not in str(body["started_at"])


def test_a_non_utc_instant_is_converted_or_refused_but_never_sent_raw() -> None:
    """A station in India reporting +05:30 must not have it read as UTC.

    Five and a half hours is most of an orbit, so the observation would land
    against the wrong pass entirely.
    """
    india = timezone(timedelta(hours=5, minutes=30))
    result = ObservationResult(
        assignment_id="as_44b2",
        started_at=STARTED_AT.astimezone(india),
        ended_at=ENDED_AT,
        outcome="no_signal",
        signal=Signal(detected=False),
    )

    with pytest.raises(ValueError, match="UTC"):
        build_observation_body(result, STATION_ID)


def test_a_naive_instant_is_refused() -> None:
    """CLAUDE.local.md §6: a naive datetime is a bug, not a tolerance."""
    result = ObservationResult(
        assignment_id="as_44b2",
        started_at=datetime(2026, 8, 14, 9, 41, 18),  # noqa: DTZ001
        ended_at=ENDED_AT,
        outcome="no_signal",
        signal=Signal(detected=False),
    )

    with pytest.raises(ValueError, match=r"UTC|naive|timezone"):
        build_observation_body(result, STATION_ID)


def test_a_report_with_nothing_heard_omits_what_it_did_not_measure() -> None:
    """`no_signal` is data. The body says so without inventing null fields."""
    result = ObservationResult(
        assignment_id="as_44b2",
        started_at=STARTED_AT,
        ended_at=ENDED_AT,
        outcome="no_signal",
        signal=Signal(detected=False),
    )

    body = build_observation_body(result, STATION_ID)

    assert body["signal"] == {"detected": False}
    assert "products" not in body
    assert "client_notes" not in body


def test_an_unknown_outcome_never_leaves_the_client() -> None:
    """A value the platform would reject must not reach the upload queue.

    Queued, it would be retried against an endpoint that refuses it every time —
    a station holding a payload it can never deliver.
    """
    result = ObservationResult(
        assignment_id="as_44b2",
        started_at=STARTED_AT,
        ended_at=ENDED_AT,
        outcome="partially_decoded",
    )

    with pytest.raises(ValueError, match="outcome must be one of"):
        build_observation_body(result, STATION_ID)


def test_a_window_that_runs_backwards_never_leaves_the_client() -> None:
    result = ObservationResult(
        assignment_id="as_44b2",
        started_at=ENDED_AT,
        ended_at=STARTED_AT,
        outcome="aborted",
    )

    with pytest.raises(ValueError, match="started_at is after ended_at"):
        build_observation_body(result, STATION_ID)


def test_more_than_the_doppler_cap_never_leaves_the_client() -> None:
    """MSP §6 caps the array at 512 (D-032)."""
    sample = DopplerSample(DETECTED_AT, 3140)
    result = ObservationResult(
        assignment_id="as_44b2",
        started_at=STARTED_AT,
        ended_at=ENDED_AT,
        outcome="decoded",
        signal=Signal(
            detected=True,
            first_detection_at=DETECTED_AT,
            doppler_samples=(sample,) * (MAX_DOPPLER_SAMPLES + 1),
        ),
    )

    with pytest.raises(ValueError, match="doppler samples"):
        build_observation_body(result, STATION_ID)


def test_a_non_finite_product_metric_never_leaves_the_client() -> None:
    """The platform refuses it (D-072), so it must never reach the queue.

    A decoder that divides by a zero frame count produces ``inf`` without
    anyone writing the word, and a queued body the platform will always refuse
    is a station retrying it against every tick for the rest of its life.
    """
    result = ObservationResult(
        assignment_id="as_44b2",
        started_at=STARTED_AT,
        ended_at=ENDED_AT,
        outcome="decoded",
        signal=Signal(detected=True, first_detection_at=DETECTED_AT),
        products=({"kind": "image", "frames_per_second": float("inf")},),
    )

    with pytest.raises(ValueError, match="strict JSON"):
        build_observation_body(result, STATION_ID)


def test_no_samples_and_an_empty_array_are_sent_differently() -> None:
    """A station with no frequency reference has not measured an empty series."""
    without = build_observation_body(
        ObservationResult(
            "as_44b2", STARTED_AT, ENDED_AT, "no_signal", signal=Signal(detected=False)
        ),
        STATION_ID,
    )
    empty = build_observation_body(
        ObservationResult(
            "as_44b2",
            STARTED_AT,
            ENDED_AT,
            "no_signal",
            signal=Signal(detected=False, doppler_samples=()),
        ),
        STATION_ID,
    )

    assert "doppler_samples" not in without["signal"]  # type: ignore[operator]
    assert empty["signal"] == {"detected": False, "doppler_samples": []}


def test_a_valid_acknowledgement_is_read() -> None:
    """MSP §4.4's three fields, and the id the specification publishes."""
    ack = parse_observation_ack(
        {
            "observation_id": "ob_05601bd09768",
            "assignment_id": "as_44b2",
            "superseded": False,
        },
        "as_44b2",
    )

    assert ack.observation_id == "ob_05601bd09768"
    assert ack.assignment_id == "as_44b2"
    assert not ack.superseded


@pytest.mark.parametrize(
    "observation_id",
    ["ob_05601BD09768", "ob_0560", "05601bd09768", "as_05601bd09768", "", None],
)
def test_an_id_that_is_not_msp_shaped_is_refused(observation_id: object) -> None:
    """`ob_` and twelve lowercase hex. Shape only — the id is never parsed."""
    with pytest.raises(MalformedAcknowledgementError, match="observation_id"):
        parse_observation_ack(
            {
                "observation_id": observation_id,
                "assignment_id": "as_44b2",
                "superseded": False,
            },
            "as_44b2",
        )


def test_an_acknowledgement_for_another_assignment_is_refused() -> None:
    """The failure this prevents is silent and permanent.

    Accepted, it would drop the wrong item from the upload queue: one
    observation lost, and another retried forever.
    """
    with pytest.raises(MalformedAcknowledgementError, match="names"):
        parse_observation_ack(
            {
                "observation_id": "ob_05601bd09768",
                "assignment_id": "as_something_else",
                "superseded": False,
            },
            "as_44b2",
        )


def test_a_non_boolean_superseded_is_refused() -> None:
    """MSP §4.4 says boolean, and "true" is a string a station could misread."""
    with pytest.raises(MalformedAcknowledgementError, match="superseded"):
        parse_observation_ack(
            {
                "observation_id": "ob_05601bd09768",
                "assignment_id": "as_44b2",
                "superseded": "true",
            },
            "as_44b2",
        )
