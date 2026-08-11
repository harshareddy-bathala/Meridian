"""The station loop, run without a network and without waiting.

The transport is replaced with one that returns scripted responses and records
what was sent; ``station_loop._sleep`` with one that records what it was asked to
wait. The record and the executor are the real ones, so what is under test is the
loop's own sequencing rather than a rehearsal of it.

Marked as a unit test by living in ``tests/unit``: a temporary directory is not
infrastructure.

Reference: docs/MSP-SPEC.md §4.2; docs/DECISIONS.md D-003, D-024, D-069.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from meridian_client import station_loop as loop_module
from meridian_client.assignment_message import Assignment
from meridian_client.credentials import StationCredentials
from meridian_client.execution import NullExecutor
from meridian_client.held_assignments import AssignmentRecord
from meridian_client.station_loop import (
    StationLoop,
    retry_policy_attempts_for,
)
from meridian_client.transport import READ_TIMEOUT_S, ProtocolError

NOW = datetime(2026, 8, 14, 9, 31, 2, tzinfo=UTC)

CREDENTIALS = StationCredentials(
    station_id="st_7fa3c1",
    bearer_token="a-token",
    registration_key="a-key",
    heartbeat_interval_s=30,
)

LINE1 = "1 25544U 98067A   26226.50000000  .00001234  00000-0  12345-4 0  9991"
LINE2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.50377579123456"


def assignment_message(
    assignment_id: str, *, starts_in_minutes: float, minutes_long: float = 11
) -> dict[str, Any]:
    """One §4.3 assignment as the platform would send it."""
    start_at = NOW + timedelta(minutes=starts_in_minutes)
    return {
        "assignment_id": assignment_id,
        "satellite_id": "norad:57166",
        "start_at": start_at.isoformat(),
        "end_at": (start_at + timedelta(minutes=minutes_long)).isoformat(),
        "centre_freq_hz": 137900000,
        "mode": "lrpt",
        "expected_max_elevation_deg": 61.4,
        "predicted_yield": None,
        "element_set": {
            "epoch": (NOW - timedelta(hours=7)).isoformat(),
            "line1": LINE1,
            "line2": LINE2,
        },
        "timing_uncertainty_s": 4.2,
        "priority": 1.0,
    }


class ScriptedTransport:
    """Returns queued responses, records bodies, and can be told to fail."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.sent: list[dict[str, Any]] = []

    def heartbeat(self, body: dict[str, Any]) -> dict[str, Any]:
        """Record the body, then return or raise whatever was queued next."""
        self.sent.append(body)
        nxt = self._responses.pop(0) if self._responses else {"assignments": []}
        if isinstance(nxt, Exception):
            raise nxt
        response = dict(nxt)
        response.setdefault("server_time", NOW.isoformat())
        return response


class RecordingExecutor:
    """A :class:`PassExecutor` that writes down what it was asked to do."""

    def __init__(self) -> None:
        self.begun: list[str] = []
        self.ended: list[str] = []

    def begin(self, assignment: Assignment) -> None:
        """Note a start."""
        self.begun.append(assignment.assignment_id)

    def end(self, assignment: Assignment) -> None:
        """Note a stop."""
        self.ended.append(assignment.assignment_id)


def build_loop(
    tmp_path: Path, responses: list[Any]
) -> tuple[StationLoop, ScriptedTransport, RecordingExecutor]:
    """A loop over a real on-disk record and a scripted platform."""
    transport = ScriptedTransport(responses)
    executor = RecordingExecutor()
    record = AssignmentRecord(tmp_path / "held.json")
    return (
        StationLoop(transport, CREDENTIALS, record, executor),  # type: ignore[arg-type]
        transport,
        executor,
    )


class FakeClock:
    """A monotonic clock that only advances when the loop sleeps.

    Stubbing the sleep alone is not enough: the loop schedules against elapsed
    time, so a sleep that does not advance the clock makes every computed delay
    look longer than the last. Advancing here is what a real `time.sleep` does,
    and it is the only way the cadence assertions below mean anything.
    """

    def __init__(self) -> None:
        self.elapsed_s = 0.0
        self.waits: list[float] = []

    def monotonic(self) -> float:
        """Seconds since this clock started."""
        return self.elapsed_s

    def sleep(self, seconds: float) -> None:
        """Record the wait and move the clock forward by it."""
        self.waits.append(seconds)
        self.elapsed_s += seconds

    def jump_forward(self, seconds: float) -> None:
        """Advance without sleeping — a tick that took longer than it should."""
        self.elapsed_s += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """Replace the loop's clock and sleep with a fake pair."""
    fake = FakeClock()
    monkeypatch.setattr(loop_module, "_monotonic", fake.monotonic)
    monkeypatch.setattr(loop_module, "_sleep", fake.sleep)
    return fake


