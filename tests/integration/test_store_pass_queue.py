"""``meridian.store.pass_queue`` against real TimescaleDB.

The endpoint tests cover what a reader sees. These pin the read itself: which
passes count as upcoming, the order, the station filter, and that paging by pass
id resumes after the right row even when two passes rise in the same instant.

Marked ``integration`` by the directory hook in ``tests/conftest.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.store.pass_queue import find_upcoming_passes  # noqa: E402

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC).replace(microsecond=0)
"""The wall clock, not a fixed instant.

``schedule_rows.satellite()`` stamps its element set with the database's
``now() - interval '6 hours'``, so an instant pinned to one morning made the
epoch assertion below true until noon that day and false for ever after. Every
pass here is placed relative to this value, so nothing else depends on the date.
"""


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


def _ids(rows: list[Any]) -> list[int]:
    return [row.id for row in rows]


def test_the_queue_is_every_unfinished_pass_of_a_live_station_in_order(
    rollback: Any, schedule_rows: Any
) -> None:
    schedule_rows.station("st_s_a")
    schedule_rows.station("st_s_gone", deleted=True)
    elements = schedule_rows.satellite()
    later = schedule_rows.pass_(
        "st_s_a", NOW + timedelta(hours=1), element_set_id=elements
    )
    schedule_rows.pass_("st_s_a", NOW - timedelta(hours=1), element_set_id=elements)
    running = schedule_rows.pass_(
        "st_s_a", NOW - timedelta(minutes=5), element_set_id=elements
    )
    schedule_rows.pass_(
        "st_s_gone", NOW + timedelta(minutes=5), element_set_id=elements
    )

    rows = find_upcoming_passes(
        rollback, not_before=NOW, station_id=None, after_pass_id=None, limit=10
    )

    assert _ids(rows) == [running, later]
    assert rows[0].element_set_epoch < NOW


def test_paging_by_id_resumes_between_passes_that_rise_together(
    rollback: Any, schedule_rows: Any
) -> None:
    """A simulated fleet shares instants; the id breaks the tie."""
    for station in ("st_s_1", "st_s_2", "st_s_3"):
        schedule_rows.station(station)
    elements = schedule_rows.satellite()
    same = NOW + timedelta(minutes=10)
    ids = [
        schedule_rows.pass_(station, same, element_set_id=elements)
        for station in ("st_s_1", "st_s_2", "st_s_3")
    ]

    first = find_upcoming_passes(
        rollback, not_before=NOW, station_id=None, after_pass_id=None, limit=2
    )
    rest = find_upcoming_passes(
        rollback, not_before=NOW, station_id=None, after_pass_id=first[-1].id, limit=2
    )
    only_two = find_upcoming_passes(
        rollback, not_before=NOW, station_id="st_s_2", after_pass_id=None, limit=5
    )

    assert _ids(first) + _ids(rest) == sorted(ids)
    assert _ids(only_two) == [ids[1]]
