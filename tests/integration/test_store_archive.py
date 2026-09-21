"""``meridian.store.archive_stations`` and ``archive_observations``.

Marked ``integration`` by the directory hook in ``tests/conftest.py``. Builds
the same graph the loader will: a source, one retrieved artefact, and the
stations and receptions normalised out of it.

Three properties carry most of this file, and each is a decision rather than an
implementation detail: a changed station description appends instead of
rewriting, an object we do not track is stored rather than dropped, and two
different normalisations of one reception stop the load rather than settling it
by whichever ran last.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.store.archive_observations import (  # noqa: E402 — after importorskip
    NewArchiveObservation,
    NormalisationDisagreementError,
    count_satellite_coverage,
    find_archive_observations_for_record,
    insert_archive_observation,
)
from meridian.store.archive_stations import (  # noqa: E402 — after importorskip
    NewArchiveStation,
    count_denominator_inputs,
    find_archive_station_by_id,
    find_archive_stations_for_source,
    insert_archive_station,
)
from meridian.store.ingest_records import (  # noqa: E402 — after importorskip
    NewIngestRecord,
    insert_ingest_record,
)
from meridian.store.ingest_sources import (  # noqa: E402 — after importorskip
    NewIngestSource,
    insert_ingest_source,
)

pytestmark = pytest.mark.integration

SOURCE_ID = "reference_archive"
KNOWN_SATELLITE = "norad:99997"
RETRIEVED_AT = datetime(2026, 9, 20, 10, 11, 43, tzinfo=UTC)
STARTED_AT = datetime(2026, 8, 14, 9, 41, 18, tzinfo=UTC)


def digest(seed: int) -> bytes:
    """A 32-byte stand-in for a real digest, distinct per ``seed``."""
    return bytes([seed % 256]) * 32


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes — see test_store_observations.py's twin."""
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def record_id(rollback: Any) -> int:
    """One source, one catalogued satellite, one retrieved artefact."""
    insert_ingest_source(
        rollback,
        NewIngestSource(
            source_id=SOURCE_ID,
            source_class="archive_receptions",
            name="Reference archive",
            licence="CC-BY-4.0",
            terms_url="https://example.invalid/terms",
            access_constraint="none",
            attribution_entry="Ingested data sources: reference adapter",
        ),
    )
    rollback.execute(
        "insert into satellites (satellite_id, name) values (%s, %s)",
        (KNOWN_SATELLITE, "Tracked"),
    )
    arrival = insert_ingest_record(
        rollback,
        NewIngestRecord(
            source_id=SOURCE_ID,
            original_identifier="art-1",
            source_version="v1",
            payload_kind="data",
            retrieved_at=RETRIEVED_AT,
            sha256=digest(0),
            raw_path=f"{SOURCE_ID}/20260920T101143Z-abc/artefact.bin",
            media_type="application/json",
            byte_count=128,
        ),
    )
    return arrival.record_id


def a_station(record_id: int, key: str = "gs-1", **overrides: object) -> Any:
    fields: dict[str, object] = {
        "record_id": record_id,
        "source_id": SOURCE_ID,
        "source_station_key": key,
        "content_sha256": digest(1),
    }
    fields.update(overrides)
    return NewArchiveStation(**fields)  # type: ignore[arg-type]


def a_reception(record_id: int, **overrides: object) -> Any:
    fields: dict[str, object] = {
        "record_id": record_id,
        "source_id": SOURCE_ID,
        "source_observation_id": "obs-1",
        "transformation_version": "reference-1",
        "content_sha256": digest(2),
        "satellite_key": KNOWN_SATELLITE,
        "satellite_key_kind": "norad",
        "started_at": STARTED_AT,
        "archive_outcome": "decoded",
    }
    fields.update(overrides)
    return NewArchiveObservation(**fields)  # type: ignore[arg-type]


