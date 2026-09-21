"""The reference adapter, exercised the way the fetch path will exercise it.

The adapter plans names, a :class:`FixtureRetriever` turns them into bytes, the
raw store publishes them, and the normaliser reads them back from disk. That
whole sequence runs here with nothing reachable, which is the shape Stage 14's
completion gate takes — Part 13 adds the socket guard that proves it rather
than assumes it.

Everything the adapter serves is synthetic and ours (D-142). The station names,
coordinates and frame counts describe nothing that happened.

Reference: docs/DECISIONS.md D-133, D-134, D-136, D-139, D-140, D-142.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from meridian_ingest.adapters import (
    REGISTRY,
    UnknownSourceError,
    adapter_for,
    normaliser_for,
    registered_sources,
)
from meridian_ingest.adapters.protocol import (
    Adapter,
    FetchRequest,
    Normaliser,
    TileIsNotAQuantityError,
)
from meridian_ingest.adapters.reference import (
    FIXTURE_ROOT,
    REFERENCE_SOURCE,
    TRANSFORMATION_VERSION,
    ReferenceAdapter,
    ReferenceNormaliser,
)
from meridian_ingest.normalise.records import NormalisationError
from meridian_ingest.provenance import Provenance
from meridian_ingest.raw_store import RawStore, StoredArtefact
from meridian_ingest.retrieval import (
    FixtureRetriever,
    RemoteArtefact,
    RetrievalError,
)

SOURCE = "reference_archive"
RETRIEVED_AT = datetime(2026, 9, 20, 10, 11, 43, tzinfo=UTC)
JULY = "receptions-2026-07.json"
AUGUST = "receptions-2026-08.json"
TILE = "coverage-2026-08.png"
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def store(tmp_path: Path) -> Iterator[RawStore]:
    """A raw store under ``tmp_path``, left writable so the tree can be removed."""
    root = tmp_path / "raw"
    yield RawStore(root)
    for path in sorted(root.rglob("*"), reverse=True):
        path.chmod(0o700 if path.is_dir() else 0o600)


@pytest.fixture
def retriever() -> FixtureRetriever:
    return FixtureRetriever(FIXTURE_ROOT)


def publish(store: RawStore, retriever: FixtureRetriever, name: str) -> StoredArtefact:
    """Fetch and store one artefact exactly as the fetch path will."""
    adapter = ReferenceAdapter()
    remote = next(
        one for one in adapter.plan(FetchRequest()) if one.original_identifier == name
    )
    retrieved = retriever.retrieve(remote)
    published = store.publish(
        Provenance(
            source_id=SOURCE,
            original_identifier=remote.original_identifier,
            source_version=adapter.source_version(retrieved),
            payload_kind=remote.payload_kind,
            retrieved_at=RETRIEVED_AT,
            media_type=retrieved.media_type,
            valid_from=remote.valid_from,
            valid_to=remote.valid_to,
        ),
        retrieved.chunks,
    )
    return store.read(published.raw_path)


# --- the registry ----------------------------------------------------------


def test_the_reference_source_is_registered_under_its_own_declared_id() -> None:
    """The key and the descriptor cannot disagree, because one builds the other.

    A disagreement would publish artefacts into one source's directory in the
    raw store and record them against another's row.
    """
    assert set(REGISTRY) == {SOURCE}
    assert adapter_for(SOURCE).descriptor.source_id == SOURCE
    assert registered_sources() == (REFERENCE_SOURCE,)


def test_an_unregistered_source_is_refused_by_name() -> None:
    with pytest.raises(UnknownSourceError, match="registered: reference_archive"):
        adapter_for("some_real_archive")


def test_the_registered_pair_satisfies_both_protocols() -> None:
    assert isinstance(adapter_for(SOURCE), Adapter)
    assert isinstance(normaliser_for(SOURCE), Normaliser)


def test_the_source_carries_the_terms_it_is_published_under() -> None:
    """D-134: no source is registered without its licence and terms recorded."""
    assert REFERENCE_SOURCE.licence == "Apache-2.0"
    assert REFERENCE_SOURCE.terms_url.startswith("https://")
    assert REFERENCE_SOURCE.attribution_entry


def test_the_attribution_entry_exists_in_the_file_it_names() -> None:
    """Otherwise the entry is a string nobody ever has to make true.

    ``fetch`` will refuse before opening a socket on exactly this check; this
    test is what keeps the reference source able to pass it.
    """
    attribution = (REPO_ROOT / "ATTRIBUTION.md").read_text(encoding="utf-8")

    assert REFERENCE_SOURCE.attribution_entry in attribution


# --- planning --------------------------------------------------------------


def test_planning_names_artefacts_and_opens_nothing() -> None:
    planned = ReferenceAdapter().plan(FetchRequest())

    assert [one.original_identifier for one in planned] == [JULY, AUGUST, TILE]
    assert all(one.url.startswith("https://") for one in planned)


def test_every_planned_url_is_unresolvable_by_construction() -> None:
    """``.invalid`` is reserved. A real retriever pointed here still reaches nothing."""
    assert all(
        one.url.split("/")[2].endswith(".invalid")
        for one in ReferenceAdapter().plan(FetchRequest())
    )


def test_an_artefact_is_planned_when_its_month_overlaps_the_request() -> None:
    """Overlap, not containment: one day in August still wants August's file."""
    planned = ReferenceAdapter().plan(
        FetchRequest(
            since=datetime(2026, 8, 15, tzinfo=UTC),
            until=datetime(2026, 8, 16, tzinfo=UTC),
        )
    )

    assert [one.original_identifier for one in planned] == [AUGUST, TILE]


