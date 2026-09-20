"""``meridian.store.ingest_sources`` and ``ingest_records`` against real SQL.

Marked ``integration`` by the directory hook in ``tests/conftest.py``. Neither
table is a hypertable, so unlike most of this directory these exercise ordinary
PostgreSQL behaviour — but they still need the migrated schema, because every
property under test is a constraint rather than a branch in Python.

The two write paths differ on purpose and the difference is what most of this
file is about: an artefact that arrives twice is one row and says so quietly,
while a *source* that arrives twice raises, because two source rows with one id
differ in their terms and no conflict clause can compare those.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.store.ingest_records import (  # noqa: E402 — after importorskip
    NewIngestRecord,
    find_ingest_record_by_id,
    find_ingest_records_for_source,
    insert_ingest_record,
    mark_ingest_record_superseded,
)
from meridian.store.ingest_sources import (  # noqa: E402 — after importorskip
    NewIngestSource,
    find_active_ingest_sources,
    find_ingest_source,
    insert_ingest_source,
)

pytestmark = pytest.mark.integration

SOURCE_ID = "reference_archive"
RETRIEVED_AT = datetime(2026, 9, 20, 10, 11, 43, tzinfo=UTC)


def digest(seed: int) -> bytes:
    """A 32-byte stand-in for a real digest, distinct per ``seed``."""
    return bytes([seed % 256]) * 32


def a_source(source_id: str = SOURCE_ID, **overrides: str) -> NewIngestSource:
    fields = {
        "source_id": source_id,
        "source_class": "archive_receptions",
        "name": "Reference archive",
        "licence": "CC-BY-4.0",
        "terms_url": "https://example.invalid/terms",
        "access_constraint": "none",
        "attribution_entry": "Ingested data sources: reference adapter",
    }
    fields.update(overrides)
    return NewIngestSource(**fields)


def an_artefact(
    identifier: str = "art-1", sha: bytes | None = None, **overrides: object
) -> NewIngestRecord:
    fields: dict[str, object] = {
        "source_id": SOURCE_ID,
        "original_identifier": identifier,
        "source_version": "v1",
        "payload_kind": "data",
        "retrieved_at": RETRIEVED_AT,
        "sha256": digest(0) if sha is None else sha,
        "raw_path": f"{SOURCE_ID}/20260920T101143Z-abc/{identifier}.bin",
        "media_type": "application/json",
        "byte_count": 128,
    }
    fields.update(overrides)
    return NewIngestRecord(**fields)  # type: ignore[arg-type]


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes — see test_store_observations.py's twin."""
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def registered(rollback: Any) -> Any:
    """One registered source, which every record below hangs off."""
    insert_ingest_source(rollback, a_source())
    return rollback


def test_a_registered_source_reads_back_with_its_terms(registered: Any) -> None:
    """The licence and terms are the point of the row, not decoration.

    They are what decide whether the evidence dataset may republish a record or
    must reference it by checksum (D-136), and that question is asked long after
    the retrieval.
    """
    stored = find_ingest_source(registered, SOURCE_ID)

    assert stored is not None
    assert stored.licence == "CC-BY-4.0"
    assert stored.terms_url == "https://example.invalid/terms"
    assert stored.attribution_entry == "Ingested data sources: reference adapter"
    assert stored.active is True


def test_an_unregistered_source_reads_as_absent(rollback: Any) -> None:
    """Absence is the refusal: no terms recorded, so no retrieval may begin."""
    assert find_ingest_source(rollback, "never_registered") is None


def test_registering_a_source_twice_raises_rather_than_passing(
    registered: Any,
) -> None:
    """The loud failure is the feature (D-140).

    Swallowing this would let a source whose licence changed go on collecting
    records under the licence it used to have. A caller that wants idempotence
    reads ``find_ingest_source`` and compares — which is a comparison, and an
    ``on conflict`` clause cannot make one.
    """
    with pytest.raises(psycopg.errors.UniqueViolation):
        insert_ingest_source(registered, a_source(licence="All rights reserved"))


def test_a_blank_licence_is_refused(rollback: Any) -> None:
    """D-134 enforced by the column, not by whoever wrote the caller."""
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_ingest_source(rollback, a_source("blank_licence", licence="   "))


def test_a_source_id_that_is_not_a_safe_path_element_is_refused(
    rollback: Any,
) -> None:
    """The id is also a directory name in the raw store (D-141)."""
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_ingest_source(rollback, a_source("../escape"))


def test_active_sources_list_in_a_stable_order(registered: Any) -> None:
    """Two runs list the same sources in the same order.

    A command whose output order drifts makes a diff between two runs
    unreadable, and CLAUDE.md requires every published number to be
    regenerable by someone who was not in the room.
    """
    insert_ingest_source(registered, a_source("second_archive"))
    registered.execute(
        "update ingest_sources set added_at = now() + interval '1 hour'"
        " where source_id = %s",
        ("second_archive",),
    )

    listed = [source.source_id for source in find_active_ingest_sources(registered)]

    assert listed == [SOURCE_ID, "second_archive"]


