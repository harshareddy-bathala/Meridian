"""``meridian_sim.faults`` — a schedule that is arithmetic, and a transport that breaks.

No marker: a schedule is a function of a tick number, and the transport is
checked against a stub that records what reached it. Both are testable without a
platform, which is what makes "a run with faults is as reproducible as a clean
one" something that can be asserted rather than hoped for.

Reference: docs/DECISIONS.md D-024, D-074.
"""

from __future__ import annotations

import httpx
import pytest

from meridian_sim.config import seed_for_station
from meridian_sim.faults import (
    NETWORK_DOWN,
    RECEIVER_DOWN,
    RESTART,
    SCENARIOS,
    TOKEN_REVOKED,
    UPLOAD_BLOCKED,
    FaultInjectingTransport,
    FaultState,
    schedule_for,
)

MASTER_SEED = 4471
STATION_SEED = seed_for_station(MASTER_SEED, 1)
TICKS = 600


class RecordingTransport(httpx.BaseTransport):
    """Stands in for the network, and writes down what got through."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        """Accept anything, and remember it."""
        self.requests.append(request)
        return httpx.Response(200, json={}, request=request)


def request(path: str = "/msp/v0/heartbeat") -> httpx.Request:
    """One authenticated request, as the client would build it."""
    return httpx.Request(
        "POST",
        f"http://platform.test{path}",
        headers={"Authorization": "Bearer real-token"},
    )


def ticks_where(scenario: str, kind: str, seed: int = STATION_SEED) -> list[int]:
    """Every tick in a long run on which ``kind`` is in force."""
    schedule = schedule_for(seed, scenario)
    return [tick for tick in range(TICKS) if kind in schedule.active_at(tick)]


def test_a_clean_scenario_breaks_nothing() -> None:
    """The default, and the baseline every fault run is compared against."""
    schedule = schedule_for(STATION_SEED, "clean")

    assert all(schedule.active_at(tick) == frozenset() for tick in range(TICKS))


def test_a_schedule_is_the_same_twice() -> None:
    """A run with faults is as reproducible as one without, or it is not evidence."""
    first = [schedule_for(STATION_SEED, "faulty").active_at(t) for t in range(TICKS)]
    second = [schedule_for(STATION_SEED, "faulty").active_at(t) for t in range(TICKS)]

    assert first == second


def test_a_schedule_can_be_read_out_of_order() -> None:
    """It advances nothing, so a run's whole history can be printed up front."""
    schedule = schedule_for(STATION_SEED, "faulty")
    forwards = [schedule.active_at(tick) for tick in range(TICKS)]
    backwards = [schedule.active_at(tick) for tick in reversed(range(TICKS))]

    assert forwards == list(reversed(backwards))


def test_two_stations_break_at_different_times() -> None:
    """A fleet that failed in lockstep would be one failure tested many times."""
    one = ticks_where("network", NETWORK_DOWN, seed_for_station(MASTER_SEED, 1))
    two = ticks_where("network", NETWORK_DOWN, seed_for_station(MASTER_SEED, 2))

    assert one != two


@pytest.mark.parametrize("scenario", ["network", "upload", "restart"])
def test_a_recurring_fault_happens_more_than_once(scenario: str) -> None:
    """A fault that fired once in a long run would be an incident, not a schedule."""
    (kind,) = SCENARIOS[scenario]

    assert len(ticks_where(scenario, kind)) > 1


@pytest.mark.parametrize("scenario", ["network", "upload"])
def test_a_station_spends_most_of_its_life_working(scenario: str) -> None:
    """A fleet broken more often than not measures the injector, not the platform."""
    (kind,) = SCENARIOS[scenario]

    assert len(ticks_where(scenario, kind)) < TICKS // 4


def test_a_scenario_injects_only_what_it_names() -> None:
    """Selecting one fault must not quietly bring the others."""
    schedule = schedule_for(STATION_SEED, "network")
    seen = {kind for tick in range(TICKS) for kind in schedule.active_at(tick)}

    assert seen <= {NETWORK_DOWN}


def test_a_revoked_token_never_comes_back() -> None:
    """D-024: the loop stops on a 401, so this is a terminal state by design."""
    schedule = schedule_for(STATION_SEED, "revoked")
    first = next(t for t in range(TICKS) if TOKEN_REVOKED in schedule.active_at(t))

    assert all(TOKEN_REVOKED in schedule.active_at(t) for t in range(first, TICKS))


