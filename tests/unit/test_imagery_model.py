"""The imagery records keep their rules when made and when read back (D-371).

These are the shapes every later step of Stage 38 fills: the search writes a
:class:`SceneWindow`, the annotator builds :class:`Annotation` records, and the
publisher writes an :class:`ImageryManifest` and the :class:`ImageryIndex` the
API reads. Each rule is tested where it is refused, so a later step cannot
produce a record that breaks one.

Reference: docs/DECISIONS.md D-133, D-326, D-371, D-372.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from meridian_ingest.imagery import ImageryUnavailableError, available, require_imagery
from meridian_ingest.imagery.model import (
    MAX_SIDE_PX,
    Annotation,
    Bounds,
    Citation,
    ImageryIndex,
    ImageryManifest,
    IndexEntry,
    Input,
    MalformedImageryError,
    Point,
    PublishedFile,
    SceneWindow,
    caption_sha256,
    manifest_sha256,
)

ACQUIRED = datetime(2026, 11, 12, 5, 21, 30, tzinfo=UTC)
BOX = Bounds(77.46, 12.83, 77.75, 13.14)
DIGEST = "ab" * 32


def a_citation(**overrides: object) -> Citation:
    fields: dict[str, object] = {
        "source_id": "nasa_firms",
        "record_id": f"{DIGEST[:12]}:row-3",
        "url": "https://firms.modaps.eosdis.nasa.gov/",
        "licence": "NASA open data",
    }
    fields.update(overrides)
    return Citation(**fields)  # type: ignore[arg-type]


def a_fire(lat: float = 12.97, lon: float = 77.59) -> Annotation:
    return Annotation(
        kind="fire",
        geometry=Point(lat, lon),
        text="Active fire, 12.4 MW, detected 2026-11-12 08:10 UTC",
        citations=(a_citation(),),
    )


def a_window(**overrides: object) -> SceneWindow:
    fields: dict[str, object] = {
        "source_id": "sentinel2_l2a_earth_search",
        "item_id": "S2B_43PGQ_20261112_0_L2A",
        "asset": "visual",
        "asset_href": "https://sentinel-cogs.s3.us-west-2.amazonaws.com/a/TCI.tif",
        "acquired_at": ACQUIRED,
        "crs": "EPSG:32643",
        "transform": (10.0, 0.0, 770_000.0, 0.0, -10.0, 1_450_000.0),
        "width": 3200,
        "height": 3400,
        "overview_level": 0,
        "etag": '"6a8c0e"',
    }
    fields.update(overrides)
    return SceneWindow(**fields)  # type: ignore[arg-type]


def files() -> tuple[PublishedFile, ...]:
    names = (
        "scene.tif",
        "scene.png",
        "scene.pgw",
        "display.png",
        "tiles/12/2930/1880.webp",
    )
    return tuple(PublishedFile(name, DIGEST, 100) for name in names)


def a_manifest(**overrides: object) -> ImageryManifest:
    fields: dict[str, object] = {
        "area_id": 1,
        "area_label": "Bengaluru urban",
        "source_id": "sentinel2_l2a_earth_search",
        "source_class": "high_resolution",
        "platform": "sentinel-2b",
        "instrument": "msi",
        "acquired_at": ACQUIRED,
        "bounds": BOX,
        "pixel_size_deg": (0.0000898, 0.0000898),
        "resolution_m": 10.0,
        "cloud_fraction": 0.04,
        "cloud_method": "share of the box the scene classification layer calls cloud",
        "zoom_min": 10,
        "zoom_max": 14,
        "credit": "Contains modified Copernicus Sentinel data 2026",
        "licence": "Copernicus free, full and open",
        "caption": "Sentinel-2B over Bengaluru urban, 12 November 2026.",
        "caption_template": "imagery-caption-1",
        "annotations": (a_fire(),),
        "inputs": (Input("scene", "sentinel2_l2a_earth_search/x", DIGEST),),
        "files": files(),
    }
    fields.update(overrides)
    return ImageryManifest(**fields)  # type: ignore[arg-type]


def round_trip(stored: dict[str, object]) -> object:
    return json.loads(json.dumps(stored))


# --- places ------------------------------------------------------------------


def test_a_box_refuses_to_cross_the_antimeridian() -> None:
    with pytest.raises(MalformedImageryError, match="may not cross 180"):
        Bounds(179.5, -10.0, -179.5, -9.0)


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ((77.0, 13.0, 78.0, 12.0), "south must be less than north"),
        ((77.0, 12.0, 78.0, 91.0), "north 91.0 is outside"),
        ((float("nan"), 12.0, 78.0, 13.0), "west must be finite"),
    ],
)
def test_a_box_keeps_its_order_and_range(
    args: tuple[float, float, float, float], message: str
) -> None:
    with pytest.raises(MalformedImageryError, match=message):
        Bounds(*args)


def test_a_box_contains_its_edges() -> None:
    assert BOX.contains(Point(12.83, 77.46))
    assert not BOX.contains(Point(12.82, 77.46))


# --- markers and their sources ---------------------------------------------


def test_a_marker_must_cite_a_record() -> None:
    """The rule the whole stage rests on: no statement on an image without a source."""
    with pytest.raises(MalformedImageryError, match="must cite at least one record"):
        Annotation("fire", Point(12.9, 77.6), "A fire", citations=())


def test_an_area_is_placed_by_its_box_never_a_point_or_polygon() -> None:
    """D-326 publishes an area's box and never its polygon."""
    with pytest.raises(MalformedImageryError, match="placed by a Bounds"):
        Annotation("area", Point(12.9, 77.6), "Bengaluru urban", (a_citation(),))
    with pytest.raises(MalformedImageryError, match="placed by a Point"):
        Annotation("place", BOX, "Bengaluru", (a_citation(),))


