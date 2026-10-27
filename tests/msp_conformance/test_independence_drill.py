"""EVALUATION.md §12.1 as a test: the station receives with the platform unreachable.

*Break the station's route to the platform before a scheduled pass, and leave it
broken until after the pass ends. The pass is received with no gap attributable
to the outage, and after reconciliation the platform holds exactly one
observation for that assignment.* It **fails if the client blocks on the network
at any point during the pass.**

The route is **blackholed**, not refused. A closed port answers at once, and the
outage tests beside this one use it; a dropped tunnel or a dead route answers
nothing, and every request waits out its whole timeout. That is the case in
which a single-threaded loop can be held off its capture edges, so it is the case
this drill runs.

**Time is virtual.** A blackholed request advances a clock by the timeout the
transport gave it and then fails as httpx would, and the loop's clocks and
sleeps read that clock, so a fifteen-minute pass with an outage across it runs
in a moment and its lateness is measured exactly. Everything else is real: the
loop, the transport and its retry policy as ``station_wiring`` builds them, the
application, and the database, which the station reconciles with afterwards.

Marked ``msp_conformance`` by the directory hook in ``tests/conftest.py``.

Reference: docs/EVALUATION.md §12.1; docs/DECISIONS.md D-015, D-027, D-129,
D-285.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from meridian.api.app import create_app
from meridian.api.dependencies import get_connection
from meridian.store.invites import hash_invite_token
from meridian_client import station_loop, time_limit, transport
from meridian_client.assignment_message import Assignment
from meridian_client.credentials import StationCredentials
from meridian_client.execution import (
    CaptureWindow,
    ExecutionStatus,
    assignment_capture_window,
    assignment_status,
)
from meridian_client.held_assignments import AssignmentRecord
from meridian_client.observation_message import ObservationResult, Signal
from meridian_client.observation_queue import ObservationQueue
from meridian_client.registration import ReceiveChain, StationProfile, register
from meridian_client.station_loop import StationLoop, retry_policy_attempts_for
from meridian_client.transport import MspTransport, RetryPolicy

ASSIGNMENT_ID = "as_drill"
SATELLITE_ID = "norad:57166"
LINE1 = "1 25544U 98067A   26226.50000000  .00001234  00000-0  12345-4 0  9991"
LINE2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.50377579123456"

ON_TIME_S = 1.0
"""How late a capture edge may be met: the loop's own wake margin and a little.

The loop aims half a second past an edge (``EDGE_MARGIN_S``) so its tick lands
on the far side. Anything beyond a second is the network holding it.
"""

PROFILE = StationProfile(
    name="station-001",
    operator="meridian",
    lat_deg=12.9716,
    lon_deg=77.5946,
    alt_m=920.0,
    capabilities=(
        ReceiveChain(
            band="vhf",
            freq_min_hz=136_000_000,
            freq_max_hz=138_000_000,
            modes=("lrpt",),
            polarisation="rhcp",
            tracking=False,
            min_elevation_deg=10.0,
        ),
    ),
)


class DrillOverError(Exception):
    """Raised from the loop's sleep once the drill has seen enough."""


@dataclass
class VirtualClock:
    """The wall clock and the monotonic clock, advanced together."""

    wall: datetime
    monotonic: float = 0.0
    stop_after: datetime | None = None

    def advance(self, seconds: float) -> None:
        self.wall += timedelta(seconds=seconds)
        self.monotonic += seconds

    def sleep(self, seconds: float) -> None:
        if self.stop_after is not None and self.wall >= self.stop_after:
            raise DrillOverError
        self.advance(seconds)


class Blackhole(httpx.BaseTransport):
    """A route that swallows every request until its timeout runs out.

    ``connect``: the packets are dropped, as with a dead route or a stopped
    tunnel. ``read``: the connection is taken and never answered, as with a
    half-open path. Either way the request costs exactly the timeout the
    transport set on it, on the virtual clock.
    """

    def __init__(self, clock: VirtualClock, phase: str) -> None:
        self.clock = clock
        self.phase = phase
        self.requests = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        timeouts = request.extensions["timeout"]
        if self.phase == "connect":
            self.clock.advance(float(timeouts["connect"]))
            raise httpx.ConnectTimeout("blackholed", request=request)
        self.clock.advance(float(timeouts["read"]))
        raise httpx.ReadTimeout("blackholed", request=request)


