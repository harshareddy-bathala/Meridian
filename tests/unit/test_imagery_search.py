"""Windows, cloud and the clearest scene, on scenes these tests write (D-142, D-372).

Every scene here is ours: a small GeoTIFF on a UTM grid near Bengaluru, with
classification values chosen so the answer is known. The search runs end to
end — catalogue answer, token, windows, raw store — under the network guard,
with a fixture retriever and local files standing in for the archive.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

rasterio = pytest.importorskip("rasterio")
np = pytest.importorskip("numpy")

from rasterio.enums import Resampling  # noqa: E402 — after the extra is known present
from rasterio.transform import from_origin  # noqa: E402
from rasterio.warp import transform_bounds  # noqa: E402

from meridian_ingest import cli  # noqa: E402
from meridian_ingest.imagery import pixels as pixels_module  # noqa: E402
from meridian_ingest.imagery.areas import PublishedArea  # noqa: E402
from meridian_ingest.imagery.cloud import count_cloud  # noqa: E402
from meridian_ingest.imagery.model import Bounds, SceneWindow  # noqa: E402
from meridian_ingest.imagery.pixels import (  # noqa: E402
    REDACTED,
    LocalPixels,
    OffSceneError,
    ReadWindow,
    RemotePixels,
    WindowError,
    read_window,
)
from meridian_ingest.imagery.search import SearchContext, search_area  # noqa: E402
from meridian_ingest.imagery.sources import (  # noqa: E402
    GIBS_SOURCE,
    LANDSAT_SOURCE,
    SENTINEL2_SOURCE,
)
from meridian_ingest.imagery.stac import search_artefact  # noqa: E402
from meridian_ingest.imagery.stored import read_scene_window, window_bytes  # noqa: E402
from meridian_ingest.politeness import RequestBudget  # noqa: E402
from meridian_ingest.raw_store import RawStore  # noqa: E402
from meridian_ingest.retrieval import MappedFixtureRetriever  # noqa: E402

UTM = "EPSG:32643"
BOX = Bounds(77.58, 12.96, 77.60, 12.98)
AREA = PublishedArea(area_id=3, label="Bengaluru urban", area_km2=4.8, bounds=BOX)
NOW = datetime(2026, 11, 18, 6, 0, tzinfo=UTC)
TOKEN = "st=2026-11-18&sp=rl&sig=do-not-write-me"


def _box_in_utm() -> tuple[float, float, float, float]:
    left, bottom, right, top = transform_bounds(
        "EPSG:4326", UTM, *BOX.to_list(), densify_pts=21
    )
    return left, bottom, right, top


def write_scene(  # noqa: PLR0913 — a scene's grid and content, each a lever
    path: Path,
    *,
    resolution: float,
    fill: int | Callable[[object], object],
    bands: int = 1,
    dtype: str = "uint8",
    west_part: float = 0.0,
    east_part: float = 1.0,
    overviews: tuple[int, ...] = (2, 4),
    pad_m: float = 1500.0,
) -> Path:
    """A GeoTIFF on a grid aligned to multiples of its pixel size, around the box.

    ``west_part`` and ``east_part`` keep only that share of the columns, so two
    scenes can split the box between them as two granules of one pass do.
    """
    left, bottom, right, top = _box_in_utm()
    x0 = math.floor((left - pad_m) / resolution) * resolution
    y0 = math.ceil((top + pad_m) / resolution) * resolution
    x1 = math.ceil((right + pad_m) / resolution) * resolution
    y1 = math.floor((bottom - pad_m) / resolution) * resolution
    width, height = round((x1 - x0) / resolution), round((y0 - y1) / resolution)
    first, last = round(width * west_part), round(width * east_part)
    array = np.zeros((bands, height, last - first), dtype=dtype)
    array[:] = fill(array) if callable(fill) else fill
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=last - first,
        height=height,
        count=bands,
        dtype=dtype,
        crs=UTM,
        transform=from_origin(x0 + first * resolution, y0, resolution, resolution),
        nodata=0,
        tiled=True,
        blockxsize=256,
        blockysize=256,
    ) as target:
        target.write(array)
        target.build_overviews(list(overviews), Resampling.nearest)
    return path


# --- reading a window --------------------------------------------------------------


def test_a_window_covers_the_box_and_no_more_than_a_pixel_beyond(
    tmp_path: Path,
) -> None:
    href = "https://cogs.example.invalid/s/visual.tif"
    scene = write_scene(tmp_path / "v.tif", resolution=10.0, fill=9, bands=3)

    window = read_window(LocalPixels({href: scene}), href, BOX)

    left, bottom, right, top = _box_in_utm()
    a, _, c, _, e, f = window.transform
    assert window.overview_level == 0
    assert window.crs == UTM
    assert window.array.shape[0] == 3
    assert c <= left < c + a
    assert f >= top > f + e
    assert c + window.width * a >= right > c + (window.width - 1) * a
    assert f + window.height * e <= bottom < f + (window.height - 1) * e


def test_an_area_too_large_is_read_from_the_nearest_overview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    href = "https://cogs.example.invalid/s/visual.tif"
    scene = write_scene(tmp_path / "v.tif", resolution=10.0, fill=9)
    native = read_window(LocalPixels({href: scene}), href, BOX)
    side = max(native.width, native.height)
    monkeypatch.setattr(pixels_module, "MAX_SIDE_PX", math.ceil(side / 3))

    window = read_window(LocalPixels({href: scene}), href, BOX)

    assert window.overview_level == 2  # the factor-4 overview: factor 2 is too big
    # GDAL rounds an overview's size up, so its pixels are a hair under 4 x 10 m.
    assert window.transform[0] == pytest.approx(40.0, rel=0.01)
    assert max(window.width, window.height) <= math.ceil(side / 3)


def test_an_area_no_overview_fits_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    href = "https://cogs.example.invalid/s/visual.tif"
    scene = write_scene(tmp_path / "v.tif", resolution=10.0, fill=9)
    monkeypatch.setattr(pixels_module, "MAX_SIDE_PX", 10)

    with pytest.raises(WindowError, match="no overview brings it under 10 px"):
        read_window(LocalPixels({href: scene}), href, BOX)


def test_a_scene_that_misses_the_box_says_so(tmp_path: Path) -> None:
    href = "https://cogs.example.invalid/s/visual.tif"
    scene = write_scene(tmp_path / "v.tif", resolution=10.0, fill=9)
    elsewhere = Bounds(76.0, 11.0, 76.02, 11.02)

    with pytest.raises(OffSceneError, match="does not reach the box"):
        read_window(LocalPixels({href: scene}), href, elsewhere)


def test_an_address_with_no_stand_in_is_refused() -> None:
    with pytest.raises(WindowError, match="no stand-in file"):
        LocalPixels({}).dataset_path("https://cogs.example.invalid/x.tif")


def test_the_remote_door_signs_at_the_socket_and_withholds_the_token() -> None:
    reader = RemotePixels("meridian-ingest/test").with_token(TOKEN)
    href = "https://landsateuwest.blob.core.windows.net/c/B4.TIF?old=query"

    assert reader.dataset_path(href) == (
        f"/vsicurl/https://landsateuwest.blob.core.windows.net/c/B4.TIF?{TOKEN}"
    )
    assert reader.redact(f"HTTP 403 on ...?{TOKEN}") == f"HTTP 403 on ...?{REDACTED}"
    assert TOKEN not in repr(reader)


def test_the_remote_door_is_stopped_by_the_guard(
    network_guard: Callable[..., None], network_reached: type[AssertionError]
) -> None:
    network_guard()

    with pytest.raises(network_reached, match="vsicurl"):
        read_window(
            RemotePixels("meridian-ingest/test"),
            "https://cogs.example.invalid/s/visual.tif",
            BOX,
        )


# --- counting cloud ---------------------------------------------------------------


def _window(array: object, href: str = "https://x.invalid/a.tif") -> ReadWindow:
    layer = np.asarray(array, dtype="uint16")[None, :, :]
    return ReadWindow(
        href=href,
        array=layer,
        crs=UTM,
        transform=(10.0, 0.0, 1000.0, 0.0, -10.0, 2000.0),
        nodata=0,
        overview_level=0,
        box_transform=(10.0, 0.0, 1000.0, 0.0, -10.0, 2000.0),
        box_shape=layer.shape[1:],
    )


def test_scl_counts_cloud_and_cirrus_over_valid_pixels() -> None:
    count = count_cloud("scl", [_window([[4, 5, 8, 9], [10, 3, 0, 1]])])

    assert (count.cloud, count.clear, count.nodata) == (3, 3, 2)
    assert count.fraction == pytest.approx(0.5)
    assert count.nodata_share == pytest.approx(0.25)
    assert not count.covers


def test_qa_pixel_counts_its_cloud_bits_and_skips_fill() -> None:
    clear, fill, cloud, cirrus, dilated, shadow = (
        1 << 6,
        1,
        1 << 3,
        1 << 2,
        1 << 1,
        1 << 4,
    )
    count = count_cloud(
        "qa_pixel", [_window([[clear, cloud, cirrus], [dilated, shadow, fill]])]
    )

    assert (count.cloud, count.clear, count.nodata) == (3, 2, 1)


def test_two_granules_on_one_grid_are_counted_once_where_they_overlap() -> None:
    west = _window([[9, 9, 4]])
    east = replace(
        _window([[4, 4]], href="https://x.invalid/b.tif"),
        transform=(10.0, 0.0, 1020.0, 0.0, -10.0, 2000.0),
    )
    west = replace(west, box_shape=(1, 4))
    east = replace(east, box_shape=(1, 4))

    count = count_cloud("scl", [west, east])

    # Column 2 is in both; the first granule's pixel stands.
    assert (count.cloud, count.clear, count.nodata) == (2, 2, 0)


def test_granules_on_different_grids_are_not_placed_together() -> None:
    other = replace(
        _window([[4]], href="https://x.invalid/b.tif"),
        box_transform=(10.0, 0.0, 1005.0, 0.0, -10.0, 2000.0),
    )

    with pytest.raises(WindowError, match="without resampling"):
        count_cloud("scl", [_window([[4]]), other])


# --- a stored window ---------------------------------------------------------------


def _record(window: ReadWindow) -> SceneWindow:
    return SceneWindow(
        source_id="sentinel2_l2a",
        item_id="S2B_43PGQ_20261116_0_L2A",
        asset="visual",
        asset_href=window.href,
        acquired_at=NOW,
        crs=window.crs,
        transform=window.transform,
        width=window.width,
        height=window.height,
        overview_level=window.overview_level,
        etag=None,
    )


def test_a_window_stores_to_the_same_bytes_twice_with_its_record_inside(
    tmp_path: Path,
) -> None:
    href = "https://cogs.example.invalid/s/visual.tif"
    scene = write_scene(tmp_path / "v.tif", resolution=10.0, fill=9, bands=3)
    window = read_window(LocalPixels({href: scene}), href, BOX)

    first = window_bytes(window, _record(window))
    second = window_bytes(window, _record(window))
    stored = tmp_path / "stored.tif"
    stored.write_bytes(first)

    assert first == second
    assert read_scene_window(stored) == _record(window)
    with rasterio.open(stored) as source:
        assert source.crs.to_epsg() == 32643
        assert np.array_equal(source.read(), window.array)


# --- the search, end to end ---------------------------------------------------------


def _item(
    item_id: str, when: datetime, cloud: float, datatake: str
) -> dict[str, object]:
    return {
        "type": "Feature",
        "id": item_id,
        "bbox": [77.0, 12.5, 78.0, 13.5],
        "properties": {
            "datetime": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "platform": "sentinel-2b",
            "proj:epsg": 32643,
            "s2:datatake_id": datatake,
            "eo:cloud_cover": cloud,
            "updated": "2026-11-17T00:00:00Z",
        },
        "assets": {
            name: {"href": f"https://cogs.example.invalid/{item_id}/{name}.tif"}
            for name in ("visual", "scl")
        },
    }


class Archive:
    """A catalogue answer and the scenes it points at, all written by the test."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.items: list[dict[str, object]] = []
        self.files: dict[str, Path] = {}

    def scene(
        self,
        item_id: str,
        *,
        days_ago: float,
        scene_cloud: float,
        scl: int | Callable[[object], object],
        datatake: str | None = None,
        **grid: float,
    ) -> None:
        when = NOW - timedelta(days=days_ago)
        self.items.append(_item(item_id, when, scene_cloud, datatake or item_id))
        base = f"https://cogs.example.invalid/{item_id}"
        self.files[f"{base}/scl.tif"] = write_scene(
            self.root / f"{item_id}-scl.tif", resolution=20.0, fill=scl, **grid
        )
        self.files[f"{base}/visual.tif"] = write_scene(
            self.root / f"{item_id}-visual.tif",
            resolution=10.0,
            fill=120,
            bands=3,
            **grid,
        )

    def context(self, store: RawStore) -> SearchContext:
        answer = self.root / "search.json"
        answer.write_text(
            json.dumps({"type": "FeatureCollection", "features": self.items}),
            encoding="utf-8",
        )
        url = search_artefact(SENTINEL2_SOURCE, BOX, NOW).url
        return SearchContext(
            retriever=MappedFixtureRetriever({url: answer}),
            pixels=LocalPixels(self.files),
            store=store,
            budget=RequestBudget(100),
            now=NOW,
            clock=lambda: NOW,
        )


