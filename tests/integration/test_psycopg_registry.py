"""``PsycopgRegistry`` against real TimescaleDB.

Marked ``integration``. Drives the full MSP §4.1 six-row decision table
end to end — invite in, station row and bearer token out — using the
``rollback`` fixture pattern already established in
``test_store_invites.py`` and ``test_store_stations.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.registry import InvalidInviteError, RegistrationRequest  # noqa: E402
from meridian.registry.psycopg_registry import PsycopgRegistry  # noqa: E402
from meridian.store.invites import hash_invite_token, revoke_invite  # noqa: E402
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
    return PsycopgRegistry(
        rollback,
        pepper=PEPPER,
        recovery_window_s=RECOVERY_WINDOW_S,
        now_utc=datetime.now(UTC),
    )


def sample_request(
    *,
    invite_token: str,
    registration_key: str = "the-registration-key",
    simulated: bool = False,
) -> RegistrationRequest:
    return RegistrationRequest(
        invite_token=invite_token,
        registration_key=registration_key,
        name="Test station",
        operator="tests",
        lat_deg=12.9716,
        lon_deg=77.5946,
        alt_m=920.0,
        simulated=simulated,
        simulator_run_id=None,
        seed=None,
        capabilities=[SAMPLE_CAPABILITY],
        client_implementation="meridian-reference",
        client_version="0.1.0",
    )


def insert_invite(
    rollback: Any,
    *,
    plaintext: str,
    issued_for_station_id: str | None = None,
    consumed_by_station_id: str | None = None,
    expires_at: datetime | None = None,
) -> None:
    """A raw-SQL invite row — this suite drives ``PsycopgRegistry`` from the
    presented side, so the fixture data needs full control over invite state
    that only an operator or a prior consumption would normally produce."""
    with rollback.cursor() as cur:
        cur.execute(
            "insert into invite_tokens"
            " (token_sha256, label, issued_for_station_id,"
            " consumed_at, consumed_by_station_id, expires_at)"
            " values (%s, %s, %s, %s, %s, %s)",
            (
                hash_invite_token(plaintext),
                "test-invite",
                issued_for_station_id,
                datetime.now(UTC) if consumed_by_station_id else None,
                consumed_by_station_id,
                expires_at,
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


def test_an_expired_invite_is_rejected(
    rollback: Any, registry: PsycopgRegistry
) -> None:
    """D-046. Before this check existed, `meridian invite revoke` set
    ``expires_at`` and ``register()`` went on admitting the invite anyway."""
    insert_invite(
        rollback,
        plaintext="lapsed-invite",
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    with pytest.raises(InvalidInviteError):
        registry.register(sample_request(invite_token="lapsed-invite"))


def test_an_invite_with_a_future_expiry_still_registers(
    rollback: Any, registry: PsycopgRegistry
) -> None:
    """The other side of D-046 — the check must not reject a live invite."""
    insert_invite(
        rollback,
        plaintext="still-valid-invite",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    result = registry.register(sample_request(invite_token="still-valid-invite"))

    assert result.station_id.startswith("st_")


def test_a_revoked_invite_is_rejected_through_the_store_layer(
    rollback: Any, registry: PsycopgRegistry
) -> None:
    """End to end through ``revoke_invite`` rather than a hand-set timestamp,
    so the test breaks if revocation ever stops being expiry (D-046)."""
    insert_invite(rollback, plaintext="to-be-revoked")

    assert revoke_invite(rollback, label="test-invite") == 1

    with pytest.raises(InvalidInviteError):
        registry.register(sample_request(invite_token="to-be-revoked"))


def test_an_expired_bound_invite_is_rejected(
    rollback: Any, registry: PsycopgRegistry
) -> None:
    """D-046's second decision: D-034 exempts a bound invite from the recovery
    *window*, not from its own expiry. An operator who withdrew a bound invite
    withdrew the authorisation it carried."""
    insert_invite(rollback, plaintext="original-invite-7")
    created = registry.register(
        sample_request(invite_token="original-invite-7", registration_key="my-key")
    )

    insert_invite(
        rollback,
        plaintext="expired-bound-invite",
        issued_for_station_id=created.station_id,
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    with pytest.raises(InvalidInviteError):
        registry.register(
            sample_request(
                invite_token="expired-bound-invite", registration_key="my-key"
            )
        )


def test_losing_the_consume_race_rejects_and_leaves_no_station(
    rollback: Any, registry: PsycopgRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-047. The real race needs two committed connections, which the
    ``rollback`` isolation pattern cannot express; the property under test is
    narrower than that anyway — that ``consume_invite`` returning ``False``
    rejects the registration and rolls its station row back, rather than
    being discarded. Forcing the return value tests exactly that, and
    deterministically."""
    monkeypatch.setattr(
        "meridian.registry.psycopg_registry.consume_invite",
        lambda *_args, **_kwargs: False,
    )
    insert_invite(rollback, plaintext="contended-invite")

    with pytest.raises(InvalidInviteError):
        registry.register(sample_request(invite_token="contended-invite"))

    with rollback.cursor() as cur:
        cur.execute("select count(*) from stations")
        (station_count,) = cur.fetchone()
    assert station_count == 0


def test_unbound_recovery_claiming_a_different_simulated_is_rejected(
    rollback: Any, registry: PsycopgRegistry
) -> None:
    """D-048. A station cannot change its own nature. Before this check, the
    platform answered 200 to a request whose central claim it had discarded."""
    insert_invite(rollback, plaintext="original-invite-8")
    created = registry.register(
        sample_request(invite_token="original-invite-8", registration_key="my-key")
    )

    insert_invite(
        rollback,
        plaintext="recovery-invite-8",
        consumed_by_station_id=created.station_id,
    )
    with pytest.raises(InvalidInviteError):
        registry.register(
            sample_request(
                invite_token="recovery-invite-8",
                registration_key="my-key",
                simulated=True,
            )
        )


def test_bound_recovery_claiming_a_different_simulated_is_rejected(
    rollback: Any, registry: PsycopgRegistry
) -> None:
    """The same rule on D-034's path — an operator authorising a rotation does
    not authorise the station changing what kind of station it is."""
    insert_invite(rollback, plaintext="original-invite-9")
    created = registry.register(
        sample_request(invite_token="original-invite-9", registration_key="my-key")
    )

    insert_invite(
        rollback,
        plaintext="bound-invite-9",
        issued_for_station_id=created.station_id,
    )
    with pytest.raises(InvalidInviteError):
        registry.register(
            sample_request(
                invite_token="bound-invite-9",
                registration_key="my-key",
                simulated=True,
            )
        )


def test_recovery_with_a_matching_simulated_still_succeeds(
    rollback: Any, registry: PsycopgRegistry
) -> None:
    """The other side of D-048 — the check must not break ordinary recovery."""
    insert_invite(rollback, plaintext="original-invite-10")
    created = registry.register(
        sample_request(invite_token="original-invite-10", registration_key="my-key")
    )

    insert_invite(
        rollback,
        plaintext="recovery-invite-10",
        consumed_by_station_id=created.station_id,
    )
    recovered = registry.register(
        sample_request(
            invite_token="recovery-invite-10",
            registration_key="my-key",
            simulated=False,
        )
    )

    assert recovered.station_id == created.station_id
