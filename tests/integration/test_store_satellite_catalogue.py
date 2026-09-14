"""``meridian.store.satellite_catalogue`` against real TimescaleDB.

The reads behind the public satellite list, detail and transmitters. What is
asserted here is mostly about *inclusion* — which is the opposite of the station
directory's twin, and deliberately so. This read exists because a satellite that
went quiet and a transmitter that was switched off are the interesting rows, and
the scheduler's read drops exactly those.

Marked ``integration`` by the directory hook in ``tests/conftest.py``. Every test
runs inside a transaction that is always rolled back.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.store.element_sets import NewElementSet, insert_element_set  # noqa: E402
from meridian.store.satellite_catalogue import (  # noqa: E402
    find_satellite,
    find_satellites_after,
    find_transmitters_for_satellite,
)
from meridian.store.satellites import (  # noqa: E402
    NewSatellite,
    NewTransmitter,
    find_active_transmitters,
    insert_satellite,
    insert_transmitter,
)

pytestmark = pytest.mark.integration

SAMPLE_LINE_1 = "1 25544U 98067A   26225.50000000  .00016717  00000-0  10270-3 0  9004"
SAMPLE_LINE_2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.72125391563537"


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes — see test_store_stations.py's twin."""
    with conn.transaction(force_rollback=True):
        yield conn


def add_satellite(rollback: Any, satellite_id: str, **columns: Any) -> None:
    """Insert one satellite, then set columns the insertable form withholds.

    ``NewSatellite`` carries no ``active``, ``priority`` or ``deleted_at``: each
    is an operator action rather than a property of the object being entered. The
    tests below need all three, so they are written directly.
    """
    insert_satellite(rollback, NewSatellite(satellite_id=satellite_id, name="Test sat"))
    for column, value in columns.items():
        with rollback.cursor() as cur:
            cur.execute(
                f"update satellites set {column} = %s where satellite_id = %s",
                (value, satellite_id),
            )


def add_transmitter(
    rollback: Any, satellite_id: str, freq_hz: int, **columns: Any
) -> None:
    """Insert one downlink, then set columns the insertable form withholds."""
    insert_transmitter(
        rollback,
        NewTransmitter(
            satellite_id=satellite_id,
            centre_freq_hz=freq_hz,
            mode="lrpt",
            polarisation="rhcp",
            bandwidth_hz=120_000,
        ),
    )
    for column, value in columns.items():
        with rollback.cursor() as cur:
            cur.execute(
                f"update satellite_transmitters set {column} = %s "
                "where satellite_id = %s and centre_freq_hz = %s",
                (value, satellite_id, freq_hz),
            )


def line1_for_epoch(epoch: datetime) -> str:
    """A first line whose epoch field, TLE columns 19-32, matches ``epoch``.

    Written out rather than reusing one constant line because **an element set is
    identified by its contents**, not by the epoch passed alongside them: the key
    is ``(satellite_id, source, content_sha256)`` and the hash is generated from
    the lines (D-057). Two archive calls with identical lines write one row, and
    a helper that ignored that would make every assertion about the newest epoch
    below pass against a single stored set.
    """
    year_in_century = epoch.year % 100
    day_of_year = epoch.timetuple().tm_yday
    epoch_field = f"{year_in_century:02d}{day_of_year:03d}.50000000"
    return SAMPLE_LINE_1[:18] + epoch_field + SAMPLE_LINE_1[32:]


def add_element_set(rollback: Any, satellite_id: str, epoch: datetime) -> None:
    """Archive one element set at a given epoch."""
    insert_element_set(
        rollback,
        NewElementSet(
            satellite_id=satellite_id,
            epoch=epoch,
            line1=line1_for_epoch(epoch),
            line2=SAMPLE_LINE_2,
            source="manual",
        ),
    )


def test_a_catalogued_satellite_carries_its_weighting(rollback: Any) -> None:
    """Priority is half the answer to why one pass was chosen over another."""
    add_satellite(rollback, "norad:25544", priority=2.5)

    satellite = find_satellite(rollback, "norad:25544")

    assert satellite is not None
    assert satellite.priority == 2.5
    assert satellite.orbital_regime == "leo"
    assert satellite.is_active is True


def test_a_silent_satellite_is_listed_and_labelled(rollback: Any) -> None:
    """The whole reason this read is not `find_active_transmitters`.

    A decommissioned payload is still tracked and still has a history. Excluding
    it here would make a reader read its silence as a prediction failure.
    """
    add_satellite(rollback, "norad:00900", active=False)
    add_transmitter(rollback, "norad:00900", 137_000_000)

    satellite = find_satellite(rollback, "norad:00900")

    assert satellite is not None
    assert satellite.is_active is False
    assert find_active_transmitters(rollback) == []