def test_a_citation_names_an_https_source() -> None:
    with pytest.raises(MalformedImageryError, match="https URL"):
        a_citation(url="http://firms.modaps.eosdis.nasa.gov/")
    with pytest.raises(MalformedImageryError, match="is not a source id"):
        a_citation(source_id="NASA FIRMS")


def test_a_marker_reads_back_as_it_was_written() -> None:
    marker = a_fire()

    assert Annotation.from_json(round_trip(marker.to_json())) == marker


def test_a_marker_refuses_a_field_it_does_not_know() -> None:
    stored = a_fire().to_json() | {"confidence": "high"}

    with pytest.raises(MalformedImageryError, match="does not know"):
        Annotation.from_json(stored)


# --- the stored window -----------------------------------------------------------


def test_a_window_reads_back_as_it_was_written() -> None:
    window = a_window()

    assert SceneWindow.from_json(round_trip(window.to_json())) == window


def test_a_window_never_stores_an_address_with_its_query() -> None:
    """Planetary Computer's token rides in the query and may not be shared (D-372)."""
    href = "https://landsateuwest.blob.core.windows.net/landsat-c2/a.TIF?st=x&sig=y"

    with pytest.raises(MalformedImageryError, match="without its query"):
        a_window(asset_href=href)


def test_a_window_is_capped_at_its_longest_side() -> None:
    with pytest.raises(MalformedImageryError, match="exceeds 4096 px"):
        a_window(width=MAX_SIDE_PX + 1)


def test_a_window_needs_a_time_zone_and_an_epsg_code() -> None:
    with pytest.raises(MalformedImageryError, match="time zone"):
        a_window(acquired_at=ACQUIRED.replace(tzinfo=None))
    with pytest.raises(MalformedImageryError, match="EPSG code"):
        a_window(crs="+proj=utm +zone=43")


def test_an_instant_is_written_in_utc_whatever_zone_it_came_in() -> None:
    ist = timezone(timedelta(hours=5, minutes=30))
    window = a_window(acquired_at=ACQUIRED.astimezone(ist))

    assert window.to_json()["acquired_at"] == "2026-11-12T05:21:30Z"


# --- the manifest ------------------------------------------------------------------


def test_a_manifest_reads_back_as_it_was_written() -> None:
    manifest = a_manifest()

    assert ImageryManifest.from_json(round_trip(manifest.to_json())) == manifest


