"""Stage 34's element-set source, against synthetic fixtures in CelesTrak's format.

What it plans and refuses to plan, what a response normalises to, and that a
set the platform's own reader would refuse is refused here first. No network:
every fixture is a file we wrote, served by a retriever that reads it (D-142).

Reference: docs/DECISIONS.md D-138, D-142, D-320.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from meridian_ingest.adapters import REGISTRY
from meridian_ingest.adapters.protocol import FetchRequest
from meridian_ingest.adapters.public.celestrak import (
    CELESTRAK_GP,
    ELEMENT_SET_SOURCES,
)
from meridian_ingest.config import ConfigurationError, load_settings
from meridian_ingest.normalise.records import (
    NormalisationError,
    NormalisedBatch,
    NormalisedElementSet,
)
from meridian_ingest.provenance import Provenance
from meridian_ingest.raw_store import RawStore, StoredArtefact
from meridian_ingest.retrieval import MappedFixtureRetriever, RemoteArtefact

FIXTURES = Path(__file__).parent / "public_fixtures"
SOURCE = CELESTRAK_GP.source_id
RETRIEVED_AT = datetime(2026, 11, 2, 14, 0, tzinfo=UTC)

METEOR_M2_3 = (
    "1 57166U 23091A   26306.50000000 -.00000020  00000+0  50000-5 0  9994",
    "2 57166  98.5900 358.5000 0004900 120.0000 240.1000 14.24055000174151",
)


@pytest.fixture
def store(tmp_path: Path) -> RawStore:
    return RawStore(tmp_path / "raw")


def plan(**request: object) -> tuple[RemoteArtefact, ...]:
    return REGISTRY[SOURCE].adapter.plan(FetchRequest(**request))  # type: ignore[arg-type]


def publish(store: RawStore, remote: RemoteArtefact, file: Path) -> StoredArtefact:
    adapter = REGISTRY[SOURCE].adapter
    retrieved = MappedFixtureRetriever({remote.url: file}).retrieve(remote)
    published = store.publish(
        Provenance(
            source_id=SOURCE,
            original_identifier=remote.original_identifier,
            source_version=adapter.source_version(retrieved),
            payload_kind=remote.payload_kind,
            retrieved_at=RETRIEVED_AT,
            media_type=retrieved.media_type,
        ),
        retrieved.chunks,
    )
    return store.read(published.raw_path)


def normalise(artefact: StoredArtefact) -> NormalisedBatch:
    return REGISTRY[SOURCE].normaliser.normalise(artefact)


# --- the source --------------------------------------------------------------


def test_it_is_registered_off_and_credited_before_any_retrieval() -> None:
    registration = REGISTRY[SOURCE]
    assert not registration.enabled_by_default
    assert registration.adapter.descriptor.source_class == "orbital_elements"
    attribution = (Path(__file__).parents[2] / "ATTRIBUTION.md").read_text("utf-8")
    assert CELESTRAK_GP.attribution_entry in attribution


def test_it_is_followed_no_more_often_than_the_catalogue_updates() -> None:
    """CelesTrak updates every two hours and refuses a second download of one."""
    assert REGISTRY[SOURCE].cadence_s >= 2 * 3_600


def test_every_set_it_loads_is_recorded_as_celestraks() -> None:
    assert ELEMENT_SET_SOURCES == {SOURCE: "celestrak"}


# --- planning ----------------------------------------------------------------


def test_a_group_is_one_request_and_a_number_is_one_each() -> None:
    planned = plan(groups=("weather",), catalog_numbers=(57166, 59051))
    assert [one.original_identifier for one in planned] == [
        "GROUP=weather",
        "CATNR=57166",
        "CATNR=59051",
    ]
    assert planned[0].url == (
        "https://celestrak.org/NORAD/elements/gp.php?GROUP=weather&FORMAT=TLE"
    )
    assert all(one.payload_kind == "data" for one in planned)


def test_a_fetch_that_names_no_object_is_refused_by_name() -> None:
    with pytest.raises(ValueError, match=r"groups or catalog_numbers"):
        plan()


def test_a_group_name_carrying_a_query_is_refused() -> None:
    with pytest.raises(ValueError, match="not a CelesTrak group name"):
        plan(groups=("weather&GROUP=active",))


def test_more_queries_than_the_budget_is_refused_not_cut_short() -> None:
    with pytest.raises(ValueError, match="limit is 2"):
        plan(catalog_numbers=(1, 2, 3), limit=2)


# --- normalising -------------------------------------------------------------


def test_a_group_normalises_to_one_set_per_object_with_its_epoch(
    store: RawStore,
) -> None:
    remote = only(plan(groups=("weather",)))
    batch = normalise(publish(store, remote, FIXTURES / "celestrak-weather.tle"))
    assert batch.transformation_version == "celestrak-tle-1"
    assert [(one.satellite_key, one.name) for one in batch.element_sets] == [
        ("norad:57166", "METEOR-M2 3"),
        ("norad:59051", "METEOR-M2 4"),
        ("norad:33591", "NOAA 19"),
    ]
    assert batch.element_sets[0].epoch == datetime(2026, 11, 2, 12, tzinfo=UTC)
    assert batch.element_sets[0].line1 == METEOR_M2_3[0]
    assert not batch.receptions
    assert not batch.samples


def test_no_data_for_a_number_is_an_empty_batch_not_an_error(
    store: RawStore,
) -> None:
    remote = only(plan(catalog_numbers=(99999,)))
    batch = normalise(publish(store, remote, FIXTURES / "celestrak-no-data.txt"))
    assert batch.element_sets == ()


def test_normalising_twice_gives_the_same_sets(store: RawStore) -> None:
    artefact = publish(
        store, only(plan(groups=("weather",))), FIXTURES / "celestrak-weather.tle"
    )
    assert normalise(artefact) == normalise(artefact)


def test_text_that_is_not_element_sets_is_refused(
    store: RawStore, tmp_path: Path
) -> None:
    page = tmp_path / "error.txt"
    page.write_text("<html>403 Forbidden</html>\n", "ascii")
    artefact = publish(store, only(plan(groups=("weather",))), page)
    with pytest.raises(NormalisationError, match="does not start an element set"):
        normalise(artefact)


# --- one element set ---------------------------------------------------------


def test_a_set_carries_the_epoch_its_own_lines_state() -> None:
    one = NormalisedElementSet("norad:57166", *METEOR_M2_3)
    assert one.epoch == datetime(2026, 11, 2, 12, tzinfo=UTC)


def test_a_set_with_a_broken_checksum_is_refused() -> None:
    broken = METEOR_M2_3[0][:-1] + "0"
    with pytest.raises(NormalisationError, match="not a readable element set"):
        NormalisedElementSet("norad:57166", broken, METEOR_M2_3[1])


def test_a_set_filed_under_another_object_is_refused() -> None:
    with pytest.raises(NormalisationError, match="not 59051"):
        NormalisedElementSet("norad:59051", *METEOR_M2_3)


def test_a_key_that_is_not_a_catalogue_number_is_refused() -> None:
    with pytest.raises(NormalisationError, match="norad:<catalogue number>"):
        NormalisedElementSet("METEOR-M2 3", *METEOR_M2_3)


def test_one_artefact_listing_an_object_twice_is_refused() -> None:
    one = NormalisedElementSet("norad:57166", *METEOR_M2_3)
    with pytest.raises(NormalisationError, match="same element set"):
        NormalisedBatch(transformation_version="v", element_sets=(one, one))


# --- settings ----------------------------------------------------------------


def test_groups_and_numbers_come_from_the_settings_file(tmp_path: Path) -> None:
    path = tmp_path / "ingest.toml"
    path.write_text(
        f'[sources.{SOURCE}]\nenabled = true\ngroups = ["weather"]\n'
        "catalog_numbers = [57166]\n",
        "utf-8",
    )
    places = load_settings(path, {}).for_source(SOURCE).places
    assert places.groups == ("weather",)
    assert places.catalog_numbers == (57166,)


@pytest.mark.parametrize("value", ['["57166"]', "[0]", "[true]", '"57166"'])
def test_a_catalogue_number_that_is_not_a_positive_whole_number_is_refused(
    tmp_path: Path, value: str
) -> None:
    path = tmp_path / "ingest.toml"
    path.write_text(f"[sources.{SOURCE}]\ncatalog_numbers = {value}\n", "utf-8")
    with pytest.raises(ConfigurationError, match="catalog_numbers"):
        load_settings(path, {})


def only(planned: tuple[RemoteArtefact, ...]) -> RemoteArtefact:
    assert len(planned) == 1
    return planned[0]
