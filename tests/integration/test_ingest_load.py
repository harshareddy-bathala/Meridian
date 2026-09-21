"""The load: raw store in, archive tables out, one artefact at a time.

Marked ``integration`` by the directory hook in ``tests/conftest.py``. This is
where the whole stage meets: the reference adapter plans, a fixture retriever
fetches, the raw store publishes, and the loader turns what is on disk into
rows.

The property most of this file is about is that **running it again is safe** —
a second load of the same tree writes nothing and says so, which is how the
completion gate is demonstrated rather than argued. The rest is what the load
refuses: terms that changed under a registered source, a normaliser that
disagrees with itself, and anything derived from a tile.

Reference: docs/DECISIONS.md D-133, D-139, D-140, D-141, D-142.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.store.archive_observations import (  # noqa: E402 — after importorskip
    NormalisationDisagreementError,
    count_satellite_coverage,
    find_archive_observations_for_record,
)
from meridian.store.archive_stations import (  # noqa: E402 — after importorskip
    count_denominator_inputs,
    find_archive_stations_for_source,
)
from meridian.store.ingest_records import (  # noqa: E402 — after importorskip
    find_ingest_record_by_id,
    find_ingest_records_for_source,
)
from meridian.store.ingest_sources import (  # noqa: E402 — after importorskip
    find_ingest_source,
)
from meridian_ingest.adapters.protocol import (  # noqa: E402 — after importorskip
    FetchRequest,
)
from meridian_ingest.adapters.reference import (  # noqa: E402 — after importorskip
    FIXTURE_ROOT,
    REFERENCE_SOURCE,
    TRANSFORMATION_VERSION,
    ReferenceAdapter,
    ReferenceNormaliser,
)
from meridian_ingest.load import (  # noqa: E402 — after importorskip
    TermsChangedError,
    load_artefact,
    load_source,
    register_source,
)
from meridian_ingest.provenance import Provenance  # noqa: E402 — after importorskip
from meridian_ingest.raw_store import RawStore  # noqa: E402 — after importorskip
from meridian_ingest.retrieval import (  # noqa: E402 — after importorskip
    FixtureRetriever,
)

pytestmark = pytest.mark.integration

SOURCE = "reference_archive"
RETRIEVED_AT = datetime(2026, 9, 20, 10, 11, 43, tzinfo=UTC)
JULY = "receptions-2026-07.json"
AUGUST = "receptions-2026-08.json"
TILE = "coverage-2026-08.png"


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes — see test_store_archive.py's twin."""
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def store(tmp_path: Path) -> Iterator[RawStore]:
    """A raw store under ``tmp_path``, left writable so the tree can be removed."""
    root = tmp_path / "raw"
    yield RawStore(root)
    for path in sorted(root.rglob("*"), reverse=True):
        path.chmod(0o700 if path.is_dir() else 0o600)


def fetch(store: RawStore, name: str, retrieved_at: datetime = RETRIEVED_AT) -> str:
    """Fetch one artefact into the raw store, exactly as ``fetch`` will."""
    adapter = ReferenceAdapter()
    remote = next(
        one for one in adapter.plan(FetchRequest()) if one.original_identifier == name
    )
    retrieved = FixtureRetriever(FIXTURE_ROOT).retrieve(remote)
    published = store.publish(
        Provenance(
            source_id=SOURCE,
            original_identifier=remote.original_identifier,
            source_version=adapter.source_version(retrieved),
            payload_kind=remote.payload_kind,
            retrieved_at=retrieved_at,
            media_type=retrieved.media_type,
            valid_from=remote.valid_from,
            valid_to=remote.valid_to,
        ),
        retrieved.chunks,
    )
    return published.raw_path


def fetch_all(store: RawStore) -> None:
    for name in (JULY, AUGUST, TILE):
        fetch(store, name)


# --- registering the source ------------------------------------------------


