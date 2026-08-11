"""The heartbeat this client sends, and the response it reads back.

Both directions are pure functions, so every case here is written out by hand and
compared against MSP §4.2. Whether the platform accepts the body is asserted
against the real model in
``tests/msp_conformance/test_client_heartbeat.py``.

Marked as a unit test by living in ``tests/unit``: no network, no clock.

Reference: docs/MSP-SPEC.md §4.2, §4.3; docs/DECISIONS.md D-003, D-016, D-025.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from meridian_client.assignment_message import MalformedAssignmentError
from meridian_client.clock import ClockEstimate
from meridian_client.heartbeat import (
    Listening,
    StationState,
    build_heartbeat_body,
    parse_heartbeat_response,
)

SENT_AT = datetime(2026, 8, 14, 9, 31, 2, tzinfo=UTC)

LISTENING = Listening(
    assignment_id="as_44b2",
    satellite_id="norad:57166",
    centre_freq_hz=137_900_000,
    mode="lrpt",
)

LINE1 = "1 25544U 98067A   26226.50000000  .00001234  00000-0  12345-4 0  9991"
LINE2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.50377579123456"

ASSIGNMENT_MESSAGE = {
    "assignment_id": "as_44b2",
    "satellite_id": "norad:57166",
    "start_at": "2026-08-14T09:41:20.000Z",
    "end_at": "2026-08-14T09:52:07.000Z",
    "centre_freq_hz": 137900000,
    "mode": "lrpt",
    "expected_max_elevation_deg": 61.4,
    "predicted_yield": None,
    "element_set": {
        "epoch": "2026-08-14T02:11:00.000Z",
        "line1": LINE1,
        "line2": LINE2,
    },
    "timing_uncertainty_s": 4.2,
    "priority": 1.0,
}


def idle_station() -> StationState:
    """A station holding nothing and receiving nothing."""
    return StationState(station_id="st_7fa3c1", sent_at=SENT_AT, state="idle")


def test_an_empty_held_list_is_sent_rather_than_omitted() -> None:
    """§4.2: `[]` is a statement, and there is no other way to decline (D-003).

    A missing field would be indistinguishable from declining everything —
    exactly the case it has to be distinguishable from.
    """
    body = build_heartbeat_body(idle_station(), [])

    assert body["held_assignments"] == []


def test_the_held_list_is_whatever_the_record_says() -> None:
    """The ids come from the station's own record, not from the last response."""
    body = build_heartbeat_body(idle_station(), ["as_a", "as_b"])

    assert body["held_assignments"] == ["as_a", "as_b"]


def test_sent_at_is_rendered_in_msp_s_z_form() -> None:
    """§8 shows one form. `+00:00` is the same instant and a different string,
    and a microcontroller comparing suffixes sees the difference."""
    body = build_heartbeat_body(idle_station(), [])

    assert body["sent_at"] == "2026-08-14T09:31:02Z"


def test_a_naive_sent_at_is_refused_rather_than_sent() -> None:
    """A naive timestamp is wrong by whatever the station's local offset is, and
    it would land in the column the timing analysis reads."""
    naive = StationState(
        station_id="st_7fa3c1",
        sent_at=datetime(2026, 8, 14, 9, 31, 2),  # noqa: DTZ001
        state="idle",
    )

    with pytest.raises(ValueError, match="naive"):
        build_heartbeat_body(naive, [])


def test_a_state_msp_does_not_define_never_leaves_the_client() -> None:
    """Better a local error than a `400 malformed` the operator has to diagnose."""
    invalid = StationState(station_id="st_7fa3c1", sent_at=SENT_AT, state="receiving")

    with pytest.raises(ValueError, match="state must be one of"):
        build_heartbeat_body(invalid, [])


def test_an_idle_station_sends_no_listening_block() -> None:
    """The block asserts the station was tuned to something. Idle asserts nothing."""
    assert "listening" not in build_heartbeat_body(idle_station(), [])


