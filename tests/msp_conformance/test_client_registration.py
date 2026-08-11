"""The reference client registering against the real platform, over real MSP.

``tests/unit/test_register_body.py`` checks the body this client builds against
the specification text. This checks it against the implementation that has to
accept it, and then runs the recovery flow D-023 exists for — because a client
and a platform can each be individually correct about a field name and still
disagree, and neither package's own tests would notice (D-012).

A real :class:`MspTransport` is pointed at the application in-process, through
the ``http_transport`` seam the class provides. Everything else about it is the
real thing — the ``MSP-Version`` header, the ``Authorization`` header, the retry
loop, the error parsing — because a fixture that reproduced those by hand would
be testing the fixture.

Marked ``msp_conformance`` by the directory hook in ``tests/conftest.py``.

Reference: docs/MSP-SPEC.md §3, §4.1; docs/DECISIONS.md D-012, D-023, D-034.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from meridian.api.app import create_app
from meridian.api.dependencies import get_connection
from meridian.api.models.registration import RegisterRequestBody
from meridian.store.invites import hash_invite_token
from meridian_client.credentials import load_credentials, save_credentials
from meridian_client.registration import (
    ReceiveChain,
    StationProfile,
    build_register_body,
    register,
)
from meridian_client.transport import MspTransport, ProtocolError

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
    """Undo everything this test writes — see test_register_endpoint.py's twin."""
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def transport(
    database_url: str, rollback: Any, monkeypatch: pytest.MonkeyPatch
) -> Iterator[MspTransport]:
    """A real ``MspTransport`` whose requests reach the real application.

    ``TestClient`` is entered for two things: it runs the lifespan that populates
    ``app.state``, and it owns the only *synchronous* ASGI transport available —
    ``httpx.ASGITransport`` implements ``handle_async_request`` only, and
    :class:`MspTransport` builds a synchronous ``httpx.Client`` because a station
    client has no reason to be asynchronous. Borrowing that transport is what
    lets the station's own headers, retry loop and error parsing be the ones
    under test, rather than a fixture's reproduction of them.
    """
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("TOKEN_HASH_PEPPER", "client-registration-pepper")
    app = create_app()
    app.dependency_overrides[get_connection] = lambda: rollback

    with TestClient(app) as started:
        station_transport = MspTransport(
            "http://platform.test",
            http_transport=started._transport,
        )
        with station_transport:
            yield station_transport
    app.dependency_overrides.clear()


def issue_invite(rollback: Any, plaintext: str) -> None:
    """Put one unconsumed invite in the database, as the operator's CLI would."""
    with rollback.cursor() as cur:
        cur.execute(
            "insert into invite_tokens (token_sha256, label) values (%s, %s)",
            (hash_invite_token(plaintext), "client-registration"),
        )


def test_the_body_this_client_builds_is_one_the_platform_accepts() -> None:
    """The seam, with no HTTP in the way.

    Two distributions that never import each other cannot drift silently unless
    something compares them. This is that something: the client's own body,
    parsed by the platform's own model. A renamed field fails here, in the file
    whose name says why it exists, rather than as a `400 malformed` in a station
    log six weeks later.
    """
    body = build_register_body(PROFILE, "an-invite", "a-key")

    parsed = RegisterRequestBody.model_validate(body)

    assert parsed.location.lat_deg == 12.9716
    assert parsed.location.alt_m == 920.0
    assert parsed.capabilities[0].freq_min_hz == 136_000_000
    assert parsed.simulated is False


def test_a_simulated_station_s_body_is_also_one_the_platform_accepts() -> None:
    """MSP §5's fields travel together, and the platform's model agrees."""
    simulated = StationProfile(
        name="sim-001",
        operator="meridian",
        lat_deg=12.9716,
        lon_deg=77.5946,
        alt_m=920.0,
        capabilities=PROFILE.capabilities,
        simulated=True,
        simulator_run_id="run-1",
        seed=4471,
    )

    parsed = RegisterRequestBody.model_validate(
        build_register_body(simulated, "an-invite", "a-key")
    )

    assert parsed.simulated is True
    assert parsed.simulator_run_id == "run-1"
    assert parsed.seed == 4471


