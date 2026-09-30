"""Stage 31's nine sources, each against a synthetic fixture in its provider's format.

For every source: what it plans (and that a key or a place never leaks into
anything printed or stored), what its fixture normalises to, and that a
missing value stays missing. The night-lights granule is written here with
``h5py`` rather than committed, because its layout reads better as code.

No network: every fixture is served by a retriever that reads a file, and the
URLs are the ones a real fetch would ask for (D-142).

Reference: docs/DECISIONS.md D-133, D-142, D-220 to D-223, D-226.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import h5py
import numpy as np
import pytest

from meridian_ingest.adapters import REGISTRY
from meridian_ingest.adapters.protocol import (
    Adapter,
    FetchRequest,
    Normaliser,
    TileIsNotAQuantityError,
)
from meridian_ingest.adapters.public.black_marble import tile_box
from meridian_ingest.adapters.public.black_marble_grid import DATASET
from meridian_ingest.adapters.public.gibs import tiles_covering
from meridian_ingest.adapters.public.ornl_ndvi import sinusoidal_to_geographic
from meridian_ingest.extent import BoundingBox, GeoPoint
from meridian_ingest.normalise.records import NormalisationError, NormalisedBatch
from meridian_ingest.normalise.samples import NormalisedSample
from meridian_ingest.provenance import Provenance
from meridian_ingest.raw_store import RawStore, StoredArtefact
from meridian_ingest.retrieval import (
    KEY_PLACEHOLDER,
    MappedFixtureRetriever,
    RemoteArtefact,
)

FIXTURES = Path(__file__).parent / "public_fixtures"
RETRIEVED_AT = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
BENGALURU = GeoPoint(12.971598, 77.594566)
KARNATAKA = BoundingBox(74.0, 11.5, 78.6, 18.5)
SINCE = datetime(2026, 9, 15, tzinfo=UTC)
UNTIL = datetime(2026, 9, 29, tzinfo=UTC)


def publish(
    store: RawStore,
    source_id: str,
    remote: RemoteArtefact,
    file: Path,
    retrieved_at: datetime = RETRIEVED_AT,
) -> StoredArtefact:
    """One planned artefact, served from ``file`` and published as a fetch would."""
    adapter = REGISTRY[source_id].adapter
    retrieved = MappedFixtureRetriever({remote.url: file}).retrieve(remote)
    published = store.publish(
        Provenance(
            source_id=source_id,
            original_identifier=remote.original_identifier,
            source_version=adapter.source_version(retrieved),
            payload_kind=remote.payload_kind,
            retrieved_at=retrieved_at,
            media_type=retrieved.media_type,
            valid_from=remote.valid_from,
            valid_to=remote.valid_to,
            spatial_extent=remote.spatial_extent,
        ),
        retrieved.chunks,
    )
    return store.read(published.raw_path)


def normalise(source_id: str, artefact: StoredArtefact) -> NormalisedBatch:
    return REGISTRY[source_id].normaliser.normalise(artefact)


def plan(source_id: str, **request: object) -> tuple[RemoteArtefact, ...]:
    return REGISTRY[source_id].adapter.plan(FetchRequest(**request))  # type: ignore[arg-type]


def only(planned: tuple[RemoteArtefact, ...]) -> RemoteArtefact:
    assert len(planned) == 1
    return planned[0]


@pytest.fixture
def store(tmp_path: Path) -> RawStore:
    return RawStore(tmp_path / "raw")


# --- every source ------------------------------------------------------------

PUBLIC = sorted(set(REGISTRY) - {"reference_archive"})


def test_nine_classes_are_registered_each_with_both_halves() -> None:
    assert len(PUBLIC) == 9
    for source_id in PUBLIC:
        registration = REGISTRY[source_id]
        assert isinstance(registration.adapter, Adapter)
        assert isinstance(registration.normaliser, Normaliser)


def test_no_real_source_is_fetched_unless_an_operator_asks() -> None:
    """With no settings file, `fetch` still reaches only the reference archive."""
    for source_id in PUBLIC:
        assert not REGISTRY[source_id].enabled_by_default


def test_every_source_is_credited_before_anything_is_retrieved() -> None:
    attribution = (Path(__file__).parents[2] / "ATTRIBUTION.md").read_text("utf-8")
    for source_id in PUBLIC:
        entry = REGISTRY[source_id].adapter.descriptor.attribution_entry
        assert entry in attribution, source_id


def test_a_source_with_a_key_names_the_variable_it_is_read_from() -> None:
    for source_id in PUBLIC:
        registration = REGISTRY[source_id]
        needs = registration.adapter.descriptor.access_constraint != "none"
        assert needs == (registration.key_env is not None), source_id


# --- space weather -----------------------------------------------------------


def test_kp_is_one_sample_per_three_hours_with_its_gap_kept(store: RawStore) -> None:
    remote = only(plan("noaa_swpc_kp"))
    batch = normalise(
        "noaa_swpc_kp",
        publish(store, "noaa_swpc_kp", remote, FIXTURES / "swpc-kp-2026-09-28.json"),
    )
    assert len(batch.samples) == 16
    first = batch.samples[0]
    assert first.quantity == "kp_index"
    assert first.value == 1.33
    assert first.observed_to - first.observed_from == timedelta(hours=3)
    assert first.published_at == RETRIEVED_AT
    assert first.published_basis == "retrieved"
    gap = batch.samples[10]
    assert gap.value is None
    assert gap.missing_reason == "published without a value"


def test_kp_reads_the_legacy_shape_too(store: RawStore) -> None:
    remote = only(plan("noaa_swpc_kp"))
    batch = normalise(
        "noaa_swpc_kp",
        publish(store, "noaa_swpc_kp", remote, FIXTURES / "swpc-kp-legacy.json"),
    )
    assert [one.value for one in batch.samples] == [1.33, 2.0, 2.67, 3.0]
    assert batch.samples[0].observed_from == datetime(2026, 9, 27, tzinfo=UTC)


def test_a_revised_kp_is_a_second_row_published_later(store: RawStore) -> None:
    """D-222: a revision never replaces the value a pass could have read."""
    remote = only(plan("noaa_swpc_kp"))
    earlier = normalise(
        "noaa_swpc_kp",
        publish(store, "noaa_swpc_kp", remote, FIXTURES / "swpc-kp-2026-09-28.json"),
    )
    later = normalise(
        "noaa_swpc_kp",
        publish(
            store,
            "noaa_swpc_kp",
            remote,
            FIXTURES / "swpc-kp-2026-09-29.json",
            RETRIEVED_AT + timedelta(days=1),
        ),
    )
    assert len(store.scan("noaa_swpc_kp")) == 2
    before = {one.series_key: one for one in earlier.samples}
    after = {one.series_key: one for one in later.samples}
    revised = "kp:2026-09-28T12:00:00+00:00"
    assert before[revised].value == 1.0
    assert after[revised].value == 1.67
    assert after[revised].published_at > before[revised].published_at


# --- atmosphere and aerosol --------------------------------------------------


@pytest.mark.parametrize(
    ("source_id", "file", "quantity", "unit"),
    [
        ("open_meteo_cloud", "open-meteo-cloud.json", "cloud_cover", "%"),
        ("open_meteo_aerosol", "open-meteo-aerosol.json", "aerosol_optical_depth", "1"),
    ],
)
def test_an_hourly_series_is_one_sample_an_hour_at_the_answering_cell(
    store: RawStore, source_id: str, file: str, quantity: str, unit: str
) -> None:
    remote = only(plan(source_id, points=(BENGALURU,)))
    batch = normalise(source_id, publish(store, source_id, remote, FIXTURES / file))
    assert len(batch.samples) == 48
    assert {one.quantity for one in batch.samples} == {quantity}
    assert {one.value_unit for one in batch.samples} == {unit}
    assert {(one.lat_deg, one.lon_deg) for one in batch.samples} == {(12.98, 77.6)}
    missing = [one for one in batch.samples if one.value is None]
    assert len(missing) == 1
    assert missing[0].missing_reason == "null in the response"


def test_a_point_is_rounded_before_it_is_sent() -> None:
    """D-220: a station's exact position never reaches a third party's logs."""
    remote = only(plan("open_meteo_cloud", points=(BENGALURU,)))
    assert "latitude=12.97&longitude=77.59" in remote.url
    assert "12.971598" not in remote.url
    assert "12.971598" not in remote.original_identifier


def test_a_point_product_with_no_point_is_refused_by_name() -> None:
    with pytest.raises(ValueError, match=r"\[sources.open_meteo_cloud\] points"):
        plan("open_meteo_cloud")


# --- imagery, displayed only -------------------------------------------------


@pytest.mark.parametrize(
    ("source_id", "file", "layers"),
    [
        ("nasa_gibs", "gibs-tile.jpg", ()),
        ("isro_bhuvan", "bhuvan-map.png", ("lulc:KA_LULC50K_1516",)),
    ],
)
def test_imagery_is_stored_as_tiles_and_never_read_for_a_number(
    store: RawStore, source_id: str, file: str, layers: tuple[str, ...]
) -> None:
    """D-133: both locks — the tile kind, and a normaliser that only refuses."""
    planned = plan(source_id, bbox=KARNATAKA, layers=layers)
    assert planned
    assert {one.payload_kind for one in planned} == {"tile"}
    artefact = publish(store, source_id, planned[0], FIXTURES / file)
    with pytest.raises(TileIsNotAQuantityError):
        normalise(source_id, artefact)


def test_a_display_only_source_refuses_even_an_artefact_labelled_data(
    store: RawStore,
) -> None:
    """What may be derived is the source's to decide, not a label's (D-220)."""
    remote = RemoteArtefact(
        url="https://bhuvan-vec2.nrsc.gov.in/bhuvan/wms?x",
        original_identifier="x",
        payload_kind="data",
    )
    artefact = publish(store, "isro_bhuvan", remote, FIXTURES / "bhuvan-map.png")
    with pytest.raises(TileIsNotAQuantityError, match="display-only"):
        normalise("isro_bhuvan", artefact)


def test_bhuvan_has_no_default_layer() -> None:
    with pytest.raises(ValueError, match="layers"):
        plan("isro_bhuvan", bbox=KARNATAKA)


def test_gibs_tiles_cover_the_box_and_nothing_else() -> None:
    tiles = tiles_covering(KARNATAKA, 6)
    assert tiles == [(15, 56), (15, 57), (16, 56), (16, 57), (17, 56), (17, 57)]
    days = plan(
        "nasa_gibs", bbox=KARNATAKA, since=SINCE, until=SINCE + timedelta(days=2)
    )
    assert len(days) == 12
    assert all("/2026-09-1" in one.url for one in days)


# --- fires -------------------------------------------------------------------


def test_the_firms_key_is_a_placeholder_until_the_socket() -> None:
    planned = plan("nasa_firms", bbox=KARNATAKA, since=SINCE, until=UNTIL)
    assert len(planned) == 14
    first = planned[0]
    assert KEY_PLACEHOLDER in first.url
    assert KEY_PLACEHOLDER not in first.original_identifier
    assert first.needs_key
    assert first.valid_from == SINCE
    assert first.valid_to == SINCE + timedelta(days=1)
    assert first.spatial_extent == {
        "west": 74.0,
        "south": 11.5,
        "east": 78.6,
        "north": 18.5,
    }


def test_detections_are_points_with_their_power_and_confidence(
    store: RawStore,
) -> None:
    remote = plan("nasa_firms", bbox=KARNATAKA, since=SINCE, until=UNTIL)[-1]
    batch = normalise(
        "nasa_firms",
        publish(store, "nasa_firms", remote, FIXTURES / "firms-2026-09-28.csv"),
    )
    assert len(batch.samples) == 4
    first = batch.samples[0]
    assert (first.lat_deg, first.lon_deg, first.value) == (12.41239, 76.90112, 4.7)
    assert first.observed_from == datetime(2026, 9, 28, 7, 52, tzinfo=UTC)
    assert first.quality == "confidence=n;daynight=D"
    assert first.product == "VIIRS_SNPP_NRT 2.0NRT"
    assert first.footprint_m == pytest.approx(374.6, abs=0.1)
    unpadded = batch.samples[3]
    assert unpadded.observed_from == datetime(2026, 9, 28, 7, 54, tzinfo=UTC)
    assert unpadded.value is None


def test_a_day_with_no_detections_is_an_empty_batch_not_an_error(
    store: RawStore,
) -> None:
    """Zero fires on a fetched day is a finding; the record says the day was asked."""
    remote = plan("nasa_firms", bbox=KARNATAKA, since=SINCE, until=UNTIL)[0]
    artefact = publish(store, "nasa_firms", remote, FIXTURES / "firms-empty.csv")
    assert normalise("nasa_firms", artefact).samples == ()
    assert artefact.manifest.provenance.valid_from == SINCE


# --- vegetation and precipitation --------------------------------------------


def test_ndvi_pixels_are_placed_by_the_sinusoidal_inverse(store: RawStore) -> None:
    remote = only(
        plan("ornl_modis_ndvi", points=(BENGALURU,), since=SINCE, until=UNTIL)
    )
    batch = normalise(
        "ornl_modis_ndvi",
        publish(store, "ornl_modis_ndvi", remote, FIXTURES / "ornl-ndvi.json"),
    )
    assert len(batch.samples) == 162
    centre = next(one for one in batch.samples if one.series_key == "A2026225:4,4")
    assert centre.lat_deg == pytest.approx(12.97, abs=1e-6)
    assert centre.lon_deg == pytest.approx(77.59, abs=1e-6)
    assert centre.value is not None
    assert -0.2 <= centre.value <= 1.0
    assert centre.observed_to - centre.observed_from == timedelta(days=16)
    filled = next(one for one in batch.samples if one.series_key == "A2026241:4,4")
    assert filled.value is None
    assert filled.missing_reason == "fill value -3000"


def test_a_composite_is_published_when_it_was_produced(store: RawStore) -> None:
    """D-222: a declared production time earlier than our fetch is used."""
    remote = only(
        plan("ornl_modis_ndvi", points=(BENGALURU,), since=SINCE, until=UNTIL)
    )
    batch = normalise(
        "ornl_modis_ndvi",
        publish(store, "ornl_modis_ndvi", remote, FIXTURES / "ornl-ndvi.json"),
    )
    first = batch.samples[0]
    assert first.published_basis == "source_declared"
    assert first.published_at == datetime(2026, 8, 30, 3, 15, 2, tzinfo=UTC)


def test_a_production_time_after_our_own_fetch_is_not_believed(
    store: RawStore,
) -> None:
    remote = only(
        plan("ornl_modis_ndvi", points=(BENGALURU,), since=SINCE, until=UNTIL)
    )
    early = datetime(2026, 8, 20, tzinfo=UTC)
    artefact = publish(
        store, "ornl_modis_ndvi", remote, FIXTURES / "ornl-ndvi.json", early
    )
    first = normalise("ornl_modis_ndvi", artefact).samples[0]
    assert first.published_basis == "retrieved"
    assert first.published_at == early


def test_the_sinusoidal_inverse_is_the_forward_projection_undone() -> None:
    lat, lon = 12.97, 77.59
    x = 6371007.181 * np.radians(lon) * np.cos(np.radians(lat))
    y = 6371007.181 * np.radians(lat)
    assert sinusoidal_to_geographic(float(x), float(y)) == pytest.approx((lat, lon))


def test_a_long_interval_is_planned_in_chunks_the_service_answers() -> None:
    planned = plan(
        "ornl_modis_ndvi",
        points=(BENGALURU,),
        since=SINCE - timedelta(days=200),
        until=UNTIL,
    )
    assert len(planned) == 2


def test_precipitation_is_one_sample_a_day_with_the_fill_value_missing(
    store: RawStore,
) -> None:
    remote = only(
        plan("nasa_power_precipitation", points=(BENGALURU,), since=SINCE, until=UNTIL)
    )
    assert "start=20260915&end=20260928" in remote.url
    assert "latitude=13.0" in remote.url
    batch = normalise(
        "nasa_power_precipitation",
        publish(
            store,
            "nasa_power_precipitation",
            remote,
            FIXTURES / "power-precipitation.json",
        ),
    )
    assert len(batch.samples) == 14
    assert {one.value_unit for one in batch.samples} == {"mm/day"}
    gap = next(one for one in batch.samples if one.series_key == "PRECTOTCORR:20260921")
    assert gap.value is None
    assert gap.missing_reason == "fill value -999"
    assert batch.samples[0].product == "NASA POWER PRECTOTCORR (v2.8.4)"


def test_a_dated_product_needs_both_ends_of_its_interval() -> None:
    with pytest.raises(ValueError, match="--since and --until"):
        plan("nasa_power_precipitation", points=(BENGALURU,), since=SINCE)


# --- night-time lights -------------------------------------------------------


def write_granule(path: Path, pixels: int = 200) -> Path:
    """A VNP46A3-shaped granule: the dataset path, attributes and a 10° grid."""
    grid = np.full((pixels, pixels), 250, dtype=np.uint16)
    grid[:2, :2] = [[100, 300], [500, 65535]]
    grid[2:4, 0:2] = 65535
    with h5py.File(path, "w") as granule:
        dataset = granule.create_dataset(DATASET, data=grid)
        dataset.attrs["_FillValue"] = np.uint16(65535)
        dataset.attrs["scale_factor"] = 0.1
    return path


GRANULE_FETCHED = datetime(2026, 10, 5, tzinfo=UTC)


def listing_and_granules(
    store: RawStore, tmp_path: Path, fetched: datetime = GRANULE_FETCHED
) -> list[StoredArtefact]:
    adapter = REGISTRY["nasa_black_marble"].adapter
    request = FetchRequest(
        bbox=KARNATAKA,
        since=datetime(2026, 9, 1, tzinfo=UTC),
        until=datetime(2026, 9, 2, tzinfo=UTC),
    )
    listing = publish(
        store,
        "nasa_black_marble",
        only(adapter.plan(request)),
        FIXTURES / "laads-vnp46a3-2026-244.json",
    )
    granules = adapter.expand(listing, request)  # type: ignore[attr-defined]
    granule = write_granule(tmp_path / "granule.h5")
    return [
        listing,
        *(
            publish(store, "nasa_black_marble", one, granule, fetched)
            for one in granules
        ),
    ]


def test_a_listing_names_only_granules_over_the_box(
    store: RawStore, tmp_path: Path
) -> None:
    listing, *granules = listing_and_granules(store, tmp_path)
    names = [one.manifest.provenance.original_identifier for one in granules]
    assert names == ["VNP46A3.A2026244.h25v07.002.2026275083012.h5"]
    assert normalise("nasa_black_marble", listing).samples == ()
    assert tile_box(25, 7) == BoundingBox(70.0, 10.0, 80.0, 20.0)


def test_night_lights_are_block_means_with_sparse_blocks_missing(
    store: RawStore, tmp_path: Path
) -> None:
    _, granule = listing_and_granules(store, tmp_path)
    batch = normalise("nasa_black_marble", granule)
    assert len(batch.samples) == 100 * 100
    corner = batch.samples[0]
    assert corner.series_key == "h25v07:0,0"
    assert corner.value == pytest.approx((100 + 300 + 500) / 3 * 0.1)
    assert corner.quality == "pixels=3"
    assert (corner.lat_deg, corner.lon_deg) == (19.95, 70.05)
    sparse = batch.samples[100]
    assert sparse.value is None
    assert sparse.missing_reason == "0 of 4 pixels valid"
    assert corner.published_basis == "source_declared"
    assert corner.published_at == datetime(2026, 10, 2, 8, 30, 12, tzinfo=UTC)


def test_a_granule_fetched_before_its_stated_production_is_bounded_by_the_fetch(
    store: RawStore, tmp_path: Path
) -> None:
    """D-222: a production time after our own fetch is a clock that is wrong."""
    _, granule = listing_and_granules(store, tmp_path, fetched=RETRIEVED_AT)
    sample = normalise("nasa_black_marble", granule).samples[0]
    assert sample.published_basis == "retrieved"
    assert sample.published_at == RETRIEVED_AT


def test_the_black_marble_token_travels_as_a_placeholder_header() -> None:
    planned = plan(
        "nasa_black_marble",
        bbox=KARNATAKA,
        since=datetime(2026, 8, 15, tzinfo=UTC),
        until=datetime(2026, 9, 2, tzinfo=UTC),
    )
    assert [one.original_identifier for one in planned] == [
        "VNP46A3/2026/213/listing",
        "VNP46A3/2026/244/listing",
    ]
    assert all(
        one.headers == {"Authorization": f"Bearer {KEY_PLACEHOLDER}"} for one in planned
    )


def test_a_granule_that_is_not_the_products_is_refused(
    store: RawStore, tmp_path: Path
) -> None:
    other = tmp_path / "other.h5"
    with h5py.File(other, "w") as granule:
        granule.create_dataset("something/else", data=np.zeros((10, 10)))
    remote = RemoteArtefact(
        url="https://ladsweb.modaps.eosdis.nasa.gov/x.h5",
        original_identifier="VNP46A3.A2026244.h25v07.002.2026275083012.h5",
        payload_kind="data",
    )
    artefact = publish(store, "nasa_black_marble", remote, other)
    with pytest.raises(NormalisationError, match="has no HDFEOS"):
        normalise("nasa_black_marble", artefact)


# --- what a sample refuses, and what no code path does ------------------------


def a_sample(**overrides: object) -> NormalisedSample:
    fields: dict[str, object] = {
        "series_key": "kp:x",
        "quantity": "kp_index",
        "value_unit": "Kp",
        "observed_from": RETRIEVED_AT,
        "observed_to": RETRIEVED_AT + timedelta(hours=3),
        "published_at": RETRIEVED_AT,
        "published_basis": "retrieved",
        "product": "p",
        "value": 2.0,
    }
    fields.update(overrides)
    return NormalisedSample(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("overrides", "said"),
    [
        ({"product": " "}, "product is empty"),
        ({"quantity": "Kp Index"}, "lowercase"),
        ({"published_basis": "guessed"}, "published_basis"),
        ({"value": None}, "exactly one"),
        ({"missing_reason": "also"}, "exactly one"),
        ({"value": float("nan")}, "finite"),
        ({"observed_to": RETRIEVED_AT - timedelta(hours=1)}, "precedes"),
        ({"published_at": datetime(2026, 9, 28)}, "naive"),  # noqa: DTZ001
        ({"lat_deg": 12.0}, "half a location"),
        ({"lat_deg": 91.0, "lon_deg": 0.0}, "off the globe"),
    ],
)
def test_a_sample_with_incomplete_or_impossible_fields_is_refused(
    overrides: dict[str, object], said: str
) -> None:
    with pytest.raises(NormalisationError, match=said):
        a_sample(**overrides)


def test_no_ingest_module_can_decode_an_image() -> None:
    """D-133 by construction: no image library is imported, so no pixel is read."""
    source = Path(__file__).parents[2] / "ingest" / "src"
    banned = ("PIL", "imageio", "cv2", "skimage", "matplotlib", "rasterio")
    for path in source.rglob("*.py"):
        text = path.read_text("utf-8")
        for name in banned:
            assert f"import {name}" not in text, path
            assert f"from {name}" not in text, path