def test_the_source_is_recorded_with_the_terms_its_adapter_declares(
    rollback: Any,
) -> None:
    written = register_source(rollback, REFERENCE_SOURCE)

    stored = find_ingest_source(rollback, SOURCE)

    assert written is True
    assert stored is not None
    assert stored.licence == REFERENCE_SOURCE.licence
    assert stored.terms_url == REFERENCE_SOURCE.terms_url
    assert stored.attribution_entry == REFERENCE_SOURCE.attribution_entry


def test_registering_twice_writes_one_row(rollback: Any) -> None:
    register_source(rollback, REFERENCE_SOURCE)

    assert register_source(rollback, REFERENCE_SOURCE) is False


def test_terms_that_changed_under_a_registered_source_stop_the_load(
    rollback: Any,
) -> None:
    """``ingest_sources`` is insert-only, so the alternative is loading in silence.

    Without this, records retrieved after a licence change would keep citing a
    row that asserts the old one.
    """
    import dataclasses

    register_source(rollback, REFERENCE_SOURCE)
    relicensed = dataclasses.replace(REFERENCE_SOURCE, licence="CC-BY-NC-4.0")

    with pytest.raises(TermsChangedError, match="new source_id"):
        register_source(rollback, relicensed)


def test_a_corrected_display_name_is_not_a_change_of_terms(rollback: Any) -> None:
    """A label grants nothing, so a typo fix does not cost a new source id."""
    import dataclasses

    register_source(rollback, REFERENCE_SOURCE)
    renamed = dataclasses.replace(REFERENCE_SOURCE, name="Meridian reference archive ")

    assert register_source(rollback, renamed) is False


# --- loading a tree --------------------------------------------------------


def test_a_whole_tree_loads_into_the_archive_tables(
    rollback: Any, store: RawStore
) -> None:
    fetch_all(store)

    report = load_source(rollback, store, SOURCE)

    assert report.source_registered is True
    assert report.records_written == 3
    assert report.stations_written == 4, "gs-dublin is one row across two months"
    assert report.receptions_written == 7


def test_the_same_station_described_twice_is_one_row(
    rollback: Any, store: RawStore
) -> None:
    """August describes gs-dublin exactly as July did, so it is the row July wrote."""
    fetch_all(store)
    load_source(rollback, store, SOURCE)

    stored = find_archive_stations_for_source(rollback, SOURCE)
    dublin = [one for one in stored if one.source_station_key == "gs-dublin"]

    assert len(dublin) == 1


def test_a_station_that_moved_becomes_a_second_row(
    rollback: Any, store: RawStore
) -> None:
    """So a denominator computed from July's coordinates stays explainable."""
    fetch_all(store)
    load_source(rollback, store, SOURCE)

    stored = find_archive_stations_for_source(rollback, SOURCE)
    galway = sorted(
        (one.lat_deg for one in stored if one.source_station_key == "gs-galway"),
    )

    assert galway == [53.2707, 53.28]


def test_every_station_is_counted_for_what_a_denominator_could_use(
    rollback: Any, store: RawStore
) -> None:
    """The shortfall is published, never used to filter the rows it describes."""
    fetch_all(store)
    load_source(rollback, store, SOURCE)

    counts = count_denominator_inputs(rollback, SOURCE)

    assert counts == {
        "neither": 1,
        "location_only": 2,
        "location_and_capability": 1,
    }


def test_an_object_we_do_not_track_is_stored_and_counted(
    rollback: Any, store: RawStore
) -> None:
    """No FK to ``satellites``: an archive never decides what we propagate."""
    rollback.execute(
        "insert into satellites (satellite_id, name) values (%s, %s)",
        ("norad:57166", "Meteor-M N2-4"),
    )
    fetch_all(store)
    load_source(rollback, store, SOURCE)

    coverage = count_satellite_coverage(rollback, SOURCE)
    catalogued = rollback.execute("select count(*) from satellites").fetchone()[0]

    assert coverage.matched == 3
    assert coverage.unmatched == 4
    assert catalogued == 1, "an archive never adds to our catalogue"