def test_a_first_tick_reports_holding_nothing(tmp_path: Path) -> None:
    """`[]` and not an omitted field — the only way to say "I hold nothing"."""
    loop, transport, _ = build_loop(tmp_path, [{"assignments": []}])

    loop.tick(NOW)

    assert transport.sent[0]["held_assignments"] == []
    assert transport.sent[0]["state"] == "idle"


def test_a_delivered_assignment_is_named_on_the_next_heartbeat(
    tmp_path: Path,
) -> None:
    """Delivery, then confirmation. The platform moves it to `held` on the second."""
    loop, transport, _ = build_loop(
        tmp_path,
        [
            {"assignments": [assignment_message("as_a", starts_in_minutes=20)]},
            {"assignments": []},
        ],
    )

    loop.tick(NOW)
    loop.tick(NOW + timedelta(seconds=30))

    assert transport.sent[0]["held_assignments"] == []
    assert transport.sent[1]["held_assignments"] == ["as_a"]


def test_an_open_window_starts_execution_and_is_reported_the_same_tick(
    tmp_path: Path,
) -> None:
    """Execution is decided before the body is built.

    Reporting `listening` on the following tick instead would give away thirty
    seconds of an eight-minute pass — the part carrying the rise.
    """
    loop, transport, executor = build_loop(
        tmp_path,
        [
            {"assignments": [assignment_message("as_a", starts_in_minutes=1)]},
            {"assignments": []},
        ],
    )

    loop.tick(NOW)
    loop.tick(NOW + timedelta(minutes=2))

    assert executor.begun == ["as_a"]
    assert transport.sent[1]["state"] == "listening"
    assert transport.sent[1]["listening"]["assignment_id"] == "as_a"
    assert transport.sent[1]["listening"]["mode"] == "lrpt"


def test_a_closed_window_ends_execution_and_stops_being_held(
    tmp_path: Path,
) -> None:
    """A pass never repeats, so the moment its window closes there is nothing left.

    Continuing to name it would also stop the platform expiring it (D-067).
    """
    loop, transport, executor = build_loop(
        tmp_path,
        [
            {"assignments": [assignment_message("as_a", starts_in_minutes=1)]},
            {"assignments": []},
            {"assignments": []},
        ],
    )

    loop.tick(NOW)
    loop.tick(NOW + timedelta(minutes=2))
    loop.tick(NOW + timedelta(minutes=30))

    assert executor.ended == ["as_a"]
    assert loop.executing() is None
    assert transport.sent[2]["held_assignments"] == []
    assert transport.sent[2]["state"] == "idle"


def test_an_unreachable_platform_does_not_cancel_work_in_progress(
    tmp_path: Path,
) -> None:
    """The property the loop exists for.

    Work is started from the on-disk record, never from a response, so an outage
    mid-pass changes nothing the station does.
    """
    loop, _, executor = build_loop(
        tmp_path,
        [
            {"assignments": [assignment_message("as_a", starts_in_minutes=1)]},
            httpx.ConnectError("platform unreachable"),
            httpx.ConnectError("platform unreachable"),
        ],
    )

    loop.tick(NOW)
    loop.tick(NOW + timedelta(minutes=2))
    outcome = loop.tick(NOW + timedelta(minutes=4))

    assert outcome.heartbeat_sent is False
    assert outcome.stop_reason is None
    assert executor.begun == ["as_a"]
    assert executor.ended == []
    assert loop.executing() is not None


def test_a_station_restarted_mid_pass_resumes_from_its_own_record(
    tmp_path: Path,
) -> None:
    """Power loss, then a new process against the same file.

    The second loop was never told about this assignment by any response — it
    reads it off disk, reports holding it, and starts receiving.
    """
    first, _, _ = build_loop(
        tmp_path, [{"assignments": [assignment_message("as_a", starts_in_minutes=1)]}]
    )
    first.tick(NOW)

    second, transport, executor = build_loop(tmp_path, [{"assignments": []}])
    second.tick(NOW + timedelta(minutes=2))

    assert executor.begun == ["as_a"]
    assert transport.sent[0]["held_assignments"] == ["as_a"]


def test_redelivery_does_not_restart_a_pass_already_running(
    tmp_path: Path,
) -> None:
    """§4.2 redelivers on every heartbeat; a client must not treat that as new."""
    message = assignment_message("as_a", starts_in_minutes=1)
    loop, _, executor = build_loop(
        tmp_path,
        [
            {"assignments": [message]},
            {"assignments": [message]},
            {"assignments": [message]},
        ],
    )

    loop.tick(NOW)
    loop.tick(NOW + timedelta(minutes=2))
    loop.tick(NOW + timedelta(minutes=3))

    assert executor.begun == ["as_a"]


