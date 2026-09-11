"""What a stored station becomes once it is published.

`DirectoryStation` is a plain dataclass, so every case here is a row written out
by hand — no database, no application. What is pinned is the pair of rules the
public surface cannot serve a station without applying: the coordinates are
coarsened to the operator's declared precision, and liveness is derived rather
than stored.

Marked as a unit test by living in ``tests/unit``: no network, no filesystem.

Reference: docs/DECISIONS.md D-082, D-054, D-085.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from meridian.api.public.models import Page, PublicStation, StationLiveness
from meridian.registry.liveness import Liveness
from meridian.store.station_directory import DirectoryStation

NOW = datetime(2026, 8, 14, 12, 0, 0, tzinfo=UTC)

# Six decimal places, so every coarsening below actually changes the value. A
# fixture already at two decimals would pass whatever the rounding did.
STORED_LAT_DEG = 12.971598
STORED_LON_DEG = 77.594562


def row(**overrides: object) -> DirectoryStation:
    """One stored station, with fields replaced as a test needs."""
    fields: dict[str, object] = {
        "station_id": "st_abc123",
        "name": "station-001",
        "operator": "meridian",
        "lat_deg": STORED_LAT_DEG,
        "lon_deg": STORED_LON_DEG,
        "alt_m": 920.4,
        "location_precision_decimals": 2,
        "simulated": False,
        "registered_at": NOW - timedelta(days=30),
        "last_heartbeat_at": NOW - timedelta(seconds=5),
    }
    fields.update(overrides)
    return DirectoryStation(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("decimals", "expected_lat_deg", "expected_lon_deg"),
    [(1, 13.0, 77.6), (2, 12.97, 77.59), (4, 12.9716, 77.5946)],
)
def test_a_published_station_carries_only_the_coarsened_coordinates(
    decimals: int, expected_lat_deg: float, expected_lon_deg: float
) -> None:
    """The whole point of D-082, asserted where a reader would see it.

    The stored value must be absent from the body, not merely accompanied by a
    rounded one — a response carrying both would publish the rooftop while
    appearing to protect it.
    """
    published = PublicStation.from_row(
        row(location_precision_decimals=decimals), now=NOW
    )

    assert published.location.lat_deg == expected_lat_deg
    assert published.location.lon_deg == expected_lon_deg
    assert STORED_LAT_DEG not in published.model_dump()["location"].values()
    assert str(STORED_LAT_DEG) not in published.model_dump_json()


def test_the_declared_precision_is_published_beside_the_position() -> None:
    """A map that does not say how coarse its pins are implies they are exact."""
    published = PublicStation.from_row(row(location_precision_decimals=1), now=NOW)

    assert published.location_precision_decimals == 1


def test_altitude_is_whole_metres_whatever_the_precision() -> None:
    """Decoupled from the field on purpose — sub-metre altitude is noise."""
    assert PublicStation.from_row(row(), now=NOW).location.alt_m == 920


@pytest.mark.parametrize(
    ("age_s", "expected"),
    [(5, "online"), (61, "stale"), (91, "offline"), (10_000, "offline")],
)
def test_liveness_is_derived_from_the_heartbeat_age(
    age_s: int, expected: Liveness
) -> None:
    """Derived on read, against the instant the caller passed in (D-054)."""
    station = row(last_heartbeat_at=NOW - timedelta(seconds=age_s))

    assert PublicStation.from_row(station, now=NOW).liveness == expected


def test_a_station_that_never_reported_is_never_seen_rather_than_offline() -> None:
    """The distinction the whole reliability model rests on.

    `never_seen` is a commissioning problem and `offline` is a fault. Collapsing
    them would make a station nobody finished setting up look like an outage.
    """
    assert PublicStation.from_row(row(last_heartbeat_at=None), now=NOW).liveness == (
        "never_seen"
    )


def test_every_station_states_whether_it_is_simulated() -> None:
    """CLAUDE.md rule 5, on the body a dashboard draws its map from."""
    assert PublicStation.from_row(row(simulated=True), now=NOW).simulated is True
    assert PublicStation.from_row(row(simulated=False), now=NOW).simulated is False


def test_the_small_liveness_body_still_states_its_provenance() -> None:
    """A liveness figure quoted on its own must not look measured when it is not."""
    status = StationLiveness.from_row(row(simulated=True), now=NOW)

    assert status.simulated is True
    assert status.liveness == "online"
    assert "lat_deg" not in status.model_dump()


def test_a_page_without_a_next_cursor_is_the_last_page() -> None:
    """Absent means done — the whole of the paging contract (D-085)."""
    page: Page[PublicStation] = Page(items=[PublicStation.from_row(row(), now=NOW)])

    assert page.next_cursor is None
    assert len(page.items) == 1


def test_two_stations_in_one_page_are_classified_against_one_instant() -> None:
    """Why `now` is a parameter rather than read inside the model.

    Both stations sit either side of the 60 s threshold relative to one reading.
    If each row read its own clock, a page built while the second elapsed would
    report two different answers for the same age.
    """
    fresh = PublicStation.from_row(
        row(last_heartbeat_at=NOW - timedelta(seconds=59)), now=NOW
    )
    stale = PublicStation.from_row(
        row(last_heartbeat_at=NOW - timedelta(seconds=61)), now=NOW
    )

    assert (fresh.liveness, stale.liveness) == ("online", "stale")