def _half_cloud(array: object) -> object:
    """SCL with the northern half cloud (9) and the southern half vegetation (4)."""
    layer = np.full(np.shape(array), 4, dtype="uint8")
    layer[:, : layer.shape[1] // 2, :] = 9
    return layer


def test_the_clearest_pass_over_the_area_wins_not_the_clearest_granule(
    tmp_path: Path, network_guard: Callable[..., None]
) -> None:
    archive = Archive(tmp_path)
    # Clear across its granule's corners but cloudy over the area: scene-wide 2%.
    archive.scene("cloudy-here", days_ago=1, scene_cloud=2.0, scl=_half_cloud)
    # Cloudier scene-wide, clear over the area.
    archive.scene("clear-here", days_ago=6, scene_cloud=30.0, scl=4)
    store = RawStore(tmp_path / "raw")
    network_guard()

    found = search_area(SENTINEL2_SOURCE, AREA, archive.context(store))

    assert found.chosen is not None
    assert found.chosen.item_ids == ("clear-here",)
    assert found.chosen.cloud_fraction == 0.0
    by_item = {one.item_ids[0]: one for one in found.considered}
    assert by_item["cloudy-here"].cloud_fraction == pytest.approx(0.5, abs=0.02)
    assert len(found.windows) == 1


def test_a_tie_over_the_area_goes_to_the_newer_scene(tmp_path: Path) -> None:
    archive = Archive(tmp_path)
    archive.scene("older", days_ago=9, scene_cloud=5.0, scl=4)
    archive.scene("newer", days_ago=3, scene_cloud=5.0, scl=4)

    found = search_area(
        SENTINEL2_SOURCE, AREA, archive.context(RawStore(tmp_path / "r"))
    )

    assert found.chosen is not None
    assert found.chosen.item_ids == ("newer",)


def test_a_pass_that_leaves_part_of_the_box_without_data_is_refused(
    tmp_path: Path,
) -> None:
    archive = Archive(tmp_path)
    archive.scene("half", days_ago=1, scene_cloud=1.0, scl=4, east_part=0.5)

    found = search_area(
        SENTINEL2_SOURCE, AREA, archive.context(RawStore(tmp_path / "r"))
    )

    assert found.chosen is None
    assert found.considered[0].refused is not None
    assert "without data" in found.considered[0].refused
    assert found.skipped == "no pass read covers the box with valid pixels"
    assert found.windows == ()


def test_two_granules_of_one_datatake_cover_the_box_together(tmp_path: Path) -> None:
    archive = Archive(tmp_path)
    archive.scene(
        "west", days_ago=2, scene_cloud=3.0, scl=4, datatake="DT", east_part=0.55
    )
    archive.scene(
        "east", days_ago=2, scene_cloud=3.0, scl=4, datatake="DT", west_part=0.45
    )

    found = search_area(
        SENTINEL2_SOURCE, AREA, archive.context(RawStore(tmp_path / "r"))
    )

    assert found.chosen is not None
    assert found.chosen.item_ids == ("east", "west")
    assert found.chosen.refused is None
    assert len(found.windows) == 2


def test_what_was_read_is_kept_as_records_and_pictures(tmp_path: Path) -> None:
    archive = Archive(tmp_path)
    archive.scene("one", days_ago=2, scene_cloud=3.0, scl=4)
    store = RawStore(tmp_path / "raw")

    found = search_area(SENTINEL2_SOURCE, AREA, archive.context(store))

    assert found.search_path is not None
    search = store.read(found.search_path).manifest.provenance
    assert (search.payload_kind, search.media_type) == ("data", "application/json")
    assert found.chosen is not None
    (scl_path,) = found.chosen.raw_paths
    scl = store.read(scl_path)
    assert scl.manifest.provenance.payload_kind == "data"
    assert scl.manifest.provenance.original_identifier == "one/scl"
    assert read_scene_window(scl.artefact).asset == "scl"
    visual = store.read(found.windows[0])
    assert visual.manifest.provenance.payload_kind == "tile"
    assert visual.manifest.provenance.source_version == "updated 2026-11-17T00:00:00Z"
    assert read_scene_window(visual.artefact).asset_href == (
        "https://cogs.example.invalid/one/visual.tif"
    )


def test_a_second_search_of_the_same_scene_stores_nothing_new(tmp_path: Path) -> None:
    archive = Archive(tmp_path)
    archive.scene("one", days_ago=2, scene_cloud=3.0, scl=4)
    store = RawStore(tmp_path / "raw")

    first = search_area(SENTINEL2_SOURCE, AREA, archive.context(store))
    second = search_area(SENTINEL2_SOURCE, AREA, archive.context(store))

    assert first.windows == second.windows
    assert len(store.scan("sentinel2_l2a")) == 3


def test_a_read_is_paid_for_from_the_budget(tmp_path: Path) -> None:
    archive = Archive(tmp_path)
    archive.scene("one", days_ago=2, scene_cloud=3.0, scl=4)
    context = archive.context(RawStore(tmp_path / "raw"))
    context.budget = RequestBudget(1)

    with pytest.raises(Exception, match="spent its 1 requests"):
        search_area(SENTINEL2_SOURCE, AREA, context)


def test_a_landsat_token_is_used_and_never_written(tmp_path: Path) -> None:
    files: dict[str, Path] = {}
    base = "https://landsateuwest.blob.core.windows.net/landsat-c2/L9"
    for asset, fill in (
        ("red", 900),
        ("green", 800),
        ("blue", 700),
        ("qa_pixel", 21824),
    ):
        files[f"{base}/{asset}.TIF"] = write_scene(
            tmp_path / f"{asset}.tif", resolution=30.0, fill=fill, dtype="uint16"
        )
    feature = {
        "type": "Feature",
        "id": "LC09_L2SP_144051_20261110_02_T1",
        "bbox": [77.0, 12.5, 78.0, 13.5],
        "properties": {
            "datetime": "2026-11-10T05:10:00Z",
            "platform": "landsat-9",
            "proj:epsg": 32643,
            "landsat:wrs_path": "144",
            "eo:cloud_cover": 12.0,
            "created": "2026-11-12T00:00:00Z",
        },
        "assets": {
            name: {"href": f"{base}/{name}.TIF"}
            for name in ("red", "green", "blue", "qa_pixel")
        },
    }
    answer = tmp_path / "landsat.json"
    answer.write_text(json.dumps({"type": "FeatureCollection", "features": [feature]}))
    token = tmp_path / "token.json"
    token.write_text(
        json.dumps({"msft:expiry": "2026-11-18T07:00:00Z", "token": TOKEN})
    )
    assert LANDSAT_SOURCE.token_url is not None
    store = RawStore(tmp_path / "raw")
    context = SearchContext(
        retriever=MappedFixtureRetriever(
            {
                search_artefact(LANDSAT_SOURCE, BOX, NOW).url: answer,
                LANDSAT_SOURCE.token_url: token,
            }
        ),
        pixels=LocalPixels(files),
        store=store,
        budget=RequestBudget(100),
        now=NOW,
        clock=lambda: NOW,
    )

    found = search_area(LANDSAT_SOURCE, AREA, context)

    assert found.chosen is not None
    assert found.chosen.cloud_fraction == 0.0  # 21824: clear, low-confidence bits only
    assert found.chosen.key == "landsat-9/2026-11-10/path 144 EPSG:32643"
    assert len(found.windows) == 3
    for path in (store.root / "landsat_c2_l2").rglob("*"):
        if path.is_file():
            assert TOKEN.encode() not in path.read_bytes(), path


# --- GIBS ------------------------------------------------------------------------


def test_gibs_keeps_yesterdays_tiles_over_the_box_as_tiles(tmp_path: Path) -> None:
    tile = tmp_path / "tile.jpg"
    tile.write_bytes(b"\xff\xd8\xff\xe0 not decoded here")
    url = (
        "https://gibs.earthdata.nasa.gov/wmts/epsg4326/best/"
        "VIIRS_NOAA20_CorrectedReflectance_TrueColor/default/2026-11-17/250m/8/68/228.jpg"
    )
    store = RawStore(tmp_path / "raw")
    context = SearchContext(
        retriever=MappedFixtureRetriever({url: tile}),
        pixels=LocalPixels({}),
        store=store,
        budget=RequestBudget(10),
        now=NOW,
        clock=lambda: NOW,
    )

    found = search_area(GIBS_SOURCE, AREA, context)

    assert found.chosen is not None
    assert found.chosen.cloud_fraction is None
    (kept,) = found.windows
    provenance = store.read(kept).manifest.provenance
    assert provenance.payload_kind == "tile"
    assert provenance.valid_from == datetime(2026, 11, 17, tzinfo=UTC)


# --- the command -------------------------------------------------------------------


def test_the_command_needs_a_published_areas_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    code = cli.main(["imagery", "search", "--source", "sentinel2_l2a"])

    assert code == 1
    assert "only a published area is imaged" in capsys.readouterr().err


def test_the_command_runs_no_source_unless_one_is_turned_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    summary = tmp_path / "areas.json"
    summary.write_text("{}", encoding="utf-8")

    code = cli.main(["imagery", "search", "--summary", str(summary)])

    assert code == 1
    assert "no imagery source is turned on" in capsys.readouterr().err


def test_the_command_refuses_an_unknown_source_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    summary = tmp_path / "areas.json"
    summary.write_text("{}", encoding="utf-8")

    code = cli.main(
        ["imagery", "search", "--summary", str(summary), "--source", "spot6"]
    )

    assert code == 1
    assert "no imagery source is 'spot6'" in capsys.readouterr().err
