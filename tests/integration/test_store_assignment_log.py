"""``meridian.store.assignment_log`` against real TimescaleDB.

Pins that skipped decisions are listed beside scheduled ones, the two filters,
paging through a tie on ``start_at``, and that the detail read serves history the
list has already dropped.

Marked ``integration`` by the directory hook in ``tests/conftest.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.store.assignment_log import (  # noqa: E402
    find_assignment,
    find_assignments,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 14, 6, 0, tzinfo=UTC)


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def decisions(schedule_rows: Any) -> None:
    """Two stations sharing one window, one scheduled and one skipped, plus history."""
    rows = schedule_rows
    rows.station("st_l_a")
    rows.station("st_l_b")
    elements = rows.satellite()
    soon = NOW + timedelta(minutes=10)
    rows.assignment("as_l_2", rows.pass_("st_l_a", soon, element_set_id=elements))
    rows.assignment(
        "as_l_1",
        rows.pass_("st_l_b", soon, element_set_id=elements),
        decision="skipped",
        reason="overlaps a higher-scoring pass",
        conflicts_with_assignment_id="as_l_2",
        state="expired",
    )
    rows.assignment(
        "as_l_old",
        rows.pass_("st_l_a", NOW - timedelta(hours=3), element_set_id=elements),
    )


def _list(rollback: Any, **overrides: Any) -> list[str]:
    arguments = {
        "not_before": NOW,
        "station_id": None,
        "decision": None,
        "after_assignment_id": None,
        "limit": 10,
    } | overrides
    return [row.assignment_id for row in find_assignments(rollback, **arguments)]


@pytest.mark.usefixtures("decisions")
def test_upcoming_decisions_include_skips_and_break_ties_by_id(rollback: Any) -> None:
    assert _list(rollback) == ["as_l_1", "as_l_2"]


@pytest.mark.usefixtures("decisions")
def test_the_filters_narrow_by_station_and_by_decision(rollback: Any) -> None:
    assert _list(rollback, station_id="st_l_a") == ["as_l_2"]
    assert _list(rollback, decision="skipped") == ["as_l_1"]


@pytest.mark.usefixtures("decisions")
def test_paging_resumes_after_the_named_assignment(rollback: Any) -> None:
    assert _list(rollback, after_assignment_id="as_l_1") == ["as_l_2"]


@pytest.mark.usefixtures("decisions")
def test_the_detail_read_carries_the_reason_and_serves_history(rollback: Any) -> None:
    skipped = find_assignment(rollback, "as_l_1")
    old = find_assignment(rollback, "as_l_old")

    assert skipped is not None
    assert skipped.reason == "overlaps a higher-scoring pass"
    assert skipped.conflicts_with_assignment_id == "as_l_2"
    assert skipped.satellite_id == "norad:99970"
    assert old is not None
    assert find_assignment(rollback, "as_nothing") is None
