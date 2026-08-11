"""The reference client heartbeating against the real platform, over real MSP.

The other half of the seam ``test_client_registration.py`` opens. Registration
proved the client can join; this proves the two agree about §4.2 and §4.3 in both
directions — the body the station builds is one the platform accepts, and the
assignment the platform emits is one the station can read back field for field.

That agreement is not implied by either package's own tests. ``meridian`` and
``meridian-client`` never import each other (D-012), so a renamed field is
correct on both sides in isolation and broken between them.

Marked ``msp_conformance`` by the directory hook in ``tests/conftest.py``.

Reference: docs/MSP-SPEC.md §4.2, §4.3; docs/DECISIONS.md D-003, D-012, D-067.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from meridian.api.app import create_app
from meridian.api.dependencies import get_connection
from meridian.api.models.heartbeat import HeartbeatRequestBody
from meridian.store.invites import hash_invite_token
from meridian_client.credentials import StationCredentials
from meridian_client.heartbeat import (
    Listening,
    StationState,
    build_heartbeat_body,
    parse_heartbeat_response,
)
from meridian_client.registration import ReceiveChain, StationProfile, register
from meridian_client.transport import MspTransport, ProtocolError

SATELLITE_ID = "norad:57166"
CENTRE_FREQ_HZ = 137_900_000
LINE1 = "1 25544U 98067A   26226.50000000  .00001234  00000-0  12345-4 0  9991"
LINE2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.50377579123456"

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


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes."""
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def app_and_rollback(
    database_url: str, rollback: Any, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Any]:
    """The started application, sharing this test's rolled-back connection."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("TOKEN_HASH_PEPPER", "client-heartbeat-pepper")
    app = create_app()
    app.dependency_overrides[get_connection] = lambda: rollback
    with TestClient(app) as started:
        yield started
    app.dependency_overrides.clear()


def registered_station(
    started: Any, rollback: Any, tmp_path: Any
) -> tuple[MspTransport, StationCredentials]:
    """Admit one station and return a transport carrying its bearer token.

    Two transports, because registration is unauthenticated and everything after
    it is not — which is the point: the second one builds its own
    ``Authorization`` header from the token the first one obtained, so the header
    MSP §3 specifies is the one under test.
    """
    with rollback.cursor() as cur:
        cur.execute(
            "insert into invite_tokens (token_sha256, label) values (%s, %s)",
            (hash_invite_token("heartbeat-invite"), "client-heartbeat"),
        )
    with MspTransport(
        "http://platform.test", http_transport=started._transport
    ) as anonymous:
        credentials = register(
            anonymous,
            PROFILE,
            invite_token="heartbeat-invite",
            registration_key_path=tmp_path / "registration_key",
        )
    return (
        MspTransport(
            "http://platform.test",
            bearer_token=credentials.bearer_token,
            http_transport=started._transport,
        ),
        credentials,
    )


def issue_assignment(rollback: Any, station_id: str, assignment_id: str) -> None:
    """One assignment for ``station_id`` whose window is open right now."""
    now = datetime.now(UTC)
    with rollback.cursor() as cur:
        cur.execute(
            "insert into satellites (satellite_id, name) values (%s, %s)"
            " on conflict do nothing",
            (SATELLITE_ID, "Meteor-M 2-3"),
        )
        cur.execute(
            "insert into element_sets (satellite_id, epoch, line1, line2, source)"
            " values (%s, %s, %s, %s, %s) returning id",
            (SATELLITE_ID, now - timedelta(hours=7), LINE1, LINE2, "manual"),
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
                now - timedelta(minutes=3),
                now + timedelta(minutes=7),
                61.4,
                now + timedelta(minutes=2),
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
                assignment_id,
                pass_id,
                station_id,
                now - timedelta(minutes=3),
                now + timedelta(minutes=7),
                CENTRE_FREQ_HZ,
                "lrpt",
                4.2,
                "conformance fixture",
            ),
        )


def state_of(rollback: Any, assignment_id: str) -> str:
    """The stored state of one assignment."""
    with rollback.cursor() as cur:
        cur.execute(
            "select state from assignments where assignment_id = %s", (assignment_id,)
        )
        row = cur.fetchone()
    assert row is not None
    return str(row[0])


def test_the_heartbeat_this_client_builds_is_one_the_platform_accepts() -> None:
    """The seam, with no HTTP in the way.

    A renamed field fails here rather than as a `400 malformed` in a station log.
    """
    station = StationState(
        station_id="st_7fa3c1",
        sent_at=datetime(2026, 8, 14, 9, 31, 2, tzinfo=UTC),
        state="listening",
        listening=Listening(
            assignment_id="as_44b2",
            satellite_id=SATELLITE_ID,
            centre_freq_hz=CENTRE_FREQ_HZ,
            mode="lrpt",
        ),
    )

    parsed = HeartbeatRequestBody.model_validate(
        build_heartbeat_body(station, ["as_44b2"])
    )

    assert parsed.held_assignments == ["as_44b2"]
    assert parsed.listening is not None
    assert parsed.listening.mode == "lrpt"
    assert parsed.clock_offset_s is None


def test_a_station_heartbeats_and_reads_back_the_assignment_it_is_given(
    app_and_rollback: Any, rollback: Any, tmp_path: Any
) -> None:
    """Round trip: the platform emits §4.3, the client parses every field of it.

    Including the inline element set, which is what lets a station propagate the
    orbit without fetching orbital data of its own.
    """
    transport, credentials = registered_station(app_and_rollback, rollback, tmp_path)
    issue_assignment(rollback, credentials.station_id, "as_delivered")

    with transport:
        response = parse_heartbeat_response(
            transport.heartbeat(
                build_heartbeat_body(
                    StationState(
                        station_id=credentials.station_id,
                        sent_at=datetime.now(UTC),
                        state="idle",
                    ),
                    [],
                )
            )
        )

    one = response.assignments[0]
    assert one.assignment_id == "as_delivered"
    assert one.centre_freq_hz == CENTRE_FREQ_HZ
    assert one.mode == "lrpt"
    assert one.timing_uncertainty_s == 4.2
    assert one.element_set.line1 == LINE1
    assert one.element_set.line2 == LINE2
    assert response.server_time.tzinfo is not None


def test_naming_an_assignment_moves_it_to_held_and_listening_starts_it(
    app_and_rollback: Any, rollback: Any, tmp_path: Any
) -> None:
    """The client's half of D-067, driven by the real client rather than raw JSON.

    Three heartbeats: the station learns of the work, confirms it holds it, then
    reports receiving it. The platform's state machine follows, and the
    assignment is still delivered while `in_progress`.
    """
    transport, credentials = registered_station(app_and_rollback, rollback, tmp_path)
    issue_assignment(rollback, credentials.station_id, "as_run")

    def beat(held: list[str], listening: Listening | None) -> Any:
        return parse_heartbeat_response(
            transport.heartbeat(
                build_heartbeat_body(
                    StationState(
                        station_id=credentials.station_id,
                        sent_at=datetime.now(UTC),
                        state="listening" if listening else "idle",
                        listening=listening,
                    ),
                    held,
                )
            )
        )

    with transport:
        beat([], None)
        assert state_of(rollback, "as_run") == "issued"

        beat(["as_run"], None)
        assert state_of(rollback, "as_run") == "held"

        final = beat(
            ["as_run"],
            Listening(
                assignment_id="as_run",
                satellite_id=SATELLITE_ID,
                centre_freq_hz=CENTRE_FREQ_HZ,
                mode="lrpt",
            ),
        )

    assert state_of(rollback, "as_run") == "in_progress"
    assert [one.assignment_id for one in final.assignments] == ["as_run"]


def test_a_revoked_token_stops_the_station_rather_than_looping(
    app_and_rollback: Any, rollback: Any, tmp_path: Any
) -> None:
    """D-024: a 401 means stop, log, and tell the operator.

    Asserted here because the retry policy must *not* fire — fifty stations
    retrying a revoked token every thirty seconds against a public endpoint is a
    denial of service the network inflicts on itself.
    """
    transport, credentials = registered_station(app_and_rollback, rollback, tmp_path)
    with rollback.cursor() as cur:
        cur.execute(
            "update stations set token_revoked_at = now() where station_id = %s",
            (credentials.station_id,),
        )

    with transport, pytest.raises(ProtocolError) as excinfo:
        transport.heartbeat(
            build_heartbeat_body(
                StationState(
                    station_id=credentials.station_id,
                    sent_at=datetime.now(UTC),
                    state="idle",
                ),
                [],
            )
        )

    assert excinfo.value.status == 401
    assert excinfo.value.code == "unauthorized"
