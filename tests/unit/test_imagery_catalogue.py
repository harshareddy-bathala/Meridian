"""The records a scene search reads: published areas, sources and a STAC answer.

All stdlib, as the modules are: a catalogue answer is a record, and none of
this needs the imagery extra (D-372).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from meridian_ingest.config import ConfigurationError, load_settings
from meridian_ingest.imagery.areas import read_published_areas
from meridian_ingest.imagery.model import Bounds, MalformedImageryError
from meridian_ingest.imagery.sources import (
    GIBS_SOURCE,
    IMAGERY_SOURCES,
    LANDSAT_SOURCE,
    SENTINEL2_SOURCE,
    UnknownImagerySourceError,
    imagery_source,
)
from meridian_ingest.imagery.stac import (
    MAX_SCENE_CLOUD,
    PAGE_LIMIT,
    acquisitions,
    parse_search,
    search_artefact,
    strip_query,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BOX = Bounds(77.58, 12.96, 77.60, 12.98)
NOW = datetime(2026, 11, 18, 6, 0, tzinfo=UTC)
REPORT = "ab" * 32


def _summary(tmp_path: Path, areas: list[dict[str, object]]) -> Path:
    path = tmp_path / "areas-summary.json"
    path.write_text(
        json.dumps(
            {
                "schema": "areas-summary-1",
                "report_sha256": REPORT,
                "method": "regions-1",
                "areas": areas,
                "series": [],
                "changes": [],
            }
        ),
        encoding="utf-8",
    )
    return path


def _area(area_id: int = 3, **extra: object) -> dict[str, object]:
    return {
        "area_id": area_id,
        "label": "Bengaluru urban",
        "area_km2": 4.8,
        "bbox": BOX.to_list(),
        **extra,
    }


def item(  # noqa: PLR0913 — one item's fields, each a test's lever
    item_id: str,
    *,
    when: datetime = NOW - timedelta(days=2),
    cloud: float | None = 10.0,
    datatake: str = "GS2B_20261116T052119_000001_N05.11",
    epsg: int = 32643,
    bbox: list[float] | None = None,
    platform: str = "sentinel-2b",
    assets: tuple[str, ...] = ("visual", "scl"),
) -> dict[str, object]:
    properties: dict[str, object] = {
        "datetime": when.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "platform": platform,
        "proj:epsg": epsg,
        "s2:datatake_id": datatake,
        "updated": "2026-11-16T09:00:00Z",
    }
    if cloud is not None:
        properties["eo:cloud_cover"] = cloud
    return {
        "type": "Feature",
        "id": item_id,
        "bbox": bbox or [77.0, 12.5, 78.0, 13.5],
        "properties": properties,
        "assets": {
            name: {"href": f"https://cogs.example.invalid/{item_id}/{name}.tif"}
            for name in assets
        },
    }


def answer(*items: dict[str, object]) -> bytes:
    return json.dumps({"type": "FeatureCollection", "features": list(items)}).encode()


# --- published areas ---------------------------------------------------------


def test_the_areas_come_from_the_published_summary(tmp_path: Path) -> None:
    path = _summary(tmp_path, [_area(7), _area(3)])

    published = read_published_areas(path)

    assert published.report_sha256 == REPORT
    assert [one.area_id for one in published.areas] == [3, 7]
    assert published.areas[0].bounds == BOX
    assert len(published.file_sha256) == 64


def test_an_area_with_a_polygon_is_refused(tmp_path: Path) -> None:
    path = _summary(tmp_path, [_area(geometry={"type": "Polygon"})])

    with pytest.raises(MalformedImageryError, match="label and a box"):
        read_published_areas(path)


def test_an_unpublished_area_has_no_image(tmp_path: Path) -> None:
    published = read_published_areas(_summary(tmp_path, [_area(3)]))

    assert published.chosen(3)[0].area_id == 3
    with pytest.raises(LookupError, match="not in the published summary"):
        published.chosen(4)


def test_a_file_of_another_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "other.json"
    path.write_text(json.dumps({"schema": "regions-report-1"}), encoding="utf-8")

    with pytest.raises(MalformedImageryError, match="areas-summary-1"):
        read_published_areas(path)


# --- sources -----------------------------------------------------------------


def test_every_imagery_source_is_credited_in_attribution() -> None:
    text = (REPO_ROOT / "ATTRIBUTION.md").read_text(encoding="utf-8")

    for source in IMAGERY_SOURCES.values():
        assert source.descriptor.attribution_entry in text, source.source_id


def test_one_source_per_public_archive_class() -> None:
    classes = sorted(one.source_class for one in IMAGERY_SOURCES.values())

    assert classes == ["daily_wide", "high_resolution", "older_record"]
    assert GIBS_SOURCE.source_id == "nasa_gibs"


def test_an_unknown_imagery_source_is_refused_by_name() -> None:
    with pytest.raises(UnknownImagerySourceError, match="sentinel2_l2a"):
        imagery_source("sentinel1")


# --- the search request --------------------------------------------------------


def test_the_search_asks_for_the_lookback_newest_first() -> None:
    remote = search_artefact(SENTINEL2_SOURCE, BOX, NOW)
    query = parse_qs(urlsplit(remote.url).query)

    assert remote.url.startswith("https://earth-search.aws.element84.com/v1/search?")
    assert query["collections"] == ["sentinel-2-l2a"]
    assert query["bbox"] == ["77.580000,12.960000,77.600000,12.980000"]
    assert query["datetime"] == ["2026-10-19T06:00:00Z/2026-11-18T06:00:00Z"]
    assert query["sortby"] == ["-properties.datetime"]
    assert query["limit"] == [str(PAGE_LIMIT)]
    assert remote.payload_kind == "data"


def test_landsat_looks_back_ninety_days() -> None:
    remote = search_artefact(LANDSAT_SOURCE, BOX, NOW)

    assert remote.valid_from == NOW - timedelta(days=90)


def test_a_token_never_survives_into_a_written_address() -> None:
    signed = "https://landsateuwest.blob.core.windows.net/c/x/B4.TIF?st=1&sig=secret"

    assert (
        strip_query(signed) == "https://landsateuwest.blob.core.windows.net/c/x/B4.TIF"
    )
    assert strip_query(signed + "#frag") == strip_query(signed)


# --- reading the answer --------------------------------------------------------


def test_items_become_candidates_and_the_rest_say_why() -> None:
    body = answer(
        item("good"),
        item("no-scl", assets=("visual",)),
        {"id": "no-properties"},
        item("no-epsg", epsg=0),
    )

    candidates, set_aside, full = parse_search(body, SENTINEL2_SOURCE)

    assert [one.item_id for one in candidates] == ["good"]
    assert candidates[0].crs == "EPSG:32643"
    assert candidates[0].version == "updated 2026-11-16T09:00:00Z"
    assert set_aside == (
        "no-scl: lacks the scl asset",
        "no-properties: no properties",
        "no-epsg: no EPSG code for its grid",
    )
    assert full is False


def test_a_proj_code_is_read_as_well_as_an_epsg_number() -> None:
    feature = item("coded")
    properties = feature["properties"]
    assert isinstance(properties, dict)
    del properties["proj:epsg"]
    properties["proj:code"] = "EPSG:32644"

    candidates, _, _ = parse_search(answer(feature), SENTINEL2_SOURCE)

    assert candidates[0].crs == "EPSG:32644"


def test_landsat_7_is_set_aside() -> None:
    def landsat(item_id: str, platform: str) -> dict[str, object]:
        return item(
            item_id,
            platform=platform,
            assets=("red", "green", "blue", "qa_pixel"),
        )

    candidates, set_aside, _ = parse_search(
        answer(landsat("l9", "landsat-9"), landsat("l7", "landsat-7")),
        LANDSAT_SOURCE,
    )

    assert [one.item_id for one in candidates] == ["l9"]
    assert "landsat-7 is not one of" in set_aside[0]


def test_a_full_page_is_said_out_loud() -> None:
    body = answer(*(item(f"i{n}") for n in range(PAGE_LIMIT)))

    _, _, full = parse_search(body, SENTINEL2_SOURCE)

    assert full is True


def test_a_body_that_is_not_stac_is_refused() -> None:
    with pytest.raises(MalformedImageryError, match="no features list"):
        parse_search(b'{"type": "Feature"}', SENTINEL2_SOURCE)


# --- passes --------------------------------------------------------------------


def _passes(*items: dict[str, object]) -> list[tuple[str, tuple[str, ...]]]:
    candidates, _, _ = parse_search(answer(*items), SENTINEL2_SOURCE)
    return [
        (one.key, tuple(c.item_id for c in one.candidates))
        for one in acquisitions(candidates, BOX)
    ]


def test_granules_of_one_datatake_are_one_scene() -> None:
    passes = _passes(item("b", datatake="T1"), item("a", datatake="T1"))

    assert passes == [("T1 EPSG:32643", ("a", "b"))]


def test_a_pass_across_a_zone_boundary_is_two_scenes() -> None:
    passes = _passes(item("a", datatake="T1"), item("b", datatake="T1", epsg=32644))

    assert sorted(key for key, _ in passes) == ["T1 EPSG:32643", "T1 EPSG:32644"]


def test_passes_are_ordered_least_cloudy_then_newest() -> None:
    passes = _passes(
        item("old-clear", datatake="A", cloud=5.0, when=NOW - timedelta(days=9)),
        item("new-cloudy", datatake="B", cloud=40.0, when=NOW - timedelta(days=1)),
        item("new-clear", datatake="C", cloud=5.0, when=NOW - timedelta(days=4)),
    )

    assert [ids for _, ids in passes] == [
        ("new-clear",),
        ("old-clear",),
        ("new-cloudy",),
    ]


def test_a_granule_off_the_box_or_overcast_is_not_read() -> None:
    passes = _passes(
        item("elsewhere", datatake="A", bbox=[70.0, 20.0, 71.0, 21.0]),
        item("overcast", datatake="B", cloud=MAX_SCENE_CLOUD + 1),
        item("kept", datatake="C"),
    )

    assert [ids for _, ids in passes] == [("kept",)]


# --- settings --------------------------------------------------------------------


def test_the_imagery_table_is_read_and_its_path_resolved(tmp_path: Path) -> None:
    config = tmp_path / "ingest.toml"
    config.write_text(
        '[imagery]\nsummary = "published/areas.json"\n'
        'sources = ["sentinel2_l2a", "nasa_gibs"]\nrequest_budget = 40\n',
        encoding="utf-8",
    )

    settings = load_settings(config)

    assert settings.imagery.summary == tmp_path / "published" / "areas.json"
    assert settings.imagery.sources == ("sentinel2_l2a", "nasa_gibs")
    assert settings.imagery.request_budget == 40


def test_where_scenes_are_published_and_their_cap_are_read(tmp_path: Path) -> None:
    """Nothing is published until a directory is named; the cap is 5 GiB (D-375)."""
    config = tmp_path / "ingest.toml"
    config.write_text("", encoding="utf-8")
    assert load_settings(config).imagery.published is None
    assert load_settings(config).imagery.max_bytes == 5 * 1024**3

    config.write_text(
        '[imagery]\npublished = "published/imagery"\nmax_bytes = 1000\n',
        encoding="utf-8",
    )
    settings = load_settings(config)

    assert settings.imagery.published == tmp_path / "published" / "imagery"
    assert settings.imagery.max_bytes == 1000


@pytest.mark.parametrize("value", ["0", "true", "'5GB'"])
def test_a_cap_that_is_not_a_number_of_bytes_is_refused(
    tmp_path: Path, value: str
) -> None:
    config = tmp_path / "ingest.toml"
    config.write_text(f"[imagery]\nmax_bytes = {value}\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="max_bytes"):
        load_settings(config)


def test_no_imagery_source_is_on_by_default(tmp_path: Path) -> None:
    config = tmp_path / "ingest.toml"
    config.write_text("", encoding="utf-8")

    assert load_settings(config).imagery.sources == ()


def test_an_unknown_imagery_key_is_refused(tmp_path: Path) -> None:
    config = tmp_path / "ingest.toml"
    config.write_text("[imagery]\nsumary = 'x'\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="sumary"):
        load_settings(config)


def test_an_area_may_name_its_own_gazetteer(tmp_path: Path) -> None:
    """The rule picks by the box's size; one area may override it (D-374)."""
    config = tmp_path / "ingest.toml"
    config.write_text(
        '[imagery.areas."3"]\ngazetteer = "geonames_cities1000"\n[imagery.areas."4"]\n',
        encoding="utf-8",
    )

    assert load_settings(config).imagery.gazetteers == {3: "geonames_cities1000"}


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('[imagery.areas."x"]\ngazetteer = "geonames_cities1000"\n', "named by its id"),
        ('[imagery.areas."3"]\ngazetteer = "osm"\n', "must be one of geonames"),
        ('[imagery.areas."3"]\npolygon = []\n', "the only key is gazetteer"),
    ],
)
def test_an_area_override_that_cannot_be_obeyed_is_refused(
    tmp_path: Path, body: str, message: str
) -> None:
    config = tmp_path / "ingest.toml"
    config.write_text(body, encoding="utf-8")

    with pytest.raises(ConfigurationError, match=message):
        load_settings(config)
