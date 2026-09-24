"""``meridian.store.snapshot_reads`` — which rows a raw snapshot holds.

Marked ``integration`` by the directory hook. The questions are the scope's:
what is inside the interval and what is not, which revisions and heartbeats
come with a pass, and that no credential leaves the database. The last test
renders every table through the canonical form, because a column type the
renderer refuses would otherwise first surface in the middle of an export.

Reference: docs/DECISIONS.md D-143, D-144.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.datasets.canonical import canonical_line  # noqa: E402
from meridian.store.archive_observations import (  # noqa: E402
    NewArchiveObservation,
    insert_archive_observation,
)
from meridian.store.archive_stations import (  # noqa: E402
    NewArchiveStation,
    insert_archive_station,
)
from meridian.store.ingest_records import (  # noqa: E402
    NewIngestRecord,
    insert_ingest_record,
)
from meridian.store.ingest_sources import (  # noqa: E402
    NewIngestSource,
    insert_ingest_source,
)
from meridian.store.snapshot_reads import (  # noqa: E402
    SNAPSHOT_TABLES,
    SnapshotScope,
    read_source_terms,
    read_table,
    snapshot_instant,
)

pytestmark = pytest.mark.integration

SINCE = datetime(2026, 8, 1, tzinfo=UTC)
INSIDE = datetime(2026, 8, 14, 9, 41, 18, tzinfo=UTC)
BEFORE = datetime(2026, 7, 20, 3, 0, 0, tzinfo=UTC)
SOURCE_ID = "reference_archive"


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes — see test_store_observations.py's twin."""
    with conn.transaction(force_rollback=True):
        yield conn


def scope(as_of: datetime | None = None) -> SnapshotScope:
    """From :data:`SINCE` to a moment safely after anything the test wrote."""
    return SnapshotScope(
        since=SINCE, as_of=as_of or datetime.now(UTC) + timedelta(minutes=5)
    )


def rows(conn: Any, name: str, within: SnapshotScope | None = None) -> list[Any]:
    table = next(one for one in SNAPSHOT_TABLES if one.name == name)
    return read_table(conn, table, within or scope())


@pytest.fixture
def seeded(rollback: Any, schedule_rows: Any) -> dict[str, Any]:
    """One pass inside the interval with everything it can depend on, one outside."""
    station = schedule_rows.station("st_snap", simulated=False)
    element_set = schedule_rows.satellite()
    inside = schedule_rows.pass_(station, INSIDE, element_set_id=element_set)
    before = schedule_rows.pass_(station, BEFORE, element_set_id=element_set)
    schedule_rows.assignment("as_in", inside)
    schedule_rows.assignment("as_before", before)
    schedule_rows.observation("as_in")
    schedule_rows.observation("as_before")
    rollback.execute(
        "insert into station_capabilities (station_id, band, freq_min_hz,"
        " freq_max_hz, modes, polarisation, min_elevation_deg)"
        " values (%s, 'vhf', 137000000, 138000000, '{lrpt}', 'rhcp', 10)",
        (station,),
    )
    rollback.execute(
        "insert into satellite_transmitters (satellite_id, centre_freq_hz, mode)"
        " values ('norad:99970', 137900000, 'lrpt')"
    )
    for received_at in (INSIDE + timedelta(minutes=2), INSIDE - timedelta(hours=1)):
        rollback.execute(
            "insert into heartbeats (station_id, sent_at, received_at, state)"
            " values (%s, %s, %s, 'idle')",
            (station, received_at, received_at),
        )
    _archive(rollback)
    return {"station": station, "inside": inside, "before": before}


def _archive(conn: Any) -> None:
    """One archive source, artefact, station and reception inside the interval."""
    insert_ingest_source(
        conn,
        NewIngestSource(
            source_id=SOURCE_ID,
            source_class="archive_receptions",
            name="Reference archive",
            licence="CC-BY-4.0",
            terms_url="https://example.invalid/terms",
            access_constraint="none",
            attribution_entry="The reference adapter's fixtures are ours",
        ),
    )
    record = insert_ingest_record(
        conn,
        NewIngestRecord(
            source_id=SOURCE_ID,
            original_identifier="art-1",
            source_version="v1",
            payload_kind="data",
            retrieved_at=INSIDE,
            sha256=bytes([7]) * 32,
            raw_path=f"{SOURCE_ID}/20260920T101143Z-abc",
            media_type="application/json",
            byte_count=128,
        ),
    ).record_id
    station = insert_archive_station(
        conn,
        NewArchiveStation(
            record_id=record,
            source_id=SOURCE_ID,
            source_station_key="gs-1",
            content_sha256=bytes([8]) * 32,
            lat_deg=12.9,
            lon_deg=77.6,
        ),
    ).archive_station_id
    insert_archive_observation(
        conn,
        NewArchiveObservation(
            record_id=record,
            source_id=SOURCE_ID,
            source_observation_id="obs-1",
            transformation_version="reference-1",
            content_sha256=bytes([9]) * 32,
            archive_station_id=station,
            satellite_key="99970",
            satellite_key_kind="norad",
            started_at=INSIDE + timedelta(hours=3),
            archive_outcome="decoded",
        ),
    )


# --- the interval ------------------------------------------------------------


def test_only_passes_inside_the_interval_are_read(
    rollback: Any, seeded: dict[str, Any]
) -> None:
    assert [one["id"] for one in rows(rollback, "passes")] == [seeded["inside"]]


