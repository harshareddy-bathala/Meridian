"""``meridian.datasets.archive_passes`` — D-150's denominator, without a database.

Most cases hand in a recording orbit service, so what is asked for — which
station, which days, which element set — is checked exactly. One case
propagates for real, with the element set and site that
``test_pass_windows_reference.py`` transcribes, so the rows are known to be
what Skyfield finds rather than what the fake returns.

Reference: docs/DECISIONS.md D-138, D-150.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from meridian.datasets.archive_passes import (
    ArchivePasses,
    ArchiveRows,
    compute_archive_passes,
)
from meridian.orbit.skyfield_service import SkyfieldOrbitService
from meridian.orbit.types import PassSearch, PassWindow

ISS_LINE1 = "1 25544U 98067A   14020.93268519  .00009878  00000-0  18200-3 0  5082"
ISS_LINE2 = "2 25544  51.6498 109.4756 0003572  55.9686 274.8005 15.49815350868473"
ISS = "norad:25544"
ISS_EPOCH = datetime(2014, 1, 20, 22, 23, 4, tzinfo=UTC)

SINCE = datetime(2014, 1, 22, 6, 0, tzinfo=UTC)
AS_OF = datetime(2014, 1, 25, 18, 0, tzinfo=UTC)
DAY = timedelta(days=1)
MIDNIGHT_23 = datetime(2014, 1, 23, tzinfo=UTC)
LEAD = timedelta(hours=1)
"""A station's first search starts this early, so a pass in progress is found."""


@dataclass
class RecordingOrbit:
    """Answers every search with one pass an hour into it, and keeps the search."""

    searches: list[PassSearch] = field(default_factory=list)

    def pass_windows(self, search: PassSearch) -> list[PassWindow]:
        self.searches.append(search)
        aos = search.start + timedelta(hours=1)
        return [
            PassWindow(
                satellite_id=search.element_set.satellite_id,
                aos=aos,
                los=aos + timedelta(minutes=10),
                max_elevation_deg=42.0,
                max_elevation_at=aos + timedelta(minutes=5),
                aos_azimuth_deg=10.0,
                los_azimuth_deg=190.0,
                element_set_epoch=search.element_set.epoch,
                element_set_age_s=search.element_set.age_s(aos),
                min_elevation_deg=search.min_elevation_deg,
            )
        ]


def station(archive_station_id: int = 1, **fields: object) -> Mapping[str, object]:
    return {
        "archive_station_id": archive_station_id,
        "lat_deg": 40.8939,
        "lon_deg": -83.8917,
        "alt_m": 230.0,
    } | fields


def reception(
    started_at: datetime,
    *,
    archive_station_id: int = 1,
    key: str = ISS,
    kind: str = "norad",
) -> Mapping[str, object]:
    return {
        "archive_station_id": archive_station_id,
        "satellite_key": key,
        "satellite_key_kind": kind,
        "started_at": started_at,
    }


def element_set(
    element_set_id: int = 1,
    *,
    epoch: datetime = ISS_EPOCH,
    retrieved_at: datetime | None = None,
    satellite_id: str = ISS,
) -> Mapping[str, object]:
    return {
        "id": element_set_id,
        "satellite_id": satellite_id,
        "epoch": epoch,
        "retrieved_at": retrieved_at or epoch + timedelta(hours=1),
        "line1": ISS_LINE1,
        "line2": ISS_LINE2,
        "source": "celestrak",
    }


def compute(
    stations: list[Mapping[str, object]],
    receptions: list[Mapping[str, object]],
    element_sets: list[Mapping[str, object]] | None = None,
    orbit: RecordingOrbit | None = None,
) -> tuple[RecordingOrbit, ArchivePasses]:
    recording = orbit or RecordingOrbit()
    result = compute_archive_passes(
        ArchiveRows(
            stations=stations,
            receptions=receptions,
            element_sets=[element_set()] if element_sets is None else element_sets,
        ),
        since=SINCE,
        as_of=AS_OF,
        orbit=recording,
    )
    return recording, result


# --- which days, which satellites ----------------------------------------------


def test_a_station_is_propagated_every_day_from_its_first_reception_to_its_last() -> (
    None
):
    orbit, _ = compute(
        [station()],
        [reception(MIDNIGHT_23 + timedelta(hours=3)), reception(MIDNIGHT_23 + 2 * DAY)],
    )

    assert [one.start for one in orbit.searches] == [
        MIDNIGHT_23 - LEAD,
        MIDNIGHT_23 + DAY,
        MIDNIGHT_23 + 2 * DAY,
    ]
    assert [one.end for one in orbit.searches] == [
        MIDNIGHT_23 + DAY,
        MIDNIGHT_23 + 2 * DAY,
        AS_OF,
    ]
    assert all(one.min_elevation_deg == 0.0 for one in orbit.searches)


def test_the_first_and_last_days_are_clipped_to_the_scope() -> None:
    """Clipped, less the lead: a pass rising just before ``since`` can still be
    what a reception just after it belongs to."""
    orbit, _ = compute(
        [station()],
        [reception(SINCE + timedelta(hours=1)), reception(AS_OF - timedelta(hours=1))],
    )

    assert orbit.searches[0].start == SINCE - LEAD
    assert orbit.searches[-1].end == AS_OF


def test_only_the_satellites_a_station_received_are_propagated_for_it() -> None:
    other = "norad:40069"
    orbit, _ = compute(
        [station(1), station(2)],
        [
            reception(MIDNIGHT_23, archive_station_id=1),
            reception(MIDNIGHT_23, archive_station_id=2, key=other),
        ],
        [element_set(1), element_set(2, satellite_id=other)],
    )

    asked = {(one.site.alt_m, one.element_set.satellite_id) for one in orbit.searches}
    assert asked == {(230.0, ISS), (230.0, other)}
    assert len(orbit.searches) == 2


