"""``meridian.store.passes`` against real TimescaleDB.

Marked ``integration``. The behaviour under test is mostly the identity rule
D-063 settles — which repeat computations collapse to one row and which are two
genuinely different predictions — so most of these tests are about what the
unique constraint does rather than about the SQL that reads rows back.

Uses the ``rollback`` fixture pattern established in ``test_store_invites.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.store.passes import (  # noqa: E402
    NewPass,
    find_passes_in_horizon,
    insert_pass,
)

pytestmark = pytest.mark.integration

SATELLITE_ID = "norad:40069"
STATION_ID = "st_passes"
OTHER_STATION_ID = "st_other"


def a_credential_hash(seed: int) -> bytes:
    """A distinct 32-byte hash per station.

    ``stations.token_sha256`` is unique, so two stations cannot share one — a
    shared placeholder makes the second insert fail on a constraint that has
    nothing to do with what these tests are about.
    """
    return bytes([seed]) * 32


LINE1 = "1 40069U 14037A   26226.09270833  .00000123  00000-0  76543-4 0  9991"
LINE2 = "2 40069  98.6021 213.4109 0005678  12.3456 347.7890 14.20800000123456"

AOS = datetime(2026, 8, 14, 9, 41, 20, tzinfo=UTC)
LOS = datetime(2026, 8, 14, 9, 52, 7, tzinfo=UTC)
CULMINATION = datetime(2026, 8, 14, 9, 46, 40, tzinfo=UTC)


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes — see test_store_invites.py's twin."""
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def element_set_ids(rollback: Any) -> list[int]:
    """One satellite, two stations, and two element sets for that satellite.

    Two sets rather than one because the identity rule turns on them: the same
    rise predicted from two different sets is two rows, and nothing else in this
    file can show that.
    """
    with rollback.cursor() as cur:
        cur.execute(
            "insert into satellites (satellite_id, name) values (%s, %s)",
            (SATELLITE_ID, "Test satellite"),
        )
        for seed, station_id in enumerate((STATION_ID, OTHER_STATION_ID), start=1):
            cur.execute(
                "insert into stations (station_id, name, operator, lat_deg,"
                " lon_deg, alt_m, token_sha256, registration_key_sha256)"
                " values (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    station_id,
                    "Test",
                    "tests",
                    0.0,
                    0.0,
                    0.0,
                    a_credential_hash(seed),
                    a_credential_hash(seed + 100),
                ),
            )
        ids = []
        for day, line2 in ((12, LINE2), (13, LINE2.replace("14.208", "14.209"))):
            cur.execute(
                "insert into element_sets (satellite_id, epoch, line1, line2, source)"
                " values (%s, %s, %s, %s, %s) returning id",
                (
                    SATELLITE_ID,
                    datetime(2026, 8, day, 2, 11, 0, tzinfo=UTC),
                    LINE1,
                    line2,
                    "manual",
                ),
            )
            (element_set_id,) = cur.fetchone()
            ids.append(element_set_id)
    return ids


def a_pass(
    element_set_id: int,
    *,
    station_id: str = STATION_ID,
    aos: datetime = AOS,
    min_elevation_deg: float = 10.0,
    simulated: bool = False,
) -> NewPass:
    """One window, varying only the fields a test is actually about."""
    return NewPass(
        satellite_id=SATELLITE_ID,
        station_id=station_id,
        aos=aos,
        los=aos + (LOS - AOS),
        max_elevation_deg=61.4,
        max_elevation_at=aos + (CULMINATION - AOS),
        aos_azimuth_deg=10.0,
        los_azimuth_deg=200.0,
        element_set_id=element_set_id,
        min_elevation_deg=min_elevation_deg,
        simulated=simulated,
    )


def test_a_computed_pass_round_trips(rollback: Any, element_set_ids: list[int]) -> None:
    """Every field survives, including the two that make a row interpretable."""
    assert insert_pass(rollback, a_pass(element_set_ids[0])) is True

    (stored,) = find_passes_in_horizon(rollback, STATION_ID, AOS, AOS + timedelta(1))

    assert stored.aos == AOS
    assert stored.los == LOS
    assert stored.max_elevation_at == CULMINATION
    assert stored.max_elevation_deg == 61.4
    assert stored.element_set_id == element_set_ids[0]
    assert stored.min_elevation_deg == 10.0


