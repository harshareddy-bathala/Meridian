"""``store.assignment_expiry`` — the periodic sweep D-067 owed.

It expires work nobody took, and nothing else: never work a station took, never
a skip, never a window still open.

Reference: docs/DECISIONS.md D-008, D-067, D-165, D-183.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytest.importorskip("psycopg")

from meridian.store.assignment_expiry import expire_untaken_assignments

pytestmark = pytest.mark.integration

AOS = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
AFTER = AOS + timedelta(hours=1)


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes."""
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def pass_id(schedule_rows: Any) -> int:
    schedule_rows.station("st_a")
    return int(
        schedule_rows.pass_(
            "st_a", AOS, element_set_id=schedule_rows.satellite("norad:99970")
        )
    )


def state_of(conn: Any, assignment_id: str) -> str:
    with conn.cursor() as cur:
        cur.execute(
            "select state from assignments where assignment_id = %s", (assignment_id,)
        )
        return str(cur.fetchone()[0])


def test_work_nobody_took_expires_once_its_window_closes(
    schedule_rows: Any, pass_id: int
) -> None:
    schedule_rows.assignment("as_1", pass_id, state="issued")

    expired = expire_untaken_assignments(schedule_rows.conn, now=AFTER)

    assert (expired, state_of(schedule_rows.conn, "as_1")) == (1, "expired")


def test_an_open_window_is_left_alone(schedule_rows: Any, pass_id: int) -> None:
    schedule_rows.assignment("as_1", pass_id, state="issued")

    expired = expire_untaken_assignments(
        schedule_rows.conn, now=AOS + timedelta(minutes=5)
    )

    assert (expired, state_of(schedule_rows.conn, "as_1")) == (0, "issued")


@pytest.mark.parametrize("state", ["held", "in_progress", "reported"])
def test_work_a_station_took_is_never_expired_by_the_timer(
    schedule_rows: Any, pass_id: int, state: str
) -> None:
    """Its report may still be in the station's queue (D-067)."""
    schedule_rows.assignment("as_1", pass_id, state=state)

    expire_untaken_assignments(schedule_rows.conn, now=AFTER)

    assert state_of(schedule_rows.conn, "as_1") == state


def test_a_skip_is_never_expired(schedule_rows: Any, pass_id: int) -> None:
    """Nothing was delivered for it, so nothing can have been declined (D-165)."""
    schedule_rows.assignment("as_1", pass_id, decision="skipped", state="issued")

    expire_untaken_assignments(schedule_rows.conn, now=AFTER)

    assert state_of(schedule_rows.conn, "as_1") == "issued"
