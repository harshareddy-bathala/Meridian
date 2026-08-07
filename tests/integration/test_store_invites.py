"""``meridian.store.invites`` against real TimescaleDB.

Marked ``integration`` by the directory hook in ``tests/conftest.py``. Every
test runs inside a transaction that is always rolled back (the ``rollback``
fixture below, matching the one in ``tests/integration/test_migrations.py``),
so nothing here ever reaches a real commit. The two seeding tests go further,
via ``empty_invite_tokens``: they clear the table first rather than assuming
it starts empty, because that assumption does not hold in general — see its
docstring.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.store.invites import (  # noqa: E402 — after importorskip
    create_invite,
    generate_invite_token,
    list_invites,
    revoke_invite,
    seed_bootstrap_invite,
)

pytestmark = pytest.mark.integration

ZERO_HASH = bytes(32)


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes, whether it commits or not.

    ``conn`` is session-scoped and shared across the whole integration run.
    The store functions call ``conn.transaction()`` internally, which nests
    as a savepoint inside this outer, unconditionally-rolled-back one rather
    than a real commit.
    """
    with conn.transaction(force_rollback=True):
        yield conn


def test_a_created_invite_is_listed(rollback: Any) -> None:
    create_invite(
        rollback, label="unit-test-create", expires_at=None, issued_for_station_id=None
    )
    labels = {invite.label for invite in list_invites(rollback)}
    assert "unit-test-create" in labels


def test_create_invite_rejects_an_unknown_station(rollback: Any) -> None:
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        create_invite(
            rollback,
            label="bad-binding",
            expires_at=None,
            issued_for_station_id="st_does_not_exist",
        )


def test_revoke_expires_a_pending_invite(rollback: Any) -> None:
    create_invite(
        rollback, label="unit-test-revoke", expires_at=None, issued_for_station_id=None
    )
    assert revoke_invite(rollback, label="unit-test-revoke") == 1

    (invite,) = [i for i in list_invites(rollback) if i.label == "unit-test-revoke"]
    assert invite.expires_at is not None
    assert invite.expires_at <= datetime.now(UTC)


def test_revoke_is_a_no_op_the_second_time(rollback: Any) -> None:
    create_invite(
        rollback,
        label="unit-test-double-revoke",
        expires_at=None,
        issued_for_station_id=None,
    )
    assert revoke_invite(rollback, label="unit-test-double-revoke") == 1
    assert revoke_invite(rollback, label="unit-test-double-revoke") == 0


def test_revoke_matching_nothing_returns_zero(rollback: Any) -> None:
    assert revoke_invite(rollback, label="no-such-label") == 0


def test_an_already_expired_invite_is_not_revocable(rollback: Any) -> None:
    create_invite(
        rollback,
        label="unit-test-preexpired",
        expires_at=datetime.now(UTC) - timedelta(days=1),
        issued_for_station_id=None,
    )
    assert revoke_invite(rollback, label="unit-test-preexpired") == 0


def test_a_consumed_invite_is_not_revocable(rollback: Any) -> None:
    create_invite(
        rollback,
        label="unit-test-consumed",
        expires_at=None,
        issued_for_station_id=None,
    )
    with rollback.cursor() as cur:
        cur.execute(
            "insert into stations (station_id, name, operator, lat_deg, lon_deg,"
            " alt_m, token_sha256, registration_key_sha256)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s)",
            ("st_invite_test", "Test", "tests", 0.0, 0.0, 0.0, ZERO_HASH, ZERO_HASH),
        )
        cur.execute(
            "update invite_tokens set consumed_at = now(), consumed_by_station_id = %s"
            " where label = %s",
            ("st_invite_test", "unit-test-consumed"),
        )

    assert revoke_invite(rollback, label="unit-test-consumed") == 0


@pytest.fixture
def empty_invite_tokens(rollback: Any) -> Any:
    """A transaction-scoped guarantee that ``invite_tokens`` starts empty.

    ``seed_bootstrap_invite``'s entire contract is about whether the table is
    empty, so its tests cannot rely on "nothing else has committed a row" —
    that was true only by accident of execution order, and it broke the first
    time this suite ran next to a manual ``meridian invite create`` against
    the same database (found running this exact scenario, not by inspection).
    Deleting here is safe: it happens inside ``rollback``'s already-open,
    unconditionally-rolled-back transaction, so nothing real is lost.
    """
    with rollback.cursor() as cur:
        cur.execute("delete from invite_tokens")
    return rollback


def test_seed_bootstrap_invite_inserts_once(empty_invite_tokens: Any) -> None:
    """D-020: safe to call unconditionally — it writes at most once."""
    token, _ = generate_invite_token()
    assert seed_bootstrap_invite(empty_invite_tokens, token=token) is True
    assert seed_bootstrap_invite(empty_invite_tokens, token=token) is False

    labels = [i.label for i in list_invites(empty_invite_tokens)]
    assert labels == ["environment bootstrap"]


def test_seed_bootstrap_invite_does_not_touch_an_existing_row(
    empty_invite_tokens: Any,
) -> None:
    """Not overwriting or recreating a consumed invite — D-020's actual requirement."""
    create_invite(
        empty_invite_tokens,
        label="already here",
        expires_at=None,
        issued_for_station_id=None,
    )
    token, _ = generate_invite_token()

    assert seed_bootstrap_invite(empty_invite_tokens, token=token) is False
    labels = [i.label for i in list_invites(empty_invite_tokens)]
    assert labels == ["already here"]