def test_the_archives_own_words_reach_the_table(rollback: Any, store: RawStore) -> None:
    """``source_outcome`` verbatim, so the mapping stays checkable in the database."""
    path = fetch(store, AUGUST)
    register_source(rollback, REFERENCE_SOURCE)
    loaded = load_artefact(rollback, store, ReferenceNormaliser(), path)

    stored = find_archive_observations_for_record(rollback, loaded.record_id)
    offline = next(one for one in stored if one.source_observation_id == "obs-2608-003")

    assert offline.archive_outcome == "unknown"
    assert offline.source_outcome == "receiver offline"
    assert {one.transformation_version for one in stored} == {TRANSFORMATION_VERSION}


def test_a_reception_is_linked_to_the_station_its_artefact_described(
    rollback: Any, store: RawStore
) -> None:
    path = fetch(store, JULY)
    register_source(rollback, REFERENCE_SOURCE)
    loaded = load_artefact(rollback, store, ReferenceNormaliser(), path)

    stored = find_archive_observations_for_record(rollback, loaded.record_id)
    linked = {one.source_observation_id: one.archive_station_id for one in stored}

    assert all(one is not None for one in linked.values())
    assert linked["obs-2607-001"] != linked["obs-2607-002"]


def test_a_reception_naming_no_station_keeps_none(
    rollback: Any, store: RawStore
) -> None:
    path = fetch(store, AUGUST)
    register_source(rollback, REFERENCE_SOURCE)
    loaded = load_artefact(rollback, store, ReferenceNormaliser(), path)

    stored = find_archive_observations_for_record(rollback, loaded.record_id)
    unattributed = next(
        one for one in stored if one.source_observation_id == "obs-2608-004"
    )

    assert unattributed.archive_station_id is None


# --- running it again ------------------------------------------------------


def test_loading_the_same_tree_twice_writes_nothing_the_second_time(
    rollback: Any, store: RawStore
) -> None:
    """The completion gate, demonstrated by doing it rather than asserting it."""
    fetch_all(store)
    load_source(rollback, store, SOURCE)

    again = load_source(rollback, store, SOURCE)

    assert again.wrote_nothing() is True
    assert again.source_registered is False
    assert again.receptions_already_held == 7


def test_the_row_counts_do_not_move_on_a_second_load(
    rollback: Any, store: RawStore
) -> None:
    """Because "wrote nothing" is a report, and the tables are the evidence."""
    fetch_all(store)
    load_source(rollback, store, SOURCE)
    before = _counts(rollback)

    load_source(rollback, store, SOURCE)

    assert _counts(rollback) == before


def _counts(conn: Any) -> tuple[int, int, int]:
    return tuple(  # type: ignore[return-value]
        conn.execute(f"select count(*) from {table}").fetchone()[0]
        for table in ("ingest_records", "archive_stations", "archive_observations")
    )


# --- tiles -----------------------------------------------------------------


def test_a_tile_is_recorded_and_nothing_is_derived_from_it(
    rollback: Any, store: RawStore
) -> None:
    """D-133. The record exists so "we hold this and used none of it" is visible."""
    fetch_all(store)

    report = load_source(rollback, store, SOURCE)
    skipped = report.skipped

    assert [one.skipped for one in skipped] == ["tile"]
    tile = find_ingest_record_by_id(rollback, skipped[0].record_id)
    assert tile is not None
    assert tile.payload_kind == "tile"
    assert find_archive_observations_for_record(rollback, tile.record_id) == []


# --- supersession ----------------------------------------------------------