def test_a_withdrawn_satellite_is_not_listed(rollback: Any) -> None:
    """Soft-deleted means we stopped tracking it, not that it went quiet."""
    add_satellite(rollback, "norad:00901", deleted_at=datetime.now(UTC))

    assert find_satellite(rollback, "norad:00901") is None
    listed = [s.satellite_id for s in find_satellites_after(rollback, None, 100)]
    assert "norad:00901" not in listed


def test_the_newest_element_set_epoch_wins(rollback: Any) -> None:
    """Accuracy decays from the epoch, so the newest one is what an age means."""
    add_satellite(rollback, "norad:00902")
    older = datetime(2026, 8, 1, 12, tzinfo=UTC)
    newer = datetime(2026, 8, 12, 12, tzinfo=UTC)
    add_element_set(rollback, "norad:00902", older)
    add_element_set(rollback, "norad:00902", newer)

    satellite = find_satellite(rollback, "norad:00902")

    assert satellite is not None
    assert satellite.latest_element_set_epoch == newer


def test_a_satellite_with_no_element_set_reports_none(rollback: Any) -> None:
    """A tracked object we cannot propagate is a gap worth seeing."""
    add_satellite(rollback, "norad:00903")

    satellite = find_satellite(rollback, "norad:00903")

    assert satellite is not None
    assert satellite.latest_element_set_epoch is None


def test_one_satellites_element_sets_do_not_age_another(rollback: Any) -> None:
    """The subquery correlates on satellite_id, and this is what proves it.

    An uncorrelated `max(epoch)` over the whole archive returns the same instant
    for every row, which looks entirely plausible on a catalogue where one
    satellite has element sets and reads as fresh for every satellite that does
    not.
    """
    add_satellite(rollback, "norad:00904")
    add_satellite(rollback, "norad:00905")
    add_element_set(rollback, "norad:00904", datetime(2026, 8, 12, 12, tzinfo=UTC))

    with_sets = find_satellite(rollback, "norad:00904")
    without = find_satellite(rollback, "norad:00905")

    assert with_sets is not None and with_sets.latest_element_set_epoch is not None
    assert without is not None
    assert without.latest_element_set_epoch is None


def test_the_cursor_advances_without_repeating(rollback: Any) -> None:
    """Two pages of one; a repeated row looks like duplicate data, not a bug."""
    add_satellite(rollback, "norad:00906")
    add_satellite(rollback, "norad:00907")

    first = find_satellites_after(rollback, "norad:00905", 1)
    second = find_satellites_after(rollback, first[0].satellite_id, 1)

    assert first[0].satellite_id == "norad:00906"
    assert second[0].satellite_id == "norad:00907"


def test_an_unknown_satellite_is_none(rollback: Any) -> None:
    """Absence is the answer, not an exception."""
    assert find_satellite(rollback, "norad:99999") is None


def test_transmitters_come_back_in_frequency_order(rollback: Any) -> None:
    """A response whose field order depends on the planner is not reproducible."""
    add_satellite(rollback, "norad:00908")
    add_transmitter(rollback, "norad:00908", 137_900_000)
    add_transmitter(rollback, "norad:00908", 137_100_000)

    transmitters = find_transmitters_for_satellite(rollback, "norad:00908")

    assert [t.centre_freq_hz for t in transmitters] == [137_100_000, 137_900_000]


def test_a_switched_off_transmitter_is_returned_and_labelled(rollback: Any) -> None:
    """A satellite can be alive with one of its downlinks off."""
    add_satellite(rollback, "norad:00909")
    add_transmitter(rollback, "norad:00909", 137_100_000)
    add_transmitter(rollback, "norad:00909", 137_900_000, active=False)

    transmitters = find_transmitters_for_satellite(rollback, "norad:00909")

    assert [t.is_active for t in transmitters] == [True, False]
    live = [t.centre_freq_hz for t in find_active_transmitters(rollback)]
    assert 137_900_000 not in live


def test_a_withdrawn_transmitter_is_not_returned(rollback: Any) -> None:
    """Soft delete is the one exclusion both reads agree on."""
    add_satellite(rollback, "norad:00910")
    add_transmitter(rollback, "norad:00910", 137_100_000, deleted_at=datetime.now(UTC))

    assert find_transmitters_for_satellite(rollback, "norad:00910") == []


def test_an_unrecorded_polarisation_stays_unknown(rollback: Any) -> None:
    """`None` is a real record — not `linear`, and not an empty string."""
    add_satellite(rollback, "norad:00911")
    insert_transmitter(
        rollback,
        NewTransmitter(
            satellite_id="norad:00911", centre_freq_hz=137_100_000, mode="lrpt"
        ),
    )

    transmitters = find_transmitters_for_satellite(rollback, "norad:00911")

    assert transmitters[0].polarisation is None
    assert transmitters[0].bandwidth_hz is None
    assert transmitters[0].source == "manual"


def test_an_unknown_satellite_has_no_transmitters(rollback: Any) -> None:
    """Empty, not an error — so the caller checks the satellite exists itself."""
    assert find_transmitters_for_satellite(rollback, "norad:99999") == []