def test_a_station_description_round_trips(rollback: Any, record_id: int) -> None:
    arrival = insert_archive_station(
        rollback,
        a_station(
            record_id,
            name="Somebody's roof",
            lat_deg=51.5,
            lon_deg=-0.1,
            alt_m=20.0,
            capability={"modes": ["lrpt"], "band": "vhf"},
        ),
    )

    stored = find_archive_station_by_id(rollback, arrival.archive_station_id)

    assert arrival.written is True
    assert stored is not None
    assert stored.name == "Somebody's roof"
    assert stored.lat_deg == 51.5
    assert stored.capability == {"modes": ["lrpt"], "band": "vhf"}
    assert stored.denominator_inputs == "location_and_capability"


def test_the_same_description_twice_is_one_row(rollback: Any, record_id: int) -> None:
    first = insert_archive_station(
        rollback, a_station(record_id, lat_deg=51.5, lon_deg=-0.1)
    )
    second = insert_archive_station(
        rollback, a_station(record_id, lat_deg=51.5, lon_deg=-0.1)
    )

    assert second.written is False
    assert second.archive_station_id == first.archive_station_id


def test_moved_coordinates_append_rather_than_rewrite(
    rollback: Any, record_id: int
) -> None:
    """The content key, and the reason for it (D-057's argument, D-139's table).

    A denominator computed from the old coordinates last month has to stay
    explainable this month, so the old description survives untouched beside
    the new one.
    """
    first = insert_archive_station(
        rollback, a_station(record_id, lat_deg=51.5, lon_deg=-0.1)
    )
    second = insert_archive_station(
        rollback,
        a_station(record_id, lat_deg=52.0, lon_deg=-0.2, content_sha256=digest(9)),
    )

    assert second.written is True
    assert second.archive_station_id != first.archive_station_id

    older = find_archive_station_by_id(rollback, first.archive_station_id)
    assert older is not None
    assert older.lat_deg == 51.5, "the earlier description is untouched"

    listed = find_archive_stations_for_source(rollback, SOURCE_ID)
    assert len(listed) == 2


def test_a_half_published_location_is_refused(rollback: Any, record_id: int) -> None:
    """A latitude without a longitude is not a place, and would be used as one."""
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_archive_station(rollback, a_station(record_id, lat_deg=51.5))


def test_the_coverage_count_names_every_case_including_the_zeroes(
    rollback: Any, record_id: int
) -> None:
    """ "No stations lacked a location" and "nobody asked" must not look alike.

    The counts are published beside a completeness ratio, never used to filter
    the rows they describe — a ratio computed only over the stations we happened
    to have coordinates for is the selection bias this project exists to
    measure, arriving through the back door.
    """
    insert_archive_station(rollback, a_station(record_id, "nowhere"))
    insert_archive_station(
        rollback,
        a_station(
            record_id, "located", lat_deg=51.5, lon_deg=-0.1, content_sha256=digest(3)
        ),
    )
    insert_archive_station(
        rollback,
        a_station(
            record_id,
            "described",
            lat_deg=51.5,
            lon_deg=-0.1,
            capability={"modes": ["lrpt"]},
            content_sha256=digest(4),
        ),
    )

    counts = count_denominator_inputs(rollback, SOURCE_ID)

    assert counts == {
        "neither": 1,
        "location_only": 1,
        "location_and_capability": 1,
    }


@pytest.mark.usefixtures("record_id")
def test_the_coverage_count_is_zero_filled_for_an_empty_source(
    rollback: Any,
) -> None:
    """A registered source with no stations yet still answers the question."""
    assert count_denominator_inputs(rollback, SOURCE_ID) == {
        "neither": 0,
        "location_only": 0,
        "location_and_capability": 0,
    }