def test_a_re_fetch_of_the_same_bytes_is_one_record(
    rollback: Any, store: RawStore
) -> None:
    """Two retrievals, one artefact: the digest is what the table is keyed on.

    Both directories stay in the raw store — two fetches did happen, and the
    store records events rather than deciding which of them mattered — but they
    hold the same bytes, so they are one row and neither supersedes anything.
    The row's ``raw_path`` names the first of them, which is the copy ``verify``
    checks; the second is a byte-identical duplicate on disk.
    """
    fetch(store, JULY)
    fetch(store, JULY, retrieved_at=RETRIEVED_AT + timedelta(days=1))

    report = load_source(rollback, store, SOURCE)

    assert len(report.artefacts) == 2, "two retrievals were visited"
    assert report.records_written == 1, "and they are one artefact"
    assert len({one.record_id for one in report.artefacts}) == 1
    assert [one.superseded for one in report.artefacts] == [(), ()]


def test_a_differing_re_fetch_supersedes_the_record_it_replaces(
    rollback: Any, store: RawStore, tmp_path: Path
) -> None:
    """Filling a column that was null — the only update in the whole write path.

    Neither artefact is deleted and no bytes are touched, because a figure
    computed from the older one last month has to stay explainable this month.
    """
    first = fetch(store, JULY)
    revised = _republished(store, tmp_path)
    register_source(rollback, REFERENCE_SOURCE)

    load_artefact(rollback, store, ReferenceNormaliser(), first)
    later = load_artefact(rollback, store, ReferenceNormaliser(), revised)

    records = {
        one.raw_path: one for one in find_ingest_records_for_source(rollback, SOURCE)
    }
    assert len(later.superseded) == 1
    assert records[first].superseded_by == later.record_id
    assert records[revised].superseded_by is None


def test_a_supersession_is_recorded_once(
    rollback: Any, store: RawStore, tmp_path: Path
) -> None:
    """A second load must not overwrite a link that already says something."""
    fetch(store, JULY)
    _republished(store, tmp_path)
    register_source(rollback, REFERENCE_SOURCE)
    load_source(rollback, store, SOURCE)

    again = load_source(rollback, store, SOURCE)

    assert [one.superseded for one in again.artefacts] == [(), ()]


def _republished(store: RawStore, tmp_path: Path) -> str:
    """The same identifier, different bytes: the archive corrected its file."""
    revised = tmp_path / "fixtures"
    revised.mkdir(exist_ok=True)
    body = (FIXTURE_ROOT / JULY).read_text(encoding="utf-8")
    (revised / JULY).write_text(body.replace("812", "813"), encoding="utf-8")

    adapter = ReferenceAdapter()
    remote = next(
        one for one in adapter.plan(FetchRequest()) if one.original_identifier == JULY
    )
    retrieved = FixtureRetriever(revised).retrieve(remote)
    published = store.publish(
        Provenance(
            source_id=SOURCE,
            original_identifier=JULY,
            source_version=adapter.source_version(retrieved),
            payload_kind="data",
            retrieved_at=RETRIEVED_AT + timedelta(days=2),
            media_type=retrieved.media_type,
            valid_from=remote.valid_from,
            valid_to=remote.valid_to,
        ),
        retrieved.chunks,
    )
    return published.raw_path


# --- what stops a load -----------------------------------------------------


def test_a_normaliser_that_disagrees_with_itself_stops_the_artefact(
    rollback: Any, store: RawStore
) -> None:
    """And rolls the whole artefact back, because a partial month is read as a fact."""
    path = fetch(store, JULY)
    register_source(rollback, REFERENCE_SOURCE)
    load_artefact(rollback, store, ReferenceNormaliser(), path)
    before = _counts(rollback)

    with pytest.raises(NormalisationDisagreementError):
        load_artefact(rollback, store, _Nondeterministic(), path)

    assert _counts(rollback) == before


class _Nondeterministic:
    """A normaliser that produces a different body under the same version.

    The bug the stage exists to catch, made to happen on purpose — without it
    the refusal in ``insert_archive_observation`` is never reached from here.
    """

    transformation_version = TRANSFORMATION_VERSION

    def normalise(self, artefact: Any) -> Any:
        import dataclasses

        batch = ReferenceNormaliser().normalise(artefact)
        return dataclasses.replace(
            batch,
            receptions=tuple(
                dataclasses.replace(one, mode="changed") for one in batch.receptions
            ),
        )
