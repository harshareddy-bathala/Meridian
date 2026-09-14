"""The scrape-time reads, against a real schema.

Counts are asserted as **differences** before and after a test inserts its rows:
the database is shared with every other integration test, and a module that
committed a station earlier in the run must not change what these assert.
Everything written here is rolled back.

Marked ``integration`` by the directory hook in ``tests/conftest.py``.

Reference: docs/DECISIONS.md D-054, D-109, D-111.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.store.monitoring import read_monitoring_snapshot  # noqa: E402
from meridian.store.schema_revision import (  # noqa: E402
    find_current_revision,
    find_head_revision,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes."""
    with conn.transaction(force_rollback=True):
        yield conn


def snapshot(conn: Any, now: datetime) -> Any:
    """The snapshot a scrape at ``now`` would read."""
    return read_monitoring_snapshot(
        conn, overdue_ended_before=now - timedelta(minutes=15)
    )


def test_deleted_stations_are_not_read(rollback: Any, schedule_rows: Any) -> None:
    """A station that was deleted is not part of the network any more."""
    before = len(snapshot(rollback, datetime.now(UTC)).stations)

    schedule_rows.station("st_collector_live", simulated=False)
    schedule_rows.station("st_collector_gone", simulated=False, deleted=True)

    assert len(snapshot(rollback, datetime.now(UTC)).stations) == before + 1


def test_assignments_are_counted_by_state_and_skipped_ones_are_not(
    rollback: Any, schedule_rows: Any
) -> None:
    """A skipped decision is a row, but no work was issued for it."""
    now = datetime.now(UTC)
    before = snapshot(rollback, now).assignments
    station = schedule_rows.station("st_collector_states", simulated=True)
    element_set = schedule_rows.satellite()
    pass_id = schedule_rows.pass_(station, now, element_set_id=element_set)

    schedule_rows.assignment("as_col_held", pass_id, state="held")
    schedule_rows.assignment(
        "as_col_skip", pass_id, decision="skipped", model_config="B"
    )

    after = snapshot(rollback, now).assignments
    assert after[("held", True)] == before[("held", True)] + 1
    assert after[("issued", True)] == before[("issued", True)]


def test_only_closed_unreported_held_or_started_work_is_overdue(
    rollback: Any, schedule_rows: Any
) -> None:
    """Closed over fifteen minutes ago, taken, and with no observation."""
    now = datetime.now(UTC)
    before = snapshot(rollback, now).overdue[False]
    station = schedule_rows.station("st_collector_overdue", simulated=False)
    element_set = schedule_rows.satellite()
    long_ago = now - timedelta(hours=2)
    just_now = now - timedelta(minutes=16)

    overdue = schedule_rows.pass_(station, long_ago, element_set_id=element_set)
    schedule_rows.assignment("as_col_overdue", overdue, state="in_progress")
    reported = schedule_rows.pass_(
        station, long_ago + timedelta(minutes=20), element_set_id=element_set
    )
    schedule_rows.assignment("as_col_reported", reported, state="held")
    schedule_rows.observation("as_col_reported")
    recent = schedule_rows.pass_(station, just_now, element_set_id=element_set)
    schedule_rows.assignment("as_col_recent", recent, state="held")
    expired = schedule_rows.pass_(
        station, long_ago + timedelta(minutes=40), element_set_id=element_set
    )
    schedule_rows.assignment("as_col_expired", expired, state="expired")

    assert snapshot(rollback, now).overdue[False] == before + 1


def test_the_migrated_database_is_at_the_head_the_scripts_define(
    rollback: Any,
) -> None:
    """The test database was migrated to head, so the two revisions agree."""
    head = find_head_revision()

    assert head is not None
    assert find_current_revision(rollback) == head