@dataclass
class DrillExecutor:
    """Records when each capture edge was met, and finishes the pass it ran."""

    clock: VirtualClock
    begun: list[tuple[str, datetime]] = field(default_factory=list)
    ended: list[tuple[str, datetime]] = field(default_factory=list)
    ready: list[ObservationResult] = field(default_factory=list)

    def capture_window(self, assignment: Assignment) -> CaptureWindow:
        return assignment_capture_window(assignment)

    def status(self, running: Assignment | None) -> ExecutionStatus:
        return assignment_status(running)

    def begin(self, assignment: Assignment) -> None:
        self.begun.append((assignment.assignment_id, self.clock.wall))

    def end(self, assignment: Assignment) -> None:
        self.ended.append((assignment.assignment_id, self.clock.wall))
        began = dict(self.begun)[assignment.assignment_id]
        # The recording and decode are the executor's, and run whether or not
        # the platform can be reached; what matters here is the handover.
        self.ready.append(
            ObservationResult(
                assignment_id=assignment.assignment_id,
                started_at=began,
                ended_at=self.clock.wall,
                outcome="no_signal",
                signal=Signal(detected=False),
            )
        )

    def take_completed(self) -> tuple[ObservationResult, ...]:
        completed = tuple(self.ready)
        self.ready.clear()
        return completed


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes."""
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def started(
    database_url: str, rollback: Any, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Any]:
    """The started application, sharing this test's rolled-back connection."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("TOKEN_HASH_PEPPER", "independence-drill-pepper")
    app = create_app()
    app.dependency_overrides[get_connection] = lambda: rollback
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def admit_station(started: Any, rollback: Any, tmp_path: Path) -> StationCredentials:
    with rollback.cursor() as cur:
        cur.execute(
            "insert into invite_tokens (token_sha256, label) values (%s, %s)",
            (hash_invite_token("drill-invite"), "independence-drill"),
        )
    with MspTransport(
        "http://platform.test", http_transport=started._transport
    ) as anonymous:
        return register(
            anonymous,
            PROFILE,
            invite_token="drill-invite",
            registration_key_path=tmp_path / "registration_key",
        )


def schedule_a_pass(rollback: Any, station_id: str, start_at: datetime) -> datetime:
    """One assignment opening at ``start_at``; returns when it closes."""
    end_at = start_at + timedelta(minutes=11)
    with rollback.cursor() as cur:
        cur.execute(
            "insert into satellites (satellite_id, name) values (%s, %s)"
            " on conflict do nothing",
            (SATELLITE_ID, "Meteor-M 2-3"),
        )
        cur.execute(
            "insert into element_sets (satellite_id, epoch, line1, line2, source)"
            " values (%s, %s, %s, %s, %s) returning id",
            (SATELLITE_ID, start_at - timedelta(hours=7), LINE1, LINE2, "manual"),
        )
        (element_set_id,) = cur.fetchone()
        cur.execute(
            "insert into passes (satellite_id, station_id, aos, los,"
            " max_elevation_deg, max_elevation_at, aos_azimuth_deg, los_azimuth_deg,"
            " element_set_id, min_elevation_deg)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id",
            (
                SATELLITE_ID,
                station_id,
                start_at,
                end_at,
                61.4,
                start_at + timedelta(minutes=5),
                10.0,
                200.0,
                element_set_id,
                10.0,
            ),
        )
        (pass_id,) = cur.fetchone()
        cur.execute(
            "insert into assignments (assignment_id, pass_id, station_id, start_at,"
            " end_at, centre_freq_hz, mode, timing_uncertainty_s, reason)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                ASSIGNMENT_ID,
                pass_id,
                station_id,
                start_at,
                end_at,
                137_900_000,
                "lrpt",
                4.2,
                "independence drill",
            ),
        )
    return end_at


def revisions(rollback: Any) -> list[tuple[int, str]]:
    with rollback.cursor() as cur:
        cur.execute(
            "select revision, observation_id from observations"
            " where assignment_id = %s order by revision",
            (ASSIGNMENT_ID,),
        )
        return [(int(revision), str(oid)) for revision, oid in cur.fetchall()]


def station_transport(
    credentials: StationCredentials, http_transport: httpx.BaseTransport
) -> MspTransport:
    """The transport as ``station_wiring.build_transport`` builds it."""
    interval_s = float(credentials.heartbeat_interval_s)
    return MspTransport(
        "http://platform.test",
        bearer_token=credentials.bearer_token,
        retry=RetryPolicy(attempts=retry_policy_attempts_for(interval_s)),
        http_transport=http_transport,
    )


@dataclass
class Drill:
    """One station, one pass, and the clock the outage runs on."""

    started: Any
    rollback: Any
    credentials: StationCredentials
    record: AssignmentRecord
    queue: ObservationQueue
    clock: VirtualClock
    executor: DrillExecutor
    opens_at: datetime
    closes_at: datetime

    def loop(
        self, http_transport: httpx.BaseTransport
    ) -> tuple[MspTransport, StationLoop]:
        station = station_transport(self.credentials, http_transport)
        loop = StationLoop(
            station, self.credentials, self.record, self.executor, self.queue
        )
        return station, loop