def test_a_manifest_states_its_captions_digest() -> None:
    manifest = a_manifest()

    assert manifest.to_json()["caption_sha256"] == caption_sha256(manifest.caption)


def test_an_edited_caption_no_longer_reads() -> None:
    stored = a_manifest().to_json()
    stored["caption"] = "Something else entirely."

    with pytest.raises(MalformedImageryError, match="no longer hashes"):
        ImageryManifest.from_json(stored)


def test_a_marker_outside_the_scene_is_refused() -> None:
    with pytest.raises(MalformedImageryError, match="lies outside the scene"):
        a_manifest(annotations=(a_fire(lat=11.0),))


def test_a_manifest_publishes_every_required_file_and_a_tile() -> None:
    without_world_file = tuple(item for item in files() if item.path != "scene.pgw")
    without_tiles = tuple(
        item for item in files() if not item.path.startswith("tiles/")
    )

    with pytest.raises(MalformedImageryError, match=r"scene\.pgw"):
        a_manifest(files=without_world_file)
    with pytest.raises(MalformedImageryError, match="at least one tile"):
        a_manifest(files=without_tiles)


@pytest.mark.parametrize(
    "path", ["../scene.tif", "Scene.TIF", "tiles/a/b/c.webp", "x.exe"]
)
def test_a_file_is_one_a_scene_publishes(path: str) -> None:
    """No string from a source is a path element (D-141): these names are ours."""
    with pytest.raises(MalformedImageryError, match="is not one a scene publishes"):
        PublishedFile(path, DIGEST, 1)


def test_a_cloud_fraction_is_a_share_or_absent() -> None:
    assert a_manifest(cloud_fraction=None).cloud_fraction is None
    with pytest.raises(MalformedImageryError, match=r"\[0, 1\]"):
        a_manifest(cloud_fraction=4.0)


def test_a_manifest_refuses_a_class_received_imagery_has_not_got_yet() -> None:
    """Stage 40 adds the received class; until then nothing can claim it."""
    with pytest.raises(MalformedImageryError, match="is not one of"):
        a_manifest(source_class="received")


def test_the_directory_digest_follows_every_byte_published() -> None:
    manifest = a_manifest()
    other_tile = (
        *files()[:-1],
        PublishedFile("tiles/12/2930/1880.webp", "cd" * 32, 100),
    )

    assert manifest_sha256(manifest) == manifest_sha256(a_manifest())
    assert manifest_sha256(manifest) != manifest_sha256(
        replace(manifest, files=other_tile)
    )


# --- the index ---------------------------------------------------------------------


def an_entry(hash_: str = "0123456789ab") -> IndexEntry:
    return IndexEntry(
        hash=hash_,
        area_id=1,
        source_class="high_resolution",
        source_id="sentinel2_l2a_earth_search",
        acquired_at=ACQUIRED,
        published_at=ACQUIRED + timedelta(hours=20),
        size_bytes=4_200_000,
    )


def test_an_index_reads_back_as_it_was_written() -> None:
    index = ImageryIndex(scenes=(an_entry(), an_entry("ba9876543210")))

    assert ImageryIndex.from_json(round_trip(index.to_json())) == index


def test_an_index_lists_a_scene_once() -> None:
    with pytest.raises(MalformedImageryError, match="more than once"):
        ImageryIndex(scenes=(an_entry(), an_entry()))


def test_an_index_refuses_another_schema() -> None:
    stored = ImageryIndex(scenes=()).to_json() | {"schema": "imagery-index-0"}

    with pytest.raises(MalformedImageryError, match="schema must be imagery-index-1"):
        ImageryIndex.from_json(stored)


# --- the extra ---------------------------------------------------------------------


def test_the_extra_is_required_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(available, "LIBRARIES", ("rasterio", "no_such_imaging_library"))

    with pytest.raises(ImageryUnavailableError, match=r"meridian-ingest\[imagery\]"):
        require_imagery()


def test_this_checkout_carries_the_extra() -> None:
    """The dev group installs it, so the suite tests what the image runs."""
    require_imagery()