def test_a_reception_round_trips_with_the_archives_own_words(
    rollback: Any, record_id: int
) -> None:
    """``source_outcome`` keeps what the archive said, so the mapping is checkable."""
    station = insert_archive_station(rollback, a_station(record_id))
    insert_archive_observation(
        rollback,
        a_reception(
            record_id,
            archive_station_id=station.archive_station_id,
            ended_at=STARTED_AT + timedelta(minutes=11),
            max_elevation_deg=64.2,
            centre_freq_hz=137_100_000,
            mode="lrpt",
            archive_outcome="no_data",
            source_outcome="nothing received",
            frames_decoded=0,
        ),
    )

    stored = find_archive_observations_for_record(rollback, record_id)

    assert len(stored) == 1
    assert stored[0].archive_outcome == "no_data"
    assert stored[0].source_outcome == "nothing received"
    assert stored[0].centre_freq_hz == 137_100_000


def test_an_msp_outcome_cannot_be_stored(rollback: Any, record_id: int) -> None:
    """``no_signal`` claims listening evidence we do not have for this station."""
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_archive_observation(
            rollback, a_reception(record_id, archive_outcome="no_signal")
        )


def test_loading_the_same_reception_twice_writes_nothing(
    rollback: Any, record_id: int
) -> None:
    """What makes the completion gate demonstrable by simply running it again."""
    first = insert_archive_observation(rollback, a_reception(record_id))
    second = insert_archive_observation(rollback, a_reception(record_id))

    assert first.written is True
    assert second.written is False
    assert second.archive_observation_id == first.archive_observation_id
    assert len(find_archive_observations_for_record(rollback, record_id)) == 1


def test_two_different_bodies_under_one_version_stop_the_load(
    rollback: Any, record_id: int
) -> None:
    """A nondeterministic normaliser is a bug in the property being demonstrated.

    Neither row can be trusted, and neither the loader nor the table can tell
    which is right — so absorbing it would settle the question by whichever ran
    last, silently.
    """
    insert_archive_observation(rollback, a_reception(record_id))

    with pytest.raises(NormalisationDisagreementError, match="not deterministic"):
        insert_archive_observation(
            rollback, a_reception(record_id, content_sha256=digest(7))
        )


def test_a_new_transformation_version_appends(rollback: Any, record_id: int) -> None:
    """Retrieval and transformation are separate events (D-140).

    The version is part of the key precisely so two normalisations of one
    artefact can be compared, which is impossible if the second overwrites the
    first.
    """
    insert_archive_observation(rollback, a_reception(record_id))
    second = insert_archive_observation(
        rollback,
        a_reception(
            record_id, transformation_version="reference-2", content_sha256=digest(8)
        ),
    )

    assert second.written is True
    stored = find_archive_observations_for_record(rollback, record_id)
    assert {row.transformation_version for row in stored} == {
        "reference-1",
        "reference-2",
    }


def test_an_untracked_object_is_stored_and_counted_not_dropped(
    rollback: Any, record_id: int
) -> None:
    """No FK to ``satellites``, so coverage is a number rather than a filter.

    Dropping these would stack a second selection filter on the archive's own —
    invisible downstream, and indistinguishable from the archive simply not
    holding those passes.
    """
    insert_archive_observation(rollback, a_reception(record_id))
    insert_archive_observation(
        rollback,
        a_reception(
            record_id,
            source_observation_id="obs-2",
            satellite_key="unknown-bird",
            satellite_key_kind="source_name",
            content_sha256=digest(5),
        ),
    )

    coverage = count_satellite_coverage(rollback, SOURCE_ID)
    catalogued = rollback.execute("select count(*) from satellites").fetchone()[0]

    assert coverage.matched == 1
    assert coverage.unmatched == 1
    assert catalogued == 1, "an archive never adds to our catalogue"


@pytest.mark.usefixtures("record_id")
def test_coverage_of_an_empty_source_is_two_zeroes(rollback: Any) -> None:
    """Nothing stored is two zeroes, not an absent answer."""
    coverage = count_satellite_coverage(rollback, SOURCE_ID)

    assert coverage.matched == 0
    assert coverage.unmatched == 0