def test_a_receiving_station_sends_all_four_listening_fields() -> None:
    """All-or-nothing (§4.2). A partial block cannot support the assertion the
    block exists to make, and `mode` is in it because a station running the wrong
    demodulator did not observe the pass (D-028)."""
    receiving = StationState(
        station_id="st_7fa3c1", sent_at=SENT_AT, state="listening", listening=LISTENING
    )

    body = build_heartbeat_body(receiving, ["as_44b2"])

    assert body["listening"] == {
        "assignment_id": "as_44b2",
        "satellite_id": "norad:57166",
        "centre_freq_hz": 137_900_000,
        "mode": "lrpt",
    }


def test_an_unmeasured_clock_sends_no_offset_at_all() -> None:
    """Not `null`, and above all not `0.0`.

    A station claiming a perfect clock and one that cannot measure its own are
    opposite cases, and EVALUATION.md §6.1 discards timing errors smaller than
    the reported uncertainty — so a zero would discard nothing and promote noise
    into the timing results (D-016, D-025).
    """
    body = build_heartbeat_body(idle_station(), [])

    assert "clock_offset_s" not in body
    assert "clock_uncertainty_s" not in body


def test_a_measured_clock_reports_both_numbers() -> None:
    """§6.1 needs both, and a station with a fast clock reports a negative offset."""
    measured = StationState(
        station_id="st_7fa3c1",
        sent_at=SENT_AT,
        state="idle",
        clock=ClockEstimate(offset_s=-5.0, uncertainty_s=0.05, round_trip_s=0.1),
    )

    body = build_heartbeat_body(measured, [])

    assert body["clock_offset_s"] == -5.0
    assert body["clock_uncertainty_s"] == 0.05


def test_health_defaults_to_an_empty_object() -> None:
    """Opaque to the platform, and an object either way — never absent."""
    assert build_heartbeat_body(idle_station(), [])["health"] == {}


def test_a_response_with_no_assignments_is_read_as_holding_nothing() -> None:
    """The common case: the station is up to date and there is nothing new."""
    response = parse_heartbeat_response(
        {"assignments": [], "server_time": "2026-08-14T09:31:02.123Z"}
    )

    assert response.assignments == ()
    assert response.server_time == SENT_AT + timedelta(milliseconds=123)


def test_a_delivered_assignment_is_read_with_its_element_set() -> None:
    """§4.3 carries the elements inline so a station never fetches orbital data."""
    response = parse_heartbeat_response(
        {"assignments": [ASSIGNMENT_MESSAGE], "server_time": "2026-08-14T09:31:02.000Z"}
    )

    one = response.assignments[0]
    assert one.assignment_id == "as_44b2"
    assert one.centre_freq_hz == 137_900_000
    assert one.timing_uncertainty_s == 4.2
    assert one.element_set.epoch == datetime(2026, 8, 14, 2, 11, 0, tzinfo=UTC)
    assert one.element_set.line1.startswith("1 25544U")


def test_an_assignment_missing_a_field_is_refused_by_name() -> None:
    """Nothing is defaulted. A missing `timing_uncertainty_s` read as zero would
    have the station record a pass on exactly its predicted boundaries and lose
    the opening of it."""
    incomplete = {
        k: v for k, v in ASSIGNMENT_MESSAGE.items() if k != "timing_uncertainty_s"
    }

    with pytest.raises(MalformedAssignmentError, match="timing_uncertainty_s"):
        parse_heartbeat_response(
            {"assignments": [incomplete], "server_time": "2026-08-14T09:31:02.000Z"}
        )


def test_a_null_predicted_yield_is_allowed() -> None:
    """The one nullable field: §4.3 calls it advisory and Phase 1 has no model."""
    response = parse_heartbeat_response(
        {"assignments": [ASSIGNMENT_MESSAGE], "server_time": "2026-08-14T09:31:02.000Z"}
    )

    assert response.assignments[0].predicted_yield is None