def test_a_retired_source_is_excluded_but_not_deleted(registered: Any) -> None:
    """Records already taken under it keep their provenance and stay readable."""
    registered.execute(
        "update ingest_sources set active = false where source_id = %s", (SOURCE_ID,)
    )

    assert find_active_ingest_sources(registered) == []
    assert find_ingest_source(registered, SOURCE_ID) is not None


def test_the_same_artefact_twice_is_one_row(registered: Any) -> None:
    """Re-running a fetch costs nothing, which is what makes a backfill safe.

    The second call reports the id it already had rather than a new one, so a
    caller cannot act on "written" and be wrong about which row it means.
    """
    first = insert_ingest_record(registered, an_artefact())
    second = insert_ingest_record(registered, an_artefact())

    assert first.written is True
    assert second.written is False
    assert second.record_id == first.record_id


def test_a_differing_refetch_is_a_second_row(registered: Any) -> None:
    """Different bytes under one identifier are two artefacts, never an edit."""
    first = insert_ingest_record(registered, an_artefact())
    second = insert_ingest_record(registered, an_artefact(sha=digest(1)))

    assert second.written is True
    assert second.record_id != first.record_id


def test_supersession_links_the_old_row_and_writes_nothing_else(
    registered: Any,
) -> None:
    """The one update in the whole write path, and it fills a null."""
    first = insert_ingest_record(registered, an_artefact())
    second = insert_ingest_record(registered, an_artefact(sha=digest(1)))

    assert mark_ingest_record_superseded(registered, first.record_id, second.record_id)

    older = find_ingest_record_by_id(registered, first.record_id)
    newer = find_ingest_record_by_id(registered, second.record_id)
    assert older is not None and newer is not None
    assert older.superseded_by == second.record_id
    assert older.sha256 == digest(0), "the superseded row's bytes are untouched"
    assert newer.superseded_by is None


def test_marking_an_already_superseded_record_changes_nothing(
    registered: Any,
) -> None:
    """A second link would overwrite the first, which is the one thing forbidden.

    ``where superseded_by is null`` makes that impossible under a second writer,
    and the False says so rather than leaving the caller to assume.
    """
    first = insert_ingest_record(registered, an_artefact())
    second = insert_ingest_record(registered, an_artefact(sha=digest(1)))
    third = insert_ingest_record(registered, an_artefact(sha=digest(2)))
    mark_ingest_record_superseded(registered, first.record_id, second.record_id)

    assert not mark_ingest_record_superseded(
        registered, first.record_id, third.record_id
    )

    older = find_ingest_record_by_id(registered, first.record_id)
    assert older is not None
    assert older.superseded_by == second.record_id


def test_a_record_cannot_supersede_itself(registered: Any) -> None:
    arrival = insert_ingest_record(registered, an_artefact())
    with pytest.raises(psycopg.errors.CheckViolation):
        mark_ingest_record_superseded(registered, arrival.record_id, arrival.record_id)


def test_a_raw_path_leaving_the_store_is_refused(registered: Any) -> None:
    """The column is the second line of defence behind the writer (D-141)."""
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_ingest_record(
            registered, an_artefact(raw_path="../outside/artefact.bin")
        )


def test_the_described_interval_and_extent_survive_a_round_trip(
    registered: Any,
) -> None:
    """What an artefact describes is not when we fetched it (D-131).

    A feature lookup selects on the first; confusing it for the second is how a
    model reads the future, so the two are stored as separate columns and both
    are checked here.
    """
    arrival = insert_ingest_record(
        registered,
        an_artefact(
            valid_from=RETRIEVED_AT - timedelta(days=2),
            valid_to=RETRIEVED_AT - timedelta(days=1),
            spatial_extent={
                "north_deg": 20.5,
                "south_deg": 8.0,
                "east_deg": 88.25,
                "west_deg": 72.0,
            },
        ),
    )

    stored = find_ingest_record_by_id(registered, arrival.record_id)

    assert stored is not None
    assert stored.retrieved_at == RETRIEVED_AT
    assert stored.valid_from == RETRIEVED_AT - timedelta(days=2)
    assert stored.valid_to == RETRIEVED_AT - timedelta(days=1)
    assert stored.spatial_extent == {
        "north_deg": 20.5,
        "south_deg": 8.0,
        "east_deg": 88.25,
        "west_deg": 72.0,
    }


def test_listing_a_source_includes_what_was_superseded(registered: Any) -> None:
    """Their bytes are still on disk, ``verify`` has to check them.

    A listing that hid the artefact a published figure was computed from would
    make that figure unexplainable, which is the opposite of what this table is
    for.
    """
    first = insert_ingest_record(registered, an_artefact())
    second = insert_ingest_record(registered, an_artefact(sha=digest(1)))
    mark_ingest_record_superseded(registered, first.record_id, second.record_id)

    listed = find_ingest_records_for_source(registered, SOURCE_ID)

    assert [record.record_id for record in listed] == [
        first.record_id,
        second.record_id,
    ]
    assert [record.superseded_by for record in listed] == [second.record_id, None]


def test_an_unknown_record_id_reads_as_absent(registered: Any) -> None:
    assert find_ingest_record_by_id(registered, 10_000_000) is None
