"""The store's reads of the two operators' views, against real rows.

The views' own arithmetic is tested in ``test_migrations.py``. This checks what
``meridian schedule runs`` and ``meridian passes timing`` are handed: the
columns come back typed, newest first, and filtered as asked.

Marked ``integration`` by the directory hook.

Reference: docs/DECISIONS.md D-177.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytest.importorskip("psycopg")

from meridian.store.operator_views import (
    find_recent_runs,
    find_timing_errors,
)

pytestmark = pytest.mark.integration

AOS = datetime(2026, 9, 14, 6, 0, tzinfo=UTC)


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def reported(rollback: Any, schedule_rows: Any) -> str:
    """One station with two decoded passes, a clock report, and one run."""
    station = schedule_rows.station("st_views", simulated=False)
    element_set = schedule_rows.satellite()
    rollback.execute(
        "insert into schedule_runs (run_id, decided_at, horizon_start, horizon_end,"
        " model_config, config_sha256, parameters, yield_source, solver,"
        " solver_version, status, objective, time_limit_s, runtime_s, stations,"
        " candidates, scheduled, skipped)"
        " values ('sr_views', %s, %s, %s, 'A', %s, '{}'::jsonb, 'elevation_proxy',"
        " 'highs', '1.0', 'optimal', 1.0, 10, 0.1, 1, 2, 2, 0)",
        (AOS - timedelta(hours=1), AOS, AOS + timedelta(hours=6), bytes(32)),
    )
    for index, assignment_id in enumerate(("as_views_1", "as_views_2")):
        aos = AOS + timedelta(hours=index)
        schedule_rows.assignment(
            assignment_id, schedule_rows.pass_(station, aos, element_set_id=element_set)
        )
        schedule_rows.observation(assignment_id, outcome="decoded")
    rollback.execute(
        "update assignments set schedule_run_id = 'sr_views'"
        " where assignment_id in ('as_views_1', 'as_views_2')"
    )
    rollback.execute(
        "insert into heartbeats (station_id, sent_at, received_at, state,"
        " clock_offset_s, clock_uncertainty_s)"
        " values (%s, %s, %s, 'idle', -1.5, 0.2)",
        (station, AOS + timedelta(minutes=1), AOS + timedelta(minutes=1)),
    )
    return station


def test_timing_errors_come_back_newest_first_and_corrected(
    rollback: Any, reported: str
) -> None:
    errors = find_timing_errors(rollback, station_id=reported, limit=10)

    assert [one.assignment_id for one in errors] == ["as_views_2", "as_views_1"]
    first = errors[1]
    assert first.timing_error_s == pytest.approx(first.uncorrected_error_s - 1.5)
    assert first.excluded is None
    assert errors[0].excluded == "clock_offset_unknown"


@pytest.mark.usefixtures("reported")
def test_timing_errors_are_filtered_by_station(rollback: Any) -> None:
    assert find_timing_errors(rollback, station_id="st_other", limit=10) == []
    assert len(find_timing_errors(rollback, station_id=None, limit=1)) == 1


@pytest.mark.usefixtures("reported")
def test_a_run_is_read_with_what_came_of_it(rollback: Any) -> None:
    runs = [
        one for one in find_recent_runs(rollback, limit=50) if one.run_id == "sr_views"
    ]

    (run,) = runs
    assert (run.scheduled, run.decoded, run.outstanding) == (2, 2, 0)
    assert run.fell_back is False
    assert run.simulated is False