def test_a_prediction_rising_just_before_since_comes_with_its_assignment(
    rollback: Any, schedule_rows: Any
) -> None:
    """D-148: its window reaches past ``since``, so the rise is seen whole."""
    station = schedule_rows.station("st_edge", simulated=False)
    element_set = schedule_rows.satellite()
    straddling = schedule_rows.pass_(
        station, SINCE - timedelta(seconds=2), element_set_id=element_set
    )
    ended = schedule_rows.pass_(
        station, SINCE - timedelta(minutes=30), element_set_id=element_set
    )
    schedule_rows.assignment("as_edge", straddling)

    ids = [one["id"] for one in rows(rollback, "passes")]

    assert straddling in ids
    assert ended not in ids
    assert "as_edge" in [one["assignment_id"] for one in rows(rollback, "assignments")]


@pytest.mark.usefixtures("seeded")
def test_a_pass_brings_its_assignments_and_nothing_elses(rollback: Any) -> None:
    assert [one["assignment_id"] for one in rows(rollback, "assignments")] == ["as_in"]
    assert [one["assignment_id"] for one in rows(rollback, "observations")] == ["as_in"]


def test_a_revision_submitted_after_as_of_is_not_in_the_snapshot(
    rollback: Any, seeded: dict[str, Any]
) -> None:
    """The observation was submitted just now; a snapshot an hour ago never saw it."""
    earlier = scope(as_of=datetime.now(UTC) - timedelta(hours=1))

    assert [one["id"] for one in rows(rollback, "passes", earlier)] == [
        seeded["inside"]
    ]
    assert rows(rollback, "observations", earlier) == []


@pytest.mark.usefixtures("seeded")
def test_only_heartbeats_inside_an_assignments_window_come_with_it(
    rollback: Any,
) -> None:
    received = [one["received_at"] for one in rows(rollback, "heartbeats")]

    assert received == [INSIDE + timedelta(minutes=2)]


@pytest.mark.usefixtures("seeded")
def test_what_a_pass_names_comes_with_it(rollback: Any) -> None:
    assert [one["station_id"] for one in rows(rollback, "stations")] == ["st_snap"]
    assert len(rows(rollback, "capabilities")) == 1
    assert len(rows(rollback, "element_sets")) == 1
    assert [one["satellite_id"] for one in rows(rollback, "satellites")] == [
        "norad:99970"
    ]
    assert len(rows(rollback, "transmitters")) == 1


@pytest.mark.usefixtures("seeded")
def test_archive_receptions_in_the_interval_come_with_their_provenance(
    rollback: Any,
) -> None:
    assert len(rows(rollback, "archive_observations")) == 1
    assert len(rows(rollback, "archive_stations")) == 1
    assert [one["source_id"] for one in rows(rollback, "ingest_records")] == [SOURCE_ID]

    (terms,) = read_source_terms(rollback, scope())
    assert terms.licence == "CC-BY-4.0"
    assert terms.records == 1


@pytest.mark.usefixtures("seeded")
def test_an_empty_interval_reads_nothing(rollback: Any) -> None:
    empty = SnapshotScope(since=SINCE, as_of=SINCE)

    assert all(read_table(rollback, table, empty) == [] for table in SNAPSHOT_TABLES)
    assert read_source_terms(rollback, empty) == []


# --- what leaves the database ----------------------------------------------


@pytest.mark.usefixtures("seeded")
def test_no_credential_is_read(rollback: Any) -> None:
    (station,) = rows(rollback, "stations")

    for column in (
        "token_sha256",
        "registration_key_sha256",
        "operator",
        "token_issued_at",
        "token_revoked_at",
    ):
        assert column not in station


@pytest.mark.usefixtures("seeded")
def test_every_table_renders_to_canonical_lines(rollback: Any) -> None:
    """Every column type the export will meet, rendered — or this fails first."""
    for table in SNAPSHOT_TABLES:
        for row in read_table(rollback, table, scope()):
            assert canonical_line(row).endswith(b"\n"), table.name


def test_the_snapshot_instant_is_fixed_for_the_transaction(rollback: Any) -> None:
    """Postgres fixes now() when the transaction starts: one as_of for every table."""
    first = snapshot_instant(rollback)

    assert first.tzinfo is not None
    assert snapshot_instant(rollback) == first


def test_an_archive_satellite_brings_the_set_current_at_each_days_start(
    rollback: Any, schedule_rows: Any
) -> None:
    """D-150: the sets an archive station's denominator is propagated from.

    One set is current from before the interval, a second from 01:00 on the
    reception's day — so it is current only from the next midnight — and a
    third has an epoch nothing in the interval reaches.
    """
    satellite = "norad:88801"
    day = datetime(2026, 8, 14, tzinfo=UTC)
    old = schedule_rows.element_set_at(satellite, SINCE - timedelta(days=2), "manual")
    newer = schedule_rows.element_set_at(
        satellite, day + timedelta(hours=1), "celestrak"
    )
    schedule_rows.element_set_at(
        satellite, datetime.now(UTC) + timedelta(days=30), "spacetrack"
    )
    schedule_rows.archive_reception(satellite, day + timedelta(hours=9))

    held = [
        one["id"]
        for one in rows(rollback, "element_sets")
        if one["satellite_id"] == satellite
    ]

    assert held == sorted([old, newer])


def test_an_archive_satellite_keyed_by_name_brings_no_element_set(
    rollback: Any, schedule_rows: Any
) -> None:
    satellite = "norad:88802"
    schedule_rows.element_set_at(satellite, SINCE - timedelta(days=2), "manual")
    station = schedule_rows.archive_reception(satellite, INSIDE)
    rollback.execute(
        "update archive_observations set satellite_key_kind = 'source_name'"
        " where archive_station_id = %s",
        (station,),
    )

    held = rows(rollback, "element_sets")

    assert [one for one in held if one["satellite_id"] == satellite] == []