def take_delivery(started: Any, rollback: Any, tmp_path: Path) -> Drill:
    """Before the outage: the station registers, and takes delivery of its pass."""
    credentials = admit_station(started, rollback, tmp_path)
    opens_at = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=3)
    closes_at = schedule_a_pass(rollback, credentials.station_id, opens_at)
    clock = VirtualClock(wall=datetime.now(UTC))
    drill = Drill(
        started,
        rollback,
        credentials,
        AssignmentRecord(tmp_path / "held.json"),
        ObservationQueue(tmp_path / "outbox"),
        clock,
        DrillExecutor(clock),
        opens_at,
        closes_at,
    )
    station, loop = drill.loop(started._transport)
    with station:
        assert loop.tick(clock.wall).accepted == (ASSIGNMENT_ID,)
    return drill


def run_through_outage(
    drill: Drill, monkeypatch: pytest.MonkeyPatch, phase: str, offset_s: int
) -> Blackhole:
    """Break the route a minute or so before the pass; run the loop past its end."""
    clock = drill.clock
    monkeypatch.setattr(station_loop, "_wall_clock", lambda: clock.wall)
    monkeypatch.setattr(station_loop, "_monotonic", lambda: clock.monotonic)
    monkeypatch.setattr(station_loop, "_sleep", clock.sleep)
    monkeypatch.setattr(transport, "_sleep", clock.advance)
    monkeypatch.setattr(time_limit, "_monotonic", lambda: clock.monotonic)
    # The longest backoff the policy can draw: the worst case, every time.
    monkeypatch.setattr(transport.random, "uniform", lambda _low, high: high)
    clock.advance((drill.opens_at - clock.wall).total_seconds() - 60 - offset_s)
    clock.stop_after = drill.closes_at + timedelta(minutes=2)

    blackhole = Blackhole(clock, phase)
    began = clock.wall
    station, loop = drill.loop(blackhole)
    with station, pytest.raises(DrillOverError):
        loop.run()

    # The outage was real: the station kept trying, every interval, and nothing
    # came back. (A limit that gave up before sending would pass the timing
    # checks for the wrong reason.)
    outage_s = (clock.wall - began).total_seconds()
    assert blackhole.requests >= outage_s // drill.credentials.heartbeat_interval_s
    return blackhole


ALIGNMENTS = [
    (phase, offset_s)
    for phase in ("connect", "read")
    for offset_s in (0, 7, 13, 22, 29)
]
"""``offset_s`` moves the window across one heartbeat interval, so somewhere in
the sweep each edge falls while a request is waiting out its timeout."""


@pytest.mark.parametrize(
    "alignment", ALIGNMENTS, ids=[f"{phase}-{offset}s" for phase, offset in ALIGNMENTS]
)
def test_a_pass_is_received_on_time_through_an_outage_and_stored_once(
    started: Any,
    rollback: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    alignment: tuple[str, int],
) -> None:
    """Every alignment of the pass's edges against a heartbeat stuck in the hole."""
    phase, offset_s = alignment
    drill = take_delivery(started, rollback, tmp_path)
    run_through_outage(drill, monkeypatch, phase, offset_s)

    # The pass was captured on its own schedule, with no gap at either edge.
    ((began_id, began_at),) = drill.executor.begun
    ((ended_id, ended_at),) = drill.executor.ended
    assert began_id == ended_id == ASSIGNMENT_ID
    on_time = timedelta(seconds=ON_TIME_S)
    assert timedelta(0) <= began_at - drill.opens_at <= on_time
    assert timedelta(0) <= ended_at - drill.closes_at <= on_time
    # And its result was queued, not lost, while nothing reached the platform.
    (queued,) = drill.queue.pending()
    assert queued.body["assignment_id"] == ASSIGNMENT_ID
    assert revisions(rollback) == []

    # The route returns. The station resends what was never acknowledged.
    station, loop = drill.loop(started._transport)
    with station:
        after = loop.tick(drill.clock.wall)
    assert after.heartbeat_sent
    assert after.submitted == (ASSIGNMENT_ID,)
    ((revision, observation_id),) = revisions(rollback)
    assert revision == 1

    # A replay of the same report is a no-op: the identifier is derived and
    # revisions are append-only (D-027, D-015).
    replayed = started.post(
        "/msp/v0/observations",
        json=queued.body,
        headers={
            "MSP-Version": "0.3",
            "Authorization": f"Bearer {drill.credentials.bearer_token}",
        },
    )
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()["observation_id"] == observation_id
    assert revisions(rollback) == [(1, observation_id)]