def test_a_request_outside_the_catalogue_plans_nothing() -> None:
    assert (
        ReferenceAdapter().plan(FetchRequest(since=datetime(2027, 1, 1, tzinfo=UTC)))
        == ()
    )


def test_the_ceiling_is_the_operators_and_is_obeyed() -> None:
    planned = ReferenceAdapter().plan(FetchRequest(limit=1))

    assert len(planned) == 1


def test_a_planned_artefact_carries_the_month_it_is_expected_to_cover() -> None:
    """Which reaches ``ingest_records.valid_from``, where a lookup selects on it."""
    july = ReferenceAdapter().plan(FetchRequest())[0]

    assert july.valid_from == datetime(2026, 7, 1, tzinfo=UTC)
    assert july.valid_to == datetime(2026, 8, 1, tzinfo=UTC)


def test_the_adapter_plans_against_whatever_it_is_pointed_at() -> None:
    planned = ReferenceAdapter(base_url="https://elsewhere.invalid/x/").plan(
        FetchRequest(limit=1)
    )

    assert planned[0].url == f"https://elsewhere.invalid/x/{JULY}"


# --- the fixture retriever -------------------------------------------------


def test_a_fixture_is_served_with_a_version_taken_from_its_content(
    retriever: FixtureRetriever,
) -> None:
    """An unchanged fixture re-fetches to the same version, as a real ETag would."""
    remote = ReferenceAdapter().plan(FetchRequest(limit=1))[0]

    first = ReferenceAdapter().source_version(retriever.retrieve(remote))
    second = ReferenceAdapter().source_version(retriever.retrieve(remote))

    assert first == second
    assert first.startswith('"')


def test_two_different_fixtures_have_different_versions(
    retriever: FixtureRetriever,
) -> None:
    planned = ReferenceAdapter().plan(FetchRequest())
    versions = {
        ReferenceAdapter().source_version(retriever.retrieve(one)) for one in planned
    }

    assert len(versions) == len(planned)


def test_a_url_that_is_not_a_fixture_name_is_refused(
    retriever: FixtureRetriever,
) -> None:
    """A fixture directory is flat: a separator in the last element is not a miss."""
    with pytest.raises(RetrievalError, match="does not name a fixture"):
        retriever.retrieve(
            RemoteArtefact(
                url="https://reference-archive.invalid/v1/.hidden",
                original_identifier="x",
                payload_kind="data",
            )
        )


