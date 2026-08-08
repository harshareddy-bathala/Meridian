"""``PsycopgRegistry`` against real TimescaleDB.

Marked ``integration``. Drives the full MSP §4.1 six-row decision table
end to end — invite in, station row and bearer token out — using the
``rollback`` fixture pattern already established in
``test_store_invites.py`` and ``test_store_stations.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.registry import InvalidInviteError, RegistrationRequest  # noqa: E402
from meridian.registry.psycopg_registry import PsycopgRegistry  # noqa: E402
from meridian.store.invites import hash_invite_token  # noqa: E402
from meridian.store.stations import Capability  # noqa: E402

pytestmark = pytest.mark.integration

PEPPER = "test-pepper"
RECOVERY_WINDOW_S = 3600

SAMPLE_CAPABILITY = Capability(
    band="vhf",
    freq_min_hz=136_000_000,
    freq_max_hz=138_000_000,
    modes=("lrpt",),
    polarisation="rhcp",
    tracking=True,
    min_elevation_deg=10.0,
)


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes — see test_store_invites.py's twin."""
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def registry(rollback: Any) -> PsycopgRegistry:
    return PsycopgRegistry(rollback, pepper=PEPPER, recovery_window_s=RECOVERY_WINDOW_S)


def sample_request(
    *, invite_token: str, registration_key: str = "the-registration-key"
) -> RegistrationRequest:
    return RegistrationRequest(
        invite_token=invite_token,
        registration_key=registration_key,
        name="Test station",
        operator="tests",
        lat_deg=12.9716,
        lon_deg=77.5946,
        alt_m=920.0,
        simulated=False,
        simulator_run_id=None,
        seed=None,
        capabilities=[SAMPLE_CAPABILITY],
        client_impl="meridian-reference",
        client_version="0.1.0",
    )


def insert_invite(
    rollback: Any,
    *,
    plaintext: str,
    issued_for_station_id: str | None = None,
    consumed_by_station_id: str | None = None,
) -> None:
    """A raw-SQL invite row — this suite drives ``PsycopgRegistry`` from the
    presented side, so the fixture data needs full control over invite state
    that only an operator or a prior consumption would normally produce."""
    with rollback.cursor() as cur:
        cur.execute(
            "insert into invite_tokens"
            " (token_sha256, label, issued_for_station_id,"
            " consumed_at, consumed_by_station_id)"
            " values (%s, %s, %s, %s, %s)",
            (
                hash_invite_token(plaintext),
                "test-invite",
                issued_for_station_id,
                datetime.now(UTC) if consumed_by_station_id else None,
                consumed_by_station_id,
            ),
        )


def mark_heartbeat_seen(rollback: Any, station_id: str) -> None:
    """Simulate a station that has heartbeat — closes D-023's recovery window."""
    with rollback.cursor() as cur:
        cur.execute(
            "update stations set last_heartbeat_at = now() where station_id = %s",
            (station_id,),
        )


def test_unconsumed_unbound_invite_creates_a_station(
    rollback: Any, registry: PsycopgRegistry
) -> None:
    insert_invite(rollback, plaintext="create-invite")
    result = registry.register(sample_request(invite_token="create-invite"))

    assert result.station_id.startswith("st_")
    assert registry.authenticate(result.bearer_token) == result.station_id


def test_unknown_invite_token_is_rejected(registry: PsycopgRegistry) -> None:
    with pytest.raises(InvalidInviteError):
        registry.register(sample_request(invite_token="never-issued"))


def test_consumed_unbound_recovery_with_matching_key_mints_a_new_token(
    rollback: Any, registry: PsycopgRegistry
) -> None:
    insert_invite(rollback, plaintext="original-invite")
    created = registry.register(
        sample_request(invite_token="original-invite", registration_key="my-key")
    )

    insert_invite(
        rollback,
        plaintext="recovery-invite",
        consumed_by_station_id=created.station_id,
    )
    recovered = registry.register(
        sample_request(invite_token="recovery-invite", registration_key="my-key")
    )

    assert recovered.station_id == created.station_id
    assert recovered.bearer_token != created.bearer_token
    assert registry.authenticate(created.bearer_token) is None
    assert registry.authenticate(recovered.bearer_token) == created.station_id


def test_consumed_unbound_recovery_with_wrong_key_is_rejected(
    rollback: Any, registry: PsycopgRegistry
) -> None:
    insert_invite(rollback, plaintext="original-invite-2")
    created = registry.register(
        sample_request(invite_token="original-invite-2", registration_key="my-key")
    )

    insert_invite(
        rollback,
        plaintext="recovery-invite-2",
        consumed_by_station_id=created.station_id,
    )
    with pytest.raises(InvalidInviteError):
        registry.register(
            sample_request(
                invite_token="recovery-invite-2", registration_key="wrong-key"
            )
        )


def test_consumed_unbound_recovery_past_the_window_is_rejected(
    rollback: Any, registry: PsycopgRegistry
) -> None:
    insert_invite(rollback, plaintext="original-invite-3")
    created = registry.register(
        sample_request(invite_token="original-invite-3", registration_key="my-key")
    )
    mark_heartbeat_seen(rollback, created.station_id)

    insert_invite(
        rollback,
        plaintext="recovery-invite-3",
        consumed_by_station_id=created.station_id,
    )
    with pytest.raises(InvalidInviteError):
        registry.register(
            sample_request(invite_token="recovery-invite-3", registration_key="my-key")
        )


def test_unconsumed_bound_invite_matching_key_rotates_regardless_of_window(
    rollback: Any, registry: PsycopgRegistry
) -> None:
    """D-034: the operator issuing the bound invite is the authorisation —
    a heartbeat already seen, which would fail an unbound recovery, does not
    block this path at all."""
    insert_invite(rollback, plaintext="original-invite-4")
    created = registry.register(
        sample_request(invite_token="original-invite-4", registration_key="my-key")
    )
    mark_heartbeat_seen(rollback, created.station_id)

    insert_invite(
        rollback,
        plaintext="bound-invite-4",
        issued_for_station_id=created.station_id,
    )
    rotated = registry.register(
        sample_request(invite_token="bound-invite-4", registration_key="my-key")
    )

    assert rotated.station_id == created.station_id
    assert registry.authenticate(rotated.bearer_token) == created.station_id


def test_unconsumed_bound_invite_wrong_key_is_rejected(
    rollback: Any, registry: PsycopgRegistry
) -> None:
    insert_invite(rollback, plaintext="original-invite-5")
    created = registry.register(
        sample_request(invite_token="original-invite-5", registration_key="my-key")
    )

    insert_invite(
        rollback,
        plaintext="bound-invite-5",
        issued_for_station_id=created.station_id,
    )
    with pytest.raises(InvalidInviteError):
        registry.register(
            sample_request(invite_token="bound-invite-5", registration_key="wrong-key")
        )


def test_a_bound_invite_already_consumed_is_rejected(
    rollback: Any, registry: PsycopgRegistry
) -> None:
    insert_invite(rollback, plaintext="original-invite-6")
    created = registry.register(
        sample_request(invite_token="original-invite-6", registration_key="my-key")
    )

    insert_invite(
        rollback,
        plaintext="spent-bound-invite-6",
        issued_for_station_id=created.station_id,
        consumed_by_station_id=created.station_id,
    )
    with pytest.raises(InvalidInviteError):
        registry.register(
            sample_request(
                invite_token="spent-bound-invite-6", registration_key="my-key"
            )
        )


def test_authenticate_returns_none_for_a_garbage_token(
    registry: PsycopgRegistry,
) -> None:
    assert registry.authenticate("no-station-ever-had-this-token") is None