def test_the_element_set_is_the_one_current_at_the_start_of_each_day() -> None:
    newer = MIDNIGHT_23 + timedelta(hours=6)
    orbit, result = compute(
        [station()],
        [reception(MIDNIGHT_23), reception(MIDNIGHT_23 + DAY)],
        [element_set(1), element_set(2, epoch=newer)],
    )

    assert [one.element_set.epoch for one in orbit.searches] == [ISS_EPOCH, newer]
    assert [row["element_set_id"] for row in result.rows] == [1, 2]


def test_two_sets_with_one_epoch_resolve_to_the_later_retrieval() -> None:
    """``find_element_set_current_at``'s order, so both paths pick the same set."""
    orbit, result = compute(
        [station()],
        [reception(MIDNIGHT_23)],
        [
            element_set(7, retrieved_at=ISS_EPOCH + timedelta(hours=9)),
            element_set(3, retrieved_at=ISS_EPOCH + timedelta(hours=2)),
        ],
    )

    assert [row["element_set_id"] for row in result.rows] == [7]
    assert len(orbit.searches) == 1


# --- what cannot be computed is counted ------------------------------------------


def test_a_station_with_no_location_is_counted_and_not_propagated() -> None:
    orbit, result = compute(
        [station(lat_deg=None, lon_deg=None)], [reception(MIDNIGHT_23)]
    )

    assert orbit.searches == []
    assert result.rows == ()
    assert result.counts["archive_denominator.stations_without_location"] == 1


def test_a_station_with_no_description_at_all_has_no_location() -> None:
    _, result = compute([], [reception(MIDNIGHT_23)])

    assert result.counts["archive_denominator.stations_without_location"] == 1


def test_a_station_with_no_altitude_is_propagated_at_sea_level_and_counted() -> None:
    orbit, result = compute([station(alt_m=None)], [reception(MIDNIGHT_23)])

    assert orbit.searches[0].site.alt_m == 0.0
    assert result.counts["archive_denominator.stations_without_altitude"] == 1


def test_a_satellite_not_keyed_by_norad_number_is_counted() -> None:
    orbit, result = compute(
        [station()], [reception(MIDNIGHT_23, key="LUME-1", kind="source_name")]
    )

    assert orbit.searches == []
    assert result.counts["archive_denominator.satellites_not_norad"] == 1


def test_a_satellite_with_no_element_set_is_counted() -> None:
    _, result = compute(
        [station()], [reception(MIDNIGHT_23, key="norad:99999")], element_sets=[]
    )

    assert result.counts["archive_denominator.satellites_without_element_sets"] == 1


def test_a_day_before_the_first_epoch_is_counted() -> None:
    orbit, result = compute(
        [station()],
        [reception(MIDNIGHT_23), reception(MIDNIGHT_23 + DAY)],
        [element_set(epoch=MIDNIGHT_23 + timedelta(hours=1))],
    )

    assert len(orbit.searches) == 1
    assert result.counts["archive_denominator.satellite_days_without_element_set"] == 1


def test_every_count_is_present_when_nothing_went_wrong() -> None:
    _, result = compute([station()], [reception(MIDNIGHT_23)])

    assert set(result.counts.values()) == {0}
    assert len(result.counts) == 5


# --- the rows -----------------------------------------------------------------


def test_rows_are_in_station_satellite_and_time_order() -> None:
    """Numeric station order: 9 before 10, which text order would reverse."""
    _, result = compute(
        [station(10), station(9)],
        [
            reception(MIDNIGHT_23 + DAY, archive_station_id=10),
            reception(MIDNIGHT_23, archive_station_id=10),
            reception(MIDNIGHT_23, archive_station_id=9),
        ],
    )

    assert [(row["archive_station_id"], row["aos"]) for row in result.rows] == [
        (9, MIDNIGHT_23),
        (10, MIDNIGHT_23),
        (10, MIDNIGHT_23 + DAY + timedelta(hours=1)),
    ]


def test_each_row_carries_the_geometry_and_the_set_it_came_from() -> None:
    _, result = compute([station()], [reception(MIDNIGHT_23)])

    (row,) = result.rows
    assert row == {
        "archive_station_id": 1,
        "satellite_id": ISS,
        "aos": MIDNIGHT_23,
        "los": MIDNIGHT_23 + timedelta(minutes=10),
        "max_elevation_deg": 42.0,
        "max_elevation_at": MIDNIGHT_23 + timedelta(minutes=5),
        "aos_azimuth_deg": 10.0,
        "los_azimuth_deg": 190.0,
        "element_set_id": 1,
        "min_elevation_deg": 0.0,
    }


def test_real_propagation_finds_the_day_s_passes() -> None:
    """Skyfield, not the fake: every pass rises inside the day and above 0°."""
    result = compute_archive_passes(
        ArchiveRows(
            stations=[station(alt_m=0.0)],
            receptions=[reception(MIDNIGHT_23 + timedelta(hours=5))],
            element_sets=[element_set()],
        ),
        since=SINCE,
        as_of=AS_OF,
        orbit=SkyfieldOrbitService(),
    )

    aos = [row["aos"] for row in result.rows if isinstance(row["aos"], datetime)]
    assert len(aos) >= 4
    assert all(MIDNIGHT_23 <= one < MIDNIGHT_23 + DAY for one in aos)
    assert aos == sorted(aos)
