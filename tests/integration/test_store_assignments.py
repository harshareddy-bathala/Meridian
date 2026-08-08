"""``meridian.store.assignments`` against real TimescaleDB.

Marked ``integration`` by the directory hook in ``tests/conftest.py``. The
``base_fixtures``/``insert_assignment`` pair builds the minimal row graph an
assignment needs — mirroring ``tests/integration/test_migrations.py``'s own
``fixtures`` fixture (a closure bound to one connection) rather than
inventing a second convention for it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.store.assignments import (  # noqa: E402 — after importorskip
    expire_overdue_assignments,
    find_assignment_ids_by_state,
    find_due_assignments,
    mark_assignment_in_progress,
    mark_assignments_held,
)

pytestmark = pytest.mark.integration

STATION_ID = "st_fixture"
SATELLITE_ID = "norad:99999"
ZERO_HASH = bytes(32)
LINE1 = "1 25544U 98067A   26226.50000000  .00001234  00000-0  12345-4 0  9991"
LINE2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.50377579123456"

InsertAssignment = Callable[..., None]


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes — see test_store_invites.py's twin."""
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def insert_assignment(rollback: Any) -> InsertAssignment:
    """The minimal row graph an assignment needs — one station, one
    satellite, one element set, one pass — built once, then a closure over
    the resulting ``pass_id`` and ``rollback`` so each test calls it with
    only the fields it actually varies."""
    with rollback.cursor() as cur:
        cur.execute(
            "insert into satellites (satellite_id, name) values (%s, %s)",
            (SATELLITE_ID, "Test satellite"),
        )
        cur.execute(
            "insert into stations (station_id, name, operator, lat_deg, lon_deg,"
            " alt_m, token_sha256, registration_key_sha256)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s)",
            (STATION_ID, "Test station", "tests", 0.0, 0.0, 0.0, ZERO_HASH, ZERO_HASH),
        )
        cur.execute(
            "insert into element_sets (satellite_id, epoch, line1, line2, source)"
            " values (%s, %s, %s, %s, %s) returning id",
            (
                SATELLITE_ID,
                datetime(2026, 8, 14, 2, 11, 0, tzinfo=UTC),
                LINE1,
                LINE2,
                "manual",
            ),
        )
        (element_set_id,) = cur.fetchone()
        cur.execute(
            "insert into passes (satellite_id, station_id, aos, los,"
            " max_elevation_deg, max_elevation_at, aos_azimuth_deg, los_azimuth_deg,"
            " element_set_id, min_elevation_deg)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id",
            (
                SATELLITE_ID,
                STATION_ID,
                datetime(2026, 8, 14, 9, 41, 20, tzinfo=UTC),
                datetime(2026, 8, 14, 9, 52, 7, tzinfo=UTC),
                61.4,
                datetime(2026, 8, 14, 9, 46, 40, tzinfo=UTC),
                10.0,
                200.0,
                element_set_id,
                10.0,
            ),
        )
        (pass_id,) = cur.fetchone()

    def _insert(
        *,
        assignment_id: str,
        start_at: datetime,
        end_at: datetime,
        state: str = "issued",
    ) -> None:
        with rollback.cursor() as cur:
            cur.execute(
                "insert into assignments (assignment_id, pass_id, station_id, start_at,"
                " end_at, centre_freq_hz, mode, timing_uncertainty_s, reason, state)"
                " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    assignment_id,
                    pass_id,
                    STATION_ID,
                    start_at,
                    end_at,
                    137900000,
                    "lrpt",
                    4.2,
                    "test fixture",
                    state,
                ),
            )

    return _insert


def test_find_assignment_ids_by_state(
    rollback: Any, insert_assignment: InsertAssignment
) -> None:
    now = datetime.now(UTC)
    end = now + timedelta(minutes=10)
    insert_assignment(
        assignment_id="as_issued", start_at=now, end_at=end, state="issued"
    )
    insert_assignment(assignment_id="as_held", start_at=now, end_at=end, state="held")
    insert_assignment(
        assignment_id="as_reported", start_at=now, end_at=end, state="reported"
    )

    ids = find_assignment_ids_by_state(rollback, STATION_ID, ["issued", "held"])

    assert ids == {"as_issued", "as_held"}


def test_mark_assignments_held_transitions_only_named_issued_rows(
    rollback: Any, insert_assignment: InsertAssignment
) -> None:
    now = datetime.now(UTC)
    end = now + timedelta(minutes=10)
    insert_assignment(assignment_id="as_a", start_at=now, end_at=end)
    insert_assignment(assignment_id="as_b", start_at=now, end_at=end)

    transitioned = mark_assignments_held(
        rollback, STATION_ID, ["as_a", "as_never_issued"]
    )

    assert transitioned == 1
    with rollback.cursor() as cur:
        cur.execute(
            "select assignment_id, state from assignments order by assignment_id"
        )
        rows = dict(cur.fetchall())
    assert rows == {"as_a": "held", "as_b": "issued"}


