"""``meridian.store.station_directory`` against real TimescaleDB.

The reads behind the public station list and station detail. What is asserted
here is mostly about *exclusion* — which stations a reader may not see, and which
columns never leave the database — because those are the failures that are
invisible until someone reads a response closely.

Marked ``integration`` by the directory hook in ``tests/conftest.py``. Every test
runs inside a transaction that is always rolled back.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import fields, replace
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.store.station_directory import (  # noqa: E402
    DirectoryStation,
    find_station,
    find_stations_after,
)
from meridian.store.stations import (  # noqa: E402
    Capability,
    NewStation,
    insert_station,
)

pytestmark = pytest.mark.integration

SAMPLE_CAPABILITY = Capability(
    band="vhf",
    freq_min_hz=136_000_000,
    freq_max_hz=138_000_000,
    modes=("lrpt",),
    polarisation="rhcp",
    tracking=True,
    min_elevation_deg=10.0,
)


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes — see test_store_stations.py's twin."""
    with conn.transaction(force_rollback=True):
        yield conn


def sample_station(station_id: str, **overrides: Any) -> NewStation:
    """A minimal, valid station, with fields replaced as a test needs."""
    base = NewStation(
        station_id=station_id,
        name="Test station",
        operator="tests",
        lat_deg=12.971598,
        lon_deg=77.594562,
        alt_m=920.0,
        token_sha256=bytes(32),
        registration_key_sha256=bytes(32),
        simulated=False,
        location_precision_decimals=2,
        simulator_run_id=None,
        seed=None,
        client_implementation="meridian-reference",
        client_version="0.1.0",
    )
    return replace(base, **overrides)


def add(rollback: Any, station_id: str, **overrides: Any) -> None:
    """Insert one station, with a token hash unique to its id.

    The hash has to differ per station: it is unique in the schema, so a second
    row carrying the default would fail the insert rather than the assertion.
    """
    unique_hash = station_id.encode().ljust(32, b"\0")[:32]
    insert_station(
        rollback,
        sample_station(station_id, token_sha256=unique_hash, **overrides),
        [SAMPLE_CAPABILITY],
    )


def test_the_directory_carries_no_credential_and_no_seed() -> None:
    """The projection is the security boundary, so it is asserted directly.

    `stations` holds two hashes, a registration key and a simulator seed. This
    reads the dataclass rather than a response, because a field added here is
    the moment it becomes publishable — long before an endpoint serialises it.
    """
    served = {field.name for field in fields(DirectoryStation)}

    assert not any(
        part in name
        for name in served
        for part in ("token", "key", "seed", "invite", "deleted")
    )


def test_stations_come_back_in_id_order(rollback: Any) -> None:
    """Keyset paging is only correct on a total, stable order (D-085)."""
    add(rollback, "st_c")
    add(rollback, "st_a")
    add(rollback, "st_b")

    found = find_stations_after(rollback, None, 10)

    ids = [station.station_id for station in found]
    assert ids == sorted(ids)
    assert {"st_a", "st_b", "st_c"} <= set(ids)


def test_a_cursor_excludes_the_station_it_names(rollback: Any) -> None:
    """Strictly greater than, so no station is served on two pages.

    The off-by-one in the other direction is the one worth testing: `>=` would
    repeat the last row of every page, which reads as a duplicate station rather
    than as a paging bug.
    """
    add(rollback, "st_p1")
    add(rollback, "st_p2")

    found = find_stations_after(rollback, "st_p1", 10)

    ids = [station.station_id for station in found]
    assert "st_p1" not in ids
    assert "st_p2" in ids


def test_the_limit_is_honoured(rollback: Any) -> None:
    """The route asks for one row more than it needs, so this must not round up."""
    for index in range(4):
        add(rollback, f"st_lim{index}")

    assert len(find_stations_after(rollback, "st_lim", 2)) == 2


def test_a_soft_deleted_station_is_not_listed(rollback: Any) -> None:
    """A retired station is not something the public directory shows."""
    add(rollback, "st_gone")
    with rollback.cursor() as cur:
        cur.execute(
            "update stations set deleted_at = now() where station_id = %s",
            ("st_gone",),
        )

    ids = [station.station_id for station in find_stations_after(rollback, None, 50)]

    assert "st_gone" not in ids
    assert find_station(rollback, "st_gone") is None


def test_a_station_whose_token_was_revoked_is_still_listed(rollback: Any) -> None:
    """The deliberate difference from the scheduler's read.

    `find_receiving_stations` excludes a revoked station because it can never act
    again. The directory keeps it, because it existed and its observations are
    still attributed to it — hiding it would make the station list disagree with
    the observation list, and that reads as data loss.
    """
    add(rollback, "st_revoked")
    with rollback.cursor() as cur:
        cur.execute(
            "update stations set token_revoked_at = now() where station_id = %s",
            ("st_revoked",),
        )

    ids = [station.station_id for station in find_stations_after(rollback, None, 50)]

    assert "st_revoked" in ids


def test_coordinates_come_back_at_full_stored_precision(rollback: Any) -> None:
    """The store never rounds; serialisation does (D-082).

    A station asking for the coarsest publication still has every digit here,
    because this same row shape is what the detail endpoint reads and the
    rounding has exactly one home.
    """
    add(rollback, "st_precise", location_precision_decimals=1)

    station = find_station(rollback, "st_precise")

    assert station is not None
    assert station.lat_deg == 12.971598
    assert station.lon_deg == 77.594562
    assert station.location_precision_decimals == 1


def test_an_unknown_station_is_absent_rather_than_an_error(rollback: Any) -> None:
    """`None`, which the route turns into `not_found`."""
    assert find_station(rollback, "st_never_existed") is None