def test_a_station_registers_and_gets_credentials_it_can_persist(
    transport: MspTransport, rollback: Any, tmp_path: Path
) -> None:
    """Stage 4's client gate: register, write, restart, still be the same station."""
    issue_invite(rollback, "invite-one")

    credentials = register(
        transport,
        PROFILE,
        invite_token="invite-one",
        registration_key_path=tmp_path / "registration_key",
    )
    save_credentials(tmp_path / "credentials.json", credentials)

    assert credentials.station_id.startswith("st_")
    assert credentials.bearer_token
    assert credentials.heartbeat_interval_s > 0
    assert load_credentials(tmp_path / "credentials.json") == credentials


def test_the_key_is_on_disk_before_the_platform_is_asked(
    transport: MspTransport, rollback: Any, tmp_path: Path
) -> None:
    """D-023's ordering, and the reason the recovery below can work at all.

    The key the platform hashed is the key already written here. If `register`
    generated it in memory and wrote it after the response, a crash in between
    would consume the invite and leave nothing able to prove the retry came from
    the same station.
    """
    issue_invite(rollback, "invite-two")
    key_path = tmp_path / "registration_key"

    credentials = register(
        transport, PROFILE, invite_token="invite-two", registration_key_path=key_path
    )

    assert key_path.read_text(encoding="utf-8").strip() == credentials.registration_key


def test_a_retry_with_the_same_key_recovers_the_station_and_rotates_the_token(
    transport: MspTransport, rollback: Any, tmp_path: Path
) -> None:
    """The property D-023 was written for: `register` is idempotent for the client.

    The second call stands for a first response that was lost in transit. The
    invite is already consumed, so a protocol without the key would have to
    refuse — with it, the same station comes back with a working credential and
    no operator involved.
    """
    issue_invite(rollback, "invite-three")
    key_path = tmp_path / "registration_key"

    first = register(
        transport, PROFILE, invite_token="invite-three", registration_key_path=key_path
    )
    second = register(
        transport, PROFILE, invite_token="invite-three", registration_key_path=key_path
    )

    assert second.station_id == first.station_id
    assert second.bearer_token != first.bearer_token
    assert second.registration_key == first.registration_key


def test_a_consumed_invite_with_a_different_key_is_refused_without_a_reason(
    transport: MspTransport, rollback: Any, tmp_path: Path
) -> None:
    """MSP §3: a client may learn that it was refused, never why.

    "Already consumed", "key does not match" and "no such invite" are three
    different facts about the operator's invite table, and telling them apart is
    how a caller holding a leaked invite works out what to try next.
    """
    issue_invite(rollback, "invite-four")
    register(
        transport,
        PROFILE,
        invite_token="invite-four",
        registration_key_path=tmp_path / "first" / "registration_key",
    )

    with pytest.raises(ProtocolError) as excinfo:
        register(
            transport,
            PROFILE,
            invite_token="invite-four",
            registration_key_path=tmp_path / "second" / "registration_key",
        )

    assert excinfo.value.code == "invalid_invite"
    assert excinfo.value.status == 403
    lowered = excinfo.value.message.lower()
    assert "consumed" not in lowered
    assert "match" not in lowered


def test_an_unknown_invite_is_refused_the_same_way(
    transport: MspTransport, tmp_path: Path
) -> None:
    """Indistinguishable from the case above, which is the whole point of §3."""
    with pytest.raises(ProtocolError) as excinfo:
        register(
            transport,
            PROFILE,
            invite_token="never-issued",
            registration_key_path=tmp_path / "registration_key",
        )

    assert excinfo.value.code == "invalid_invite"