def test_a_revoked_token_stops_the_loop_rather_than_retrying(
    tmp_path: Path, clock: FakeClock
) -> None:
    """D-024: stop, log, surface to the operator. Never loop, never re-register.

    Fifty stations retrying a revoked token every thirty seconds against a
    publicly reachable endpoint is a denial of service the network inflicts on
    itself.
    """
    loop, transport, _ = build_loop(
        tmp_path, [ProtocolError(401, "unauthorized", "Bearer token is unknown.")]
    )

    reason = loop.run(stop_after_ticks=5)

    assert reason == "unauthorized"
    assert len(transport.sent) == 1
    assert clock.waits == []


def test_a_refusal_that_is_not_a_401_does_not_stop_the_loop(
    tmp_path: Path, clock: FakeClock
) -> None:
    """A malformed body is this tick's problem; the station keeps reporting."""
    loop, transport, _ = build_loop(
        tmp_path,
        [
            ProtocolError(400, "malformed", "health exceeds 4096 bytes."),
            {"assignments": []},
        ],
    )

    reason = loop.run(stop_after_ticks=2)

    assert reason is None
    assert len(transport.sent) == 2
    # And it waited between them rather than retrying immediately: a refusal the
    # platform is sure about does not get better by being asked again sooner.
    assert len(clock.waits) == 2


def test_the_loop_waits_the_platforms_interval_between_ticks(
    tmp_path: Path, clock: FakeClock
) -> None:
    """The cadence comes from registration, which is why it is persisted."""
    loop, _, _ = build_loop(tmp_path, [{"assignments": []}] * 3)

    loop.run(stop_after_ticks=3)

    assert clock.waits == [30.0, 30.0, 30.0]


def test_an_instant_tick_does_not_make_the_cadence_drift(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Ticks land on a fixed grid rather than interval-plus-work each time.

    Scheduling from "now + interval" after every tick adds the work's duration to
    every cycle, so a station drifts a little further from its slot all day. This
    schedules from when the tick was *due*.
    """
    loop, _, _ = build_loop(tmp_path, [{"assignments": []}] * 3)

    loop.run(stop_after_ticks=3)

    assert clock.elapsed_s == pytest.approx(90.0)


def test_an_overrun_tick_is_skipped_rather_than_queued(
    tmp_path: Path, clock: FakeClock
) -> None:
    """A station that catches up sends a burst, and fifty send it together.

    Five minutes pass during the first tick — a long network stall. The loop must
    wait a full interval afterwards rather than firing the ten missed ticks back
    to back with no delay at all, which is what scheduling from the original grid
    would do.
    """
    stalling = ScriptedTransport([{"assignments": []}] * 2)
    original_heartbeat = stalling.heartbeat

    def stall(body: dict[str, Any]) -> dict[str, Any]:
        clock.jump_forward(300.0)
        return original_heartbeat(body)

    stalling.heartbeat = stall  # type: ignore[method-assign]
    loop = StationLoop(
        stalling,  # type: ignore[arg-type]
        CREDENTIALS,
        AssignmentRecord(tmp_path / "held.json"),
        NullExecutor(),
    )

    loop.run(stop_after_ticks=2)

    assert clock.waits[0] == pytest.approx(CREDENTIALS.heartbeat_interval_s)


def test_the_heartbeat_retry_budget_fits_inside_the_interval() -> None:
    """A heartbeat still in flight when the next is due has already failed.

    The transport's default of four attempts is sized for a one-off request; four
    read timeouts plus backoff exceed a thirty-second interval.
    """
    attempts = retry_policy_attempts_for(30.0)

    assert attempts * READ_TIMEOUT_S <= 30.0
    assert attempts >= 1


def test_a_very_short_interval_still_gets_one_attempt() -> None:
    """Zero attempts would be a station that never heartbeats at all."""
    assert retry_policy_attempts_for(1.0) == 1


def test_the_null_executor_satisfies_the_protocol(tmp_path: Path) -> None:
    """A station with no radio still holds, reports and transitions.

    Which is the point of shipping the seam now: `in_progress` is reachable, and
    `was_listening()` gets evidence, before there is a decoder.
    """
    transport = ScriptedTransport(
        [{"assignments": [assignment_message("as_a", starts_in_minutes=1)]}, {}]
    )
    loop = StationLoop(
        transport,  # type: ignore[arg-type]
        CREDENTIALS,
        AssignmentRecord(tmp_path / "held.json"),
        NullExecutor(),
    )

    loop.tick(NOW)
    loop.tick(NOW + timedelta(minutes=2))

    assert transport.sent[1]["state"] == "listening"