def test_a_missing_fixture_says_so(tmp_path: Path) -> None:
    empty = FixtureRetriever(tmp_path)

    with pytest.raises(RetrievalError, match="no fixture at"):
        empty.retrieve(ReferenceAdapter().plan(FetchRequest(limit=1))[0])


def test_a_fixture_whose_type_nobody_declared_is_refused(tmp_path: Path) -> None:
    """Guessing a media type stores a value as though a source had said it."""
    (tmp_path / "mystery.bin").write_bytes(b"...")

    with pytest.raises(RetrievalError, match="no declared media type"):
        FixtureRetriever(tmp_path).retrieve(
            RemoteArtefact(
                url="https://reference-archive.invalid/v1/mystery.bin",
                original_identifier="mystery.bin",
                payload_kind="data",
            )
        )


# --- normalising, from the raw store ---------------------------------------


def test_a_month_normalises_to_the_stations_and_receptions_it_describes(
    store: RawStore, retriever: FixtureRetriever
) -> None:
    batch = ReferenceNormaliser().normalise(publish(store, retriever, JULY))

    assert batch.transformation_version == TRANSFORMATION_VERSION
    assert [station.source_station_key for station in batch.stations] == [
        "gs-dublin",
        "gs-galway",
    ]
    assert [one.source_observation_id for one in batch.receptions] == [
        "obs-2607-001",
        "obs-2607-002",
        "obs-2607-003",
    ]


def test_the_archives_own_word_is_kept_beside_the_one_we_mapped_it_to(
    store: RawStore, retriever: FixtureRetriever
) -> None:
    """So somebody who doubts the mapping can check it against the artefact."""
    batch = ReferenceNormaliser().normalise(publish(store, retriever, JULY))
    partial = batch.receptions[1]

    assert partial.archive_outcome == "signal_no_decode"
    assert partial.source_outcome == "partial"


def test_nothing_heard_becomes_no_data_and_never_no_signal(
    store: RawStore, retriever: FixtureRetriever
) -> None:
    """``no_signal`` would claim listening evidence no archive can give us."""
    batch = ReferenceNormaliser().normalise(publish(store, retriever, JULY))
    outcomes = {one.archive_outcome for one in batch.receptions}

    assert "no_data" in outcomes
    assert "no_signal" not in outcomes


def test_a_result_the_mapping_does_not_know_becomes_unknown(
    store: RawStore, retriever: FixtureRetriever
) -> None:
    """Kept verbatim, so the gap in the mapping is visible rather than guessed at."""
    batch = ReferenceNormaliser().normalise(publish(store, retriever, AUGUST))
    offline = next(
        one for one in batch.receptions if one.source_observation_id == "obs-2608-003"
    )

    assert offline.archive_outcome == "unknown"
    assert offline.source_outcome == "receiver offline"


def test_an_object_the_archive_could_not_resolve_keeps_the_name_it_gave(
    store: RawStore, retriever: FixtureRetriever
) -> None:
    """Dropping it would stack a second selection filter on the archive's own."""
    batch = ReferenceNormaliser().normalise(publish(store, retriever, JULY))
    unresolved = batch.receptions[2]

    assert unresolved.satellite_key == "name:UNKNOWN-OBJECT-7"
    assert unresolved.satellite_key_kind == "source_name"


def test_each_kind_of_identifier_is_namespaced(
    store: RawStore, retriever: FixtureRetriever
) -> None:
    """So two archives' identifier spaces cannot collide in one text column."""
    batch = ReferenceNormaliser().normalise(publish(store, retriever, AUGUST))
    keys = {one.satellite_key: one.satellite_key_kind for one in batch.receptions}

    assert keys["norad:57166"] == "norad"
    assert keys["intl:2023-091A"] == "international_designator"


def test_a_reception_with_no_station_named_is_kept(
    store: RawStore, retriever: FixtureRetriever
) -> None:
    batch = ReferenceNormaliser().normalise(publish(store, retriever, AUGUST))
    unattributed = next(
        one for one in batch.receptions if one.source_observation_id == "obs-2608-004"
    )

    assert unattributed.source_station_key is None


