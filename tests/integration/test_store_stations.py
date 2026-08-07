"""``meridian.store.stations`` against real TimescaleDB.

Marked ``integration`` by the directory hook in ``tests/conftest.py``. Every
test runs inside a transaction that is always rolled back — the same
``rollback`` fixture used in ``tests/integration/test_store_invites.py`` and
``tests/integration/test_migrations.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.store.invites import consume_invite  # noqa: E402 — after importorskip
from meridian.store.stations import (  # noqa: E402
    Capability,
    NewStation,
    find_station_for_recovery,
    find_station_id_by_token_hash,
    insert_station,
    rotate_station_token,
)

pytestmark = pytest.mark.integration

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


def sample_station(
    station_id: str,
    *,
    token_sha256: bytes | None = None,
    registration_key_sha256: bytes | None = None,
) -> NewStation:
    """A minimal, valid ``NewStation`` — every field but the ones under test."""
    return NewStation(
        station_id=station_id,
        name="Test station",
        operator="tests",
        lat_deg=12.9716,
        lon_deg=77.5946,
        alt_m=920.0,
        token_sha256=token_sha256 if token_sha256 is not None else bytes(32),
        registration_key_sha256=(
            registration_key_sha256
            if registration_key_sha256 is not None
            else bytes(32)
        ),
        simulated=False,
        simulator_run_id=None,
        seed=None,
        client_impl="meridian-reference",
        client_version="0.1.0",
    )


def test_insert_station_writes_the_station_and_its_capability(rollback: Any) -> None:
    insert_station(rollback, sample_station("st_insert"), [SAMPLE_CAPABILITY])

    with rollback.cursor() as cur:
        cur.execute(
            "select name, operator from stations where station_id = %s", ("st_insert",)
        )
        assert cur.fetchone() == ("Test station", "tests")

        cur.execute(
            "select band, freq_min_hz, freq_max_hz, modes, polarisation,"
            " tracking, min_elevation_deg"
            " from station_capabilities where station_id = %s",
            ("st_insert",),
        )
        assert cur.fetchone() == (
            "vhf",
            136_000_000,
            138_000_000,
            ["lrpt"],
            "rhcp",
            True,
            10.0,
        )


def test_insert_station_rejects_a_duplicate_station_id(rollback: Any) -> None:
    insert_station(rollback, sample_station("st_dup"), [SAMPLE_CAPABILITY])
    with pytest.raises(psycopg.errors.UniqueViolation):
        insert_station(rollback, sample_station("st_dup"), [SAMPLE_CAPABILITY])


def test_find_station_for_recovery_returns_the_stored_fields(rollback: Any) -> None:
    key_hash = bytes(range(32))
    insert_station(
        rollback,
        sample_station("st_recovery", registration_key_sha256=key_hash),
        [SAMPLE_CAPABILITY],
    )

    info = find_station_for_recovery(rollback, "st_recovery")

    assert info is not None
    assert info.registration_key_sha256 == key_hash
    assert info.last_heartbeat_at is None


def test_find_station_for_recovery_returns_none_for_an_unknown_station(
    rollback: Any,
) -> None:
    assert find_station_for_recovery(rollback, "st_does_not_exist") is None


def test_rotate_station_token_mints_and_unrevokes(rollback: Any) -> None:
    old_hash = bytes(32)
    insert_station(
        rollback,
        sample_station("st_rotate", token_sha256=old_hash),
        [SAMPLE_CAPABILITY],
    )
    with rollback.cursor() as cur:
        cur.execute(
            "update stations set token_revoked_at = now() where station_id = %s",
            ("st_rotate",),
        )

    new_hash = bytes(range(32))
    rotate_station_token(rollback, station_id="st_rotate", token_sha256=new_hash)

    with rollback.cursor() as cur:
        cur.execute(
            "select token_sha256, token_revoked_at from stations where station_id = %s",
            ("st_rotate",),
        )
        stored_hash, revoked_at = cur.fetchone()
    assert bytes(stored_hash) == new_hash
    assert revoked_at is None


def test_find_station_id_by_token_hash_finds_a_live_station(rollback: Any) -> None:
    token_hash = bytes(range(32))
    insert_station(
        rollback,
        sample_station("st_live", token_sha256=token_hash),
        [SAMPLE_CAPABILITY],
    )
    assert find_station_id_by_token_hash(rollback, token_hash) == "st_live"


def test_find_station_id_by_token_hash_excludes_a_revoked_station(
    rollback: Any,
) -> None:
    token_hash = bytes(range(32))
    insert_station(
        rollback,
        sample_station("st_revoked", token_sha256=token_hash),
        [SAMPLE_CAPABILITY],
    )
    with rollback.cursor() as cur:
        cur.execute(
            "update stations set token_revoked_at = now() where station_id = %s",
            ("st_revoked",),
        )
    assert find_station_id_by_token_hash(rollback, token_hash) is None


def test_find_station_id_by_token_hash_excludes_a_deleted_station(
    rollback: Any,
) -> None:
    """A separate column from ``token_revoked_at`` — a query checking only one
    would pass this test's sibling while silently authenticating a deleted
    station."""
    token_hash = bytes(range(32))
    insert_station(
        rollback,
        sample_station("st_deleted", token_sha256=token_hash),
        [SAMPLE_CAPABILITY],
    )
    with rollback.cursor() as cur:
        cur.execute(
            "update stations set deleted_at = now() where station_id = %s",
            ("st_deleted",),
        )
    assert find_station_id_by_token_hash(rollback, token_hash) is None


def test_a_failed_station_insert_rolls_back_its_invite_consumption(
    rollback: Any,
) -> None:
    """Composed atomicity is the caller's transaction, not a new mechanism.

    ``consume_invite`` and ``insert_station`` each manage their own
    ``conn.transaction()``, which nests as a savepoint under a caller-managed
    one (see ``store.invites.create_invite``'s docstring). Proven here by
    opening exactly that caller-level transaction, forcing the second call to
    fail, and confirming the first call's effect did not survive it.
    """
    token_sha256 = bytes(32)
    with rollback.cursor() as cur:
        cur.execute(
            "insert into invite_tokens (token_sha256, label) values (%s, %s)",
            (token_sha256, "composed-test"),
        )
        cur.execute(
            "insert into stations (station_id, name, operator, lat_deg, lon_deg,"
            " alt_m, token_sha256, registration_key_sha256)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s)",
            ("st_collision", "Existing", "tests", 0.0, 0.0, 0.0, bytes(32), bytes(32)),
        )

    with pytest.raises(psycopg.errors.UniqueViolation), rollback.transaction():
        assert (
            consume_invite(
                rollback, token_sha256=token_sha256, station_id="st_collision"
            )
            is True
        )
        insert_station(rollback, sample_station("st_collision"), [])

    with rollback.cursor() as cur:
        cur.execute(
            "select consumed_at from invite_tokens where token_sha256 = %s",
            (token_sha256,),
        )
        (consumed_at,) = cur.fetchone()
    assert consumed_at is None