def test_mark_assignments_held_is_idempotent_on_an_already_held_row(
    rollback: Any, insert_assignment: InsertAssignment
) -> None:
    now = datetime.now(UTC)
    insert_assignment(
        assignment_id="as_c",
        start_at=now,
        end_at=now + timedelta(minutes=10),
        state="held",
    )

    assert mark_assignments_held(rollback, STATION_ID, ["as_c"]) == 0


def test_mark_assignment_in_progress_requires_held(
    rollback: Any, insert_assignment: InsertAssignment
) -> None:
    now = datetime.now(UTC)
    insert_assignment(
        assignment_id="as_d",
        start_at=now,
        end_at=now + timedelta(minutes=10),
        state="issued",
    )

    assert (
        mark_assignment_in_progress(
            rollback, station_id=STATION_ID, assignment_id="as_d"
        )
        is False
    )

    with rollback.cursor() as cur:
        cur.execute(
            "update assignments set state = 'held' where assignment_id = %s", ("as_d",)
        )

    assert (
        mark_assignment_in_progress(
            rollback, station_id=STATION_ID, assignment_id="as_d"
        )
        is True
    )


def test_expire_overdue_assignments_transitions_only_overdue_issued_or_held(
    rollback: Any, insert_assignment: InsertAssignment
) -> None:
    now = datetime.now(UTC)
    past_end = now - timedelta(hours=1)
    insert_assignment(
        assignment_id="as_overdue",
        start_at=past_end - timedelta(minutes=15),
        end_at=past_end,
        state="issued",
    )
    insert_assignment(
        assignment_id="as_current",
        start_at=now,
        end_at=now + timedelta(minutes=10),
        state="issued",
    )
    insert_assignment(
        assignment_id="as_reported_overdue",
        start_at=past_end - timedelta(minutes=15),
        end_at=past_end,
        state="reported",
    )

    expired = expire_overdue_assignments(rollback, STATION_ID)

    assert expired == 1
    with rollback.cursor() as cur:
        cur.execute(
            "select assignment_id, state from assignments order by assignment_id"
        )
        rows = dict(cur.fetchall())
    assert rows["as_overdue"] == "expired"
    assert rows["as_current"] == "issued"
    assert rows["as_reported_overdue"] == "reported"


def test_find_due_assignments_respects_the_window_and_carries_joined_fields(
    rollback: Any, insert_assignment: InsertAssignment
) -> None:
    now = datetime.now(UTC)
    insert_assignment(
        assignment_id="as_due",
        start_at=now + timedelta(minutes=5),
        end_at=now + timedelta(minutes=15),
    )
    insert_assignment(
        assignment_id="as_past",
        start_at=now - timedelta(hours=2),
        end_at=now - timedelta(hours=1),
    )
    insert_assignment(
        assignment_id="as_future",
        start_at=now + timedelta(hours=5),
        end_at=now + timedelta(hours=5, minutes=10),
    )

    due = find_due_assignments(
        rollback, STATION_ID, horizon_end=now + timedelta(hours=2)
    )

    assert [d.assignment_id for d in due] == ["as_due"]
    result = due[0]
    assert result.satellite_id == SATELLITE_ID
    assert result.expected_max_elevation_deg == 61.4
    assert result.element_set_line1 == LINE1
    assert result.element_set_line2 == LINE2
    # CLAUDE.md rule 5 — the column exists on `assignments`, so the query that
    # feeds MSP §4.3 has to carry it or the message cannot mark itself (D-048).
    assert result.simulated is False


def test_find_due_assignments_carries_a_simulated_assignment(
    rollback: Any, insert_assignment: InsertAssignment
) -> None:
    now = datetime.now(UTC)
    insert_assignment(
        assignment_id="as_simulated",
        start_at=now + timedelta(minutes=5),
        end_at=now + timedelta(minutes=15),
    )
    with rollback.cursor() as cur:
        cur.execute(
            "update assignments set simulated = true where assignment_id = %s",
            ("as_simulated",),
        )

    due = find_due_assignments(
        rollback, STATION_ID, horizon_end=now + timedelta(hours=2)
    )

    assert [d.simulated for d in due] == [True]


def test_find_due_assignments_returns_more_than_eight_when_eligible(
    rollback: Any, insert_assignment: InsertAssignment
) -> None:
    """D-035: the query itself does not cap — proving the caller does is
    the next session's job, once a caller exists."""
    now = datetime.now(UTC)
    for i in range(9):
        insert_assignment(
            assignment_id=f"as_many_{i}",
            start_at=now + timedelta(minutes=i),
            end_at=now + timedelta(minutes=i + 10),
        )

    due = find_due_assignments(
        rollback, STATION_ID, horizon_end=now + timedelta(hours=2)
    )

    assert len(due) == 9
