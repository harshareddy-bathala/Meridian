"""``observation_history`` and ``simulator_runs`` against real TimescaleDB.

Pins that only an observation's current revision is listed, newest first, with
paging through the cursor's assignment; and that a simulator run is its live
stations grouped.

Marked ``integration`` by the directory hook in ``tests/conftest.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.store.observation_history import find_recent_observations  # noqa: E402
from meridian.store.simulator_runs import find_simulator_runs  # noqa: E402

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 14, 6, 0, tzinfo=UTC)


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def history(schedule_rows: Any) -> None:
    """Three reported passes; the middle one corrected once."""
    rows = schedule_rows
    rows.station("st_h_a")
    rows.station("st_h_b", simulated=False)
    rows.station("st_h_gone", deleted=True)
    elements = rows.satellite()
    for index, (assignment_id, station) in enumerate(
        [("as_h_1", "st_h_a"), ("as_h_2", "st_h_b"), ("as_h_3", "st_h_a")]
    ):
        at = NOW - timedelta(hours=3 - index)
        rows.assignment(assignment_id, rows.pass_(station, at, element_set_id=elements))
        rows.observation(assignment_id, outcome="no_signal")
    rows.observation("as_h_2", revision=2, outcome="decoded")
    rows.assignment(
        "as_h_x",
        rows.pass_("st_h_gone", NOW - timedelta(hours=1), element_set_id=elements),
    )
    rows.observation("as_h_x")


def _ids(rows: list[Any]) -> list[str]:
    return [row.assignment_id for row in rows]


@pytest.mark.usefixtures("history")
def test_the_current_revision_of_each_observation_is_listed_newest_first(
    rollback: Any,
) -> None:
    rows = find_recent_observations(
        rollback, station_id=None, before_assignment_id=None, limit=10
    )

    assert _ids(rows) == ["as_h_3", "as_h_2", "as_h_1"]
    assert (rows[1].revision, rows[1].outcome) == (2, "decoded")


@pytest.mark.usefixtures("history")
def test_paging_and_the_station_filter(rollback: Any) -> None:
    after = find_recent_observations(
        rollback, station_id=None, before_assignment_id="as_h_3", limit=10
    )
    only_a = find_recent_observations(
        rollback, station_id="st_h_a", before_assignment_id=None, limit=10
    )

    assert _ids(after) == ["as_h_2", "as_h_1"]
    assert _ids(only_a) == ["as_h_3", "as_h_1"]


@pytest.mark.usefixtures("history")
def test_a_simulator_run_is_its_live_virtual_stations(rollback: Any) -> None:
    runs = [
        run
        for run in find_simulator_runs(rollback, after_run_id=None, limit=50)
        if run.run_id == "run-a"
    ]

    # st_h_a only: st_h_b is physical and st_h_gone is deleted.
    assert [run.station_count for run in runs] == [1]