def test_a_station_with_no_published_location_is_kept(
    store: RawStore, retriever: FixtureRetriever
) -> None:
    """Stage 16 counts and publishes these rather than dropping them."""
    batch = ReferenceNormaliser().normalise(publish(store, retriever, AUGUST))
    cork = next(one for one in batch.stations if one.source_station_key == "gs-cork")

    assert cork.lat_deg is None
    assert cork.capability == {"band": "vhf", "modes": ["lrpt"]}


def test_an_unmoved_station_hashes_the_same_across_two_months(
    store: RawStore, retriever: FixtureRetriever
) -> None:
    """Which is what makes August's load find the row July already wrote."""
    july = ReferenceNormaliser().normalise(publish(store, retriever, JULY))
    august = ReferenceNormaliser().normalise(publish(store, retriever, AUGUST))

    assert july.stations[0].content_sha256 == august.stations[0].content_sha256


def test_a_moved_station_hashes_differently(
    store: RawStore, retriever: FixtureRetriever
) -> None:
    """So it becomes a new row and July's denominator stays explainable."""
    july = ReferenceNormaliser().normalise(publish(store, retriever, JULY))
    august = ReferenceNormaliser().normalise(publish(store, retriever, AUGUST))
    galway = {"gs-galway"}

    before = next(s for s in july.stations if s.source_station_key in galway)
    after = next(s for s in august.stations if s.source_station_key in galway)

    assert before.content_sha256 != after.content_sha256


def test_normalising_twice_produces_identical_digests(
    store: RawStore, retriever: FixtureRetriever
) -> None:
    """The completion gate in miniature, before the socket guard is added."""
    artefact = publish(store, retriever, AUGUST)

    first = ReferenceNormaliser().normalise(artefact)
    second = ReferenceNormaliser().normalise(artefact)

    assert [one.content_sha256 for one in first.receptions] == [
        one.content_sha256 for one in second.receptions
    ]
    assert first == second


def test_a_tile_is_refused_before_anything_is_derived_from_it(
    store: RawStore, retriever: FixtureRetriever
) -> None:
    """D-133, exercised against a real raster rather than a hypothetical one."""
    tile = publish(store, retriever, TILE)

    assert tile.manifest.provenance.media_type == "image/png"
    with pytest.raises(TileIsNotAQuantityError, match="picture of a measurement"):
        ReferenceNormaliser().normalise(tile)


def test_an_artefact_of_another_format_is_refused(
    store: RawStore, retriever: FixtureRetriever
) -> None:
    """A normaliser is chosen by the source, so this is a wiring mistake."""
    artefact = publish(store, retriever, JULY)
    stranger = StoredArtefact(
        raw_path=artefact.raw_path,
        manifest=artefact.manifest,
        artefact=artefact.artefact,
    )
    path = store.root / artefact.raw_path / "artefact.bin"
    path.chmod(0o600)
    path.write_text(json.dumps({"schema": "somebody-else/2"}), encoding="utf-8")

    with pytest.raises(NormalisationError, match="is not reference-archive/1"):
        ReferenceNormaliser().normalise(stranger)


def test_bytes_that_are_not_json_are_refused(
    store: RawStore, retriever: FixtureRetriever
) -> None:
    artefact = publish(store, retriever, JULY)
    path = store.root / artefact.raw_path / "artefact.bin"
    path.chmod(0o600)
    path.write_bytes(b"\xff\xfe not json")

    with pytest.raises(NormalisationError, match="not readable JSON"):
        ReferenceNormaliser().normalise(store.read(artefact.raw_path))


# --- the fixtures themselves -----------------------------------------------


def test_every_catalogued_artefact_ships_with_the_package() -> None:
    """The reference source is fetchable by anyone who installs it, not only here."""
    for name in (JULY, AUGUST, TILE):
        assert (FIXTURE_ROOT / name).is_file()


def test_the_fixture_directory_says_the_fixtures_are_invented() -> None:
    """Somebody will find this tree without reading ATTRIBUTION.md first."""
    readme = (FIXTURE_ROOT / "README.md").read_text(encoding="utf-8")

    assert "invented" in readme
    assert "D-136" in readme
