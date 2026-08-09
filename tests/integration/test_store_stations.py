"""``meridian.store.stations`` against real TimescaleDB.

Marked ``integration`` by the directory hook in ``tests/conftest.py``. Every
test runs inside a transaction that is always rolled back — the same
``rollback`` fixture used in ``tests/integration/test_store_invites.py`` and
``tests/integration/test_migrations.py``.
"""

from __future__ import annotations

from argparse import Namespace
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.cli import EXIT_FAILED, _station_revoke  # noqa: E402
from meridian.store.invites import consume_invite  # noqa: E402 — after importorskip
from meridian.store.station_tokens import (  # noqa: E402
    find_station_id_by_token_hash,
    revoke_station_token,
    rotate_station_token,
)
from meridian.store.stations import (  # noqa: E402
    Capability,
    NewStation,
    find_station_for_recovery,
    find_station_heartbeat,
    find_station_provenance,
    insert_station,
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
        client_implementation="meridian-reference",
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
    # D-048: the registry compares this against the recovering request rather
    # than discarding it, so the recovery lookup has to carry it.
    assert info.simulated is False


def test_find_station_provenance_returns_a_real_station(rollback: Any) -> None:
    insert_station(rollback, sample_station("st_real"), [SAMPLE_CAPABILITY])

    provenance = find_station_provenance(rollback, "st_real")

    assert provenance is not None
    assert provenance.simulated is False
    assert provenance.simulator_run_id is None
    assert provenance.seed is None


def test_find_station_provenance_returns_a_simulated_station(rollback: Any) -> None:
    """D-048: this is the only admissible source of ``simulated`` for anything
    a station sends — a heartbeat's own claim is not evidence."""
    simulated = replace(
        sample_station("st_sim"),
        simulated=True,
        simulator_run_id="run_2026_08_08",
        seed=4471,
    )
    insert_station(rollback, simulated, [SAMPLE_CAPABILITY])

    provenance = find_station_provenance(rollback, "st_sim")

    assert provenance is not None
    assert provenance.simulated is True
    assert provenance.simulator_run_id == "run_2026_08_08"
    assert provenance.seed == 4471


def test_find_station_provenance_returns_none_for_an_unknown_station(
    rollback: Any,
) -> None:
    assert find_station_provenance(rollback, "st_does_not_exist") is None


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


def test_rotate_station_token_reports_that_it_minted(rollback: Any) -> None:
    """The return value is what tells the caller its plaintext token is live."""
    insert_station(rollback, sample_station("st_rotate_ok"), [SAMPLE_CAPABILITY])

    assert (
        rotate_station_token(
            rollback, station_id="st_rotate_ok", token_sha256=bytes(range(32))
        )
        is True
    )


def test_rotate_station_token_refuses_a_deleted_station(rollback: Any) -> None:
    """D-058: minting onto a deleted row would answer a recovery with a token
    that ``find_station_id_by_token_hash`` then refuses — a success that did
    nothing. The old hash must also survive, or a deleted station would lose
    the credential record that says what it last held."""
    old_hash = bytes([3]) * 32
    insert_station(
        rollback,
        sample_station("st_rotate_deleted", token_sha256=old_hash),
        [SAMPLE_CAPABILITY],
    )
    with rollback.cursor() as cur:
        cur.execute(
            "update stations set deleted_at = now() where station_id = %s",
            ("st_rotate_deleted",),
        )

    minted = rotate_station_token(
        rollback, station_id="st_rotate_deleted", token_sha256=bytes(range(32))
    )

    assert minted is False
    with rollback.cursor() as cur:
        cur.execute(
            "select token_sha256 from stations where station_id = %s",
            ("st_rotate_deleted",),
        )
        (stored_hash,) = cur.fetchone()
    assert bytes(stored_hash) == old_hash


def test_rotate_station_token_reports_an_unknown_station(rollback: Any) -> None:
    assert (
        rotate_station_token(
            rollback, station_id="st_does_not_exist", token_sha256=bytes(range(32))
        )
        is False
    )


def test_find_station_for_recovery_ignores_a_deleted_station(rollback: Any) -> None:
    """D-058: a deleted station reads as absent throughout this module, so a
    recovery cannot reach the rotation that would mint an unusable token."""
    insert_station(rollback, sample_station("st_recover_deleted"), [SAMPLE_CAPABILITY])
    with rollback.cursor() as cur:
        cur.execute(
            "update stations set deleted_at = now() where station_id = %s",
            ("st_recover_deleted",),
        )

    assert find_station_for_recovery(rollback, "st_recover_deleted") is None


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


def test_revoking_a_token_stops_the_next_lookup(rollback: Any) -> None:
    """Stage 5's completion gate: revocation is immediate.

    Immediate by construction rather than by invalidation —
    ``find_station_id_by_token_hash`` filters on ``token_revoked_at`` at lookup,
    so there is no cache between the write and the next request that could
    still be holding the old answer.
    """
    token = bytes([9]) * 32
    insert_station(
        rollback, sample_station("st_revoke", token_sha256=token), [SAMPLE_CAPABILITY]
    )
    assert find_station_id_by_token_hash(rollback, token) == "st_revoke"

    assert revoke_station_token(rollback, station_id="st_revoke") is True

    assert find_station_id_by_token_hash(rollback, token) is None


def test_revoking_twice_keeps_the_first_instant(rollback: Any) -> None:
    """The first revocation is when the credential died.

    Overwriting it with the moment somebody repeated the command would lose the
    only record of when the exposure ended, so the second call is a no-op that
    reports as one.
    """
    insert_station(
        rollback,
        sample_station("st_twice", token_sha256=bytes([8]) * 32),
        [SAMPLE_CAPABILITY],
    )
    assert revoke_station_token(rollback, station_id="st_twice") is True

    with rollback.cursor() as cur:
        cur.execute(
            "select token_revoked_at from stations where station_id = %s",
            ("st_twice",),
        )
        row = cur.fetchone()
    assert row is not None
    first_revoked_at = row[0]

    assert revoke_station_token(rollback, station_id="st_twice") is False

    with rollback.cursor() as cur:
        cur.execute(
            "select token_revoked_at from stations where station_id = %s",
            ("st_twice",),
        )
        assert cur.fetchone() == (first_revoked_at,)


def test_revoking_an_unknown_station_reports_nothing_done(rollback: Any) -> None:
    """False, not an exception. The operator gets an exit code, not a traceback."""
    assert revoke_station_token(rollback, station_id="st_does_not_exist") is False


def test_rotating_a_token_lifts_an_earlier_revocation(rollback: Any) -> None:
    """D-034's recovery path has to survive revocation, or it cannot recover.

    A station shut out by ``meridian station revoke`` is readmitted by an invite
    bound to it, which rotates its token. If rotation left ``token_revoked_at``
    set, the freshly minted token would authenticate as nothing and the operator
    would have no way back in at all.
    """
    insert_station(
        rollback,
        sample_station("st_rotate_after_revoke", token_sha256=bytes([7]) * 32),
        [SAMPLE_CAPABILITY],
    )
    revoke_station_token(rollback, station_id="st_rotate_after_revoke")

    replacement = bytes([6]) * 32
    rotate_station_token(
        rollback, station_id="st_rotate_after_revoke", token_sha256=replacement
    )

    assert (
        find_station_id_by_token_hash(rollback, replacement) == "st_rotate_after_revoke"
    )


def test_the_cli_handler_reports_a_revocation_and_its_exit_code(
    rollback: Any, capsys: Any
) -> None:
    """`meridian station revoke`, one layer below the connection it opens.

    ``_station_revoke`` is called directly with this test's rolled-back
    connection rather than through ``_run_station``, which would open its own
    connection and leave the row behind. What is under test is the handler:
    the exit code, and that the operator is told the way back in.
    """
    insert_station(
        rollback,
        sample_station("st_cli", token_sha256=bytes([5]) * 32),
        [SAMPLE_CAPABILITY],
    )

    code = _station_revoke(rollback, Namespace(station_id="st_cli"))

    assert code == 0
    output = capsys.readouterr()
    assert "st_cli" in output.out
    # D-024: the station stops rather than re-registering, so the operator has
    # to be told that a bound invite is what readmits it.
    assert "--for-station st_cli" in output.err


def test_the_cli_handler_fails_on_a_station_with_no_live_token(
    rollback: Any, capsys: Any
) -> None:
    """EXIT_FAILED, and a message that does not claim the station is unknown.

    "No station with a live token" covers absent, deleted and already-revoked
    alike. An operator does not need those told apart — in all three the
    station holds nothing that works.
    """
    code = _station_revoke(rollback, Namespace(station_id="st_absent"))

    assert code == EXIT_FAILED
    assert "no station with a live token" in capsys.readouterr().err


def test_find_station_heartbeat_separates_absent_from_never_reported(
    rollback: Any,
) -> None:
    """Two `None`s at different levels, meaning different things.

    No row at all versus a row that has never reported. `derive_liveness` turns
    the second into `never_seen`; the first is a caller bug the registry raises
    on, and collapsing them here would take that distinction away.
    """
    insert_station(
        rollback,
        sample_station("st_hb", token_sha256=bytes([4]) * 32),
        [SAMPLE_CAPABILITY],
    )

    found = find_station_heartbeat(rollback, "st_hb")
    assert found is not None
    assert found.last_heartbeat_at is None

    assert find_station_heartbeat(rollback, "st_never_registered") is None


def test_a_soft_deleted_station_reads_as_absent_not_offline(rollback: Any) -> None:
    """A decommissioned station is not a fault to investigate.

    Without the `deleted_at` filter it would keep ageing and eventually report
    `offline` forever, putting a permanent red row on the dashboard for a
    station somebody deliberately retired.
    """
    insert_station(
        rollback,
        sample_station("st_gone", token_sha256=bytes([3]) * 32),
        [SAMPLE_CAPABILITY],
    )
    with rollback.cursor() as cur:
        cur.execute(
            "update stations set deleted_at = now() where station_id = %s",
            ("st_gone",),
        )

    assert find_station_heartbeat(rollback, "st_gone") is None