def test_the_same_prediction_twice_is_one_row(
    rollback: Any, element_set_ids: list[int]
) -> None:
    """What makes the generation job safe to re-run, and safe on a timer.

    The second call reports False rather than raising, so a job can count what
    it actually added without treating a repeat as a failure.
    """
    assert insert_pass(rollback, a_pass(element_set_ids[0])) is True
    assert insert_pass(rollback, a_pass(element_set_ids[0])) is False

    assert (
        len(find_passes_in_horizon(rollback, STATION_ID, AOS, AOS + timedelta(1))) == 1
    )


def test_a_newer_element_set_predicting_the_same_rise_is_a_second_row(
    rollback: Any, element_set_ids: list[int]
) -> None:
    """The distinction D-063 turns on, and the reason this is not deduplication.

    One physical pass, predicted from two element sets. Both rows are kept
    because the difference between them is the measurement that makes
    element-set age a usable feature — collapsing them would discard exactly the
    quantity the archive exists to preserve.
    """
    assert insert_pass(rollback, a_pass(element_set_ids[0])) is True
    assert insert_pass(rollback, a_pass(element_set_ids[1])) is True

    stored = find_passes_in_horizon(rollback, STATION_ID, AOS, AOS + timedelta(1))
    assert {row.element_set_id for row in stored} == set(element_set_ids)


def test_the_same_rise_at_a_different_floor_is_a_second_row(
    rollback: Any, element_set_ids: list[int]
) -> None:
    """A 10-degree search and a 0-degree search describe different populations.

    Pooling them would let a completeness ratio compare an opportunity count
    computed against one floor with observations gathered under another.
    """
    assert insert_pass(rollback, a_pass(element_set_ids[0])) is True
    assert (
        insert_pass(rollback, a_pass(element_set_ids[0], min_elevation_deg=0.0)) is True
    )

    stored = find_passes_in_horizon(rollback, STATION_ID, AOS, AOS + timedelta(1))
    assert sorted(row.min_elevation_deg for row in stored) == [0.0, 10.0]


def test_the_horizon_is_half_open_on_acquisition(
    rollback: Any, element_set_ids: list[int]
) -> None:
    """A pass rising exactly at ``end`` belongs to the next horizon, not this one.

    The same rule ``pass_windows`` uses to decide which search owns a pass
    (D-059). Two consecutive scheduler runs must consider each pass once.
    """
    later = AOS + timedelta(hours=2)
    insert_pass(rollback, a_pass(element_set_ids[0]))
    insert_pass(rollback, a_pass(element_set_ids[0], aos=later))

    found = find_passes_in_horizon(rollback, STATION_ID, AOS, later)

    assert [row.aos for row in found] == [AOS]


def test_passes_come_back_in_acquisition_order(
    rollback: Any, element_set_ids: list[int]
) -> None:
    """A scheduler walks them in time order; the query guarantees it, not luck."""
    offsets = (timedelta(hours=4), timedelta(0), timedelta(hours=2))
    for offset in offsets:
        insert_pass(rollback, a_pass(element_set_ids[0], aos=AOS + offset))

    found = find_passes_in_horizon(rollback, STATION_ID, AOS, AOS + timedelta(1))

    assert [row.aos for row in found] == [AOS + o for o in sorted(offsets)]


def test_another_stations_passes_are_not_returned(
    rollback: Any, element_set_ids: list[int]
) -> None:
    """Geometry is per station: the same satellite rises at different times."""
    insert_pass(rollback, a_pass(element_set_ids[0]))
    insert_pass(rollback, a_pass(element_set_ids[0], station_id=OTHER_STATION_ID))

    found = find_passes_in_horizon(rollback, STATION_ID, AOS, AOS + timedelta(1))

    assert [row.station_id for row in found] == [STATION_ID]


def test_simulated_provenance_survives_the_round_trip(
    rollback: Any, element_set_ids: list[int]
) -> None:
    """CLAUDE.md rule 5: losing this is a correctness bug, not a style issue.

    A pass computed for a simulated station is simulated data, and it has to
    still say so when the scheduler reads it back and stamps an assignment.
    """
    insert_pass(rollback, a_pass(element_set_ids[0], simulated=True))

    (stored,) = find_passes_in_horizon(rollback, STATION_ID, AOS, AOS + timedelta(1))

    assert stored.simulated is True