def test_a_revoked_token_lets_the_station_work_first() -> None:
    """What is under test is a working station losing its credential."""
    schedule = schedule_for(STATION_SEED, "revoked")

    assert TOKEN_REVOKED not in schedule.active_at(0)


def test_the_general_scenario_leaves_the_fleet_alive() -> None:
    """`faulty` omits revocation on purpose.

    A revoked token stops a station permanently, so including it here would mean
    every station in a long run eventually stopping — a fleet that dies of old
    age tests nothing after the first hour.
    """
    schedule = schedule_for(STATION_SEED, "faulty")
    seen = {kind for tick in range(TICKS) for kind in schedule.active_at(tick)}

    assert TOKEN_REVOKED not in seen
    assert seen == {NETWORK_DOWN, UPLOAD_BLOCKED, RESTART, RECEIVER_DOWN}


def test_a_broken_receiver_is_not_a_broken_connection() -> None:
    """The one fault injected above the transport, because nothing is wrong below.

    A station with a dead radio reports, holds work and is reachable throughout.
    Injecting it at the HTTP layer would break the wrong thing and produce a
    silent station instead of one reporting `not_attempted`.
    """
    inner = RecordingTransport()
    transport = FaultInjectingTransport(
        inner, FaultState(active=frozenset({RECEIVER_DOWN}))
    )

    transport.handle_request(request())

    assert len(inner.requests) == 1


def test_a_misspelled_scenario_is_refused() -> None:
    """Defaulting to clean would make a typo look like a run that went well."""
    with pytest.raises(KeyError):
        schedule_for(STATION_SEED, "netwrok")


def test_restarts_are_reported_as_instants() -> None:
    """The supervisor reads a restart as an instruction, not a condition."""
    schedule = schedule_for(STATION_SEED, "restart")
    firing = [tick for tick in range(TICKS) if schedule.restarts_at(tick)]

    assert firing
    assert all(RESTART in schedule.active_at(tick) for tick in firing)


def test_nothing_is_touched_when_nothing_is_wrong() -> None:
    """The transport must be invisible on a healthy tick."""
    inner = RecordingTransport()
    transport = FaultInjectingTransport(inner, FaultState())

    transport.handle_request(request())

    assert len(inner.requests) == 1
    assert inner.requests[0].headers["Authorization"] == "Bearer real-token"


def test_a_network_outage_looks_like_an_unreachable_platform() -> None:
    """A real `httpx.ConnectError`, so the client's real retry policy runs.

    Faking it any higher — a stubbed `heartbeat()` — would test the loop against
    a rehearsal of an outage rather than one.
    """
    inner = RecordingTransport()
    transport = FaultInjectingTransport(
        inner, FaultState(active=frozenset({NETWORK_DOWN}))
    )

    with pytest.raises(httpx.ConnectError):
        transport.handle_request(request())

    assert inner.requests == []


def test_a_blocked_upload_leaves_the_heartbeat_working() -> None:
    """The nastiest of the four, and the reason it is here.

    A station in this state reports, holds work and says it is listening while
    its observations pile up on disk. Nothing a dashboard shows would say so.
    """
    inner = RecordingTransport()
    transport = FaultInjectingTransport(
        inner, FaultState(active=frozenset({UPLOAD_BLOCKED}))
    )

    transport.handle_request(request("/msp/v0/heartbeat"))
    with pytest.raises(httpx.ConnectError):
        transport.handle_request(request("/msp/v0/observations"))

    assert [one.url.path for one in inner.requests] == ["/msp/v0/heartbeat"]


def test_a_revoked_token_reaches_the_platform_as_a_bad_credential() -> None:
    """So the 401 the loop handles is a real one from the real endpoint.

    This exercises the station's response to revocation, not the platform's
    revocation path — an operator running `meridian station revoke` is what does
    that, and the simulator must not reach into the database to do it itself.
    """
    inner = RecordingTransport()
    transport = FaultInjectingTransport(
        inner, FaultState(active=frozenset({TOKEN_REVOKED}))
    )

    transport.handle_request(request())

    assert inner.requests[0].headers["Authorization"] != "Bearer real-token"


def test_the_state_can_change_under_a_live_transport() -> None:
    """The transport is built once and lives a whole station's life."""
    inner = RecordingTransport()
    state = FaultState()
    transport = FaultInjectingTransport(inner, state)

    transport.handle_request(request())
    state.active = frozenset({NETWORK_DOWN})
    with pytest.raises(httpx.ConnectError):
        transport.handle_request(request())
    state.active = frozenset()
    transport.handle_request(request())

    assert len(inner.requests) == 2
