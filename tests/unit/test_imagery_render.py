"""A scene's files, rendered from windows and tiles these tests store (D-373).

Each scene is ours: a window of a known colour on a UTM grid near Bengaluru,
or GIBS tiles of one colour, published to a raw store the way a search would.
Rendering then reads that store and nothing else, under the network guard.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

rasterio = pytest.importorskip("rasterio")
np = pytest.importorskip("numpy")
Image = pytest.importorskip("PIL.Image")

from rasterio.transform import from_origin  # noqa: E402 — after the extra is known present
from rasterio.warp import transform_bounds  # noqa: E402

from meridian_ingest.adapters.public.gibs import tile_span_deg, tiles_covering  # noqa: E402
from meridian_ingest.extent import BoundingBox  # noqa: E402
from meridian_ingest.imagery.display import (  # noqa: E402
    _BOTTOM_PAD,
    _LEFT,
    _TOP,
    display_transform,
)
from meridian_ingest.imagery.graticule import row_of  # noqa: E402
from meridian_ingest.imagery.model import Bounds, SceneWindow  # noqa: E402
from meridian_ingest.imagery.pixels import ReadWindow  # noqa: E402
from meridian_ingest.imagery.render import render_scene  # noqa: E402
from meridian_ingest.imagery.scene import RenderError, compose  # noqa: E402
from meridian_ingest.imagery.sources import GIBS_SOURCE  # noqa: E402
from meridian_ingest.imagery.stored import MEDIA_TYPE, window_bytes  # noqa: E402
from meridian_ingest.imagery.webmercator import tile_bounds  # noqa: E402
from meridian_ingest.provenance import Provenance  # noqa: E402
from meridian_ingest.raw_store import RawStore  # noqa: E402

UTM = "EPSG:32643"
BOX = Bounds(77.58, 12.96, 77.60, 12.98)
ACQUIRED = datetime(2026, 11, 16, 5, 21, tzinfo=UTC)
RETRIEVED = datetime(2026, 11, 18, 6, 0, tzinfo=UTC)


def _store_window(  # noqa: PLR0913 — a window's place and content, each a lever
    store: RawStore,
    *,
    source_id: str = "sentinel2_l2a",
    item_id: str = "S2B_43PGQ_20261116_0_L2A",
    asset: str = "visual",
    colour: tuple[int, ...] = (40, 90, 160),
    dtype: str = "uint8",
    resolution: float = 10.0,
    west_part: float = 0.0,
    east_part: float = 1.0,
    payload_kind: str = "tile",
) -> str:
    """Publish one display window over the box, as a search keeps it."""
    left, bottom, right, top = transform_bounds("EPSG:4326", UTM, *BOX.to_list())
    x0 = (left // resolution - 2) * resolution
    y0 = (top // resolution + 3) * resolution
    width = int((right - x0) // resolution + 3)
    height = int((y0 - bottom) // resolution + 3)
    first, last = round(width * west_part), round(width * east_part)
    array = np.zeros((len(colour), height, last - first), dtype=dtype)
    for band, value in enumerate(colour):
        array[band] = value
    transform = tuple(from_origin(x0 + first * resolution, y0, resolution, resolution))[
        :6
    ]
    href = f"https://cogs.example.invalid/{item_id}/{asset}.tif"
    window = ReadWindow(
        href=href,
        array=array,
        crs=UTM,
        transform=transform,
        nodata=0,
        overview_level=0,
        box_transform=transform,
        box_shape=(height, last - first),
    )
    record = SceneWindow(
        source_id=source_id,
        item_id=item_id,
        asset=asset,
        asset_href=href,
        acquired_at=ACQUIRED,
        crs=UTM,
        transform=transform,
        width=window.width,
        height=window.height,
        overview_level=0,
        etag=None,
    )
    provenance = Provenance(
        source_id=source_id,
        original_identifier=f"{item_id}/{asset}",
        source_version="updated 2026-11-16T09:00:00Z",
        payload_kind=payload_kind,
        retrieved_at=RETRIEVED,
        media_type=MEDIA_TYPE,
        valid_from=ACQUIRED,
        valid_to=ACQUIRED,
    )
    return store.publish(provenance, [window_bytes(window, record)]).raw_path


def _store_gibs_tiles(store: RawStore, colour: tuple[int, int, int]) -> list[str]:
    day = datetime(2026, 11, 17, tzinfo=UTC)
    span = tile_span_deg(GIBS_SOURCE.level)
    paths = []
    for row, col in tiles_covering(BoundingBox(*BOX.to_list()), GIBS_SOURCE.level):
        west, north = -180.0 + col * span, 90.0 - row * span
        buffer = io.BytesIO()
        Image.new("RGB", (512, 512), colour).save(buffer, format="JPEG", quality=95)
        provenance = Provenance(
            source_id="nasa_gibs",
            original_identifier=f"{GIBS_SOURCE.layer}/2026-11-17/8/{row}/{col}",
            source_version="2026-11-17",
            payload_kind="tile",
            retrieved_at=RETRIEVED,
            media_type="image/jpeg",
            valid_from=day,
            valid_to=day + timedelta(days=1),
            spatial_extent={
                "west": west,
                "south": north - span,
                "east": west + span,
                "north": north,
            },
        )
        paths.append(store.publish(provenance, [buffer.getvalue()]).raw_path)
    return paths


def _tile_xy(path: str) -> tuple[int, int]:
    _, _, x, y = path.removesuffix(".webp").split("/")
    return int(x), int(y)


def _png(path: Path) -> object:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGBA"))


# --- the same inputs write the same bytes ------------------------------------------


def test_a_scene_renders_to_the_same_bytes_twice(
    tmp_path: Path, network_guard: Callable[..., None]
) -> None:
    network_guard()
    store = RawStore(tmp_path / "raw")
    paths = [_store_window(store)]

    first = render_scene(store, paths, BOX, tmp_path / "one")
    second = render_scene(store, paths, BOX, tmp_path / "two")

    assert first.files == second.files
    names = [one.path for one in first.files]
    assert {"scene.tif", "scene.png", "scene.pgw", "display.png"} <= set(names)
    assert any(name.startswith("tiles/") for name in names)
    for one in first.files:
        assert (tmp_path / "one" / one.path).read_bytes() == (
            tmp_path / "two" / one.path
        ).read_bytes()


def test_a_scene_is_drawn_in_the_colour_the_source_published(tmp_path: Path) -> None:
    store = RawStore(tmp_path / "raw")

    scene = compose(store, [_store_window(store)], BOX)

    assert scene.rgba[:, scene.height // 2, scene.width // 2].tolist() == [
        40,
        90,
        160,
        255,
    ]
    assert scene.resolution_m == 10.0
    assert scene.acquired_at == ACQUIRED


def test_its_pixels_are_square_on_the_ground_and_from_the_boxs_corner(
    tmp_path: Path,
) -> None:
    store = RawStore(tmp_path / "raw")

    scene = compose(store, [_store_window(store)], BOX)

    dx, dy = scene.pixel_size_deg
    assert dx * 108_563.0 == pytest.approx(10.0, rel=1e-3)
    assert dy * 110_598.0 == pytest.approx(10.0, rel=1e-3)
    assert (scene.bounds.west, scene.bounds.north) == (BOX.west, BOX.north)
    assert BOX.east <= scene.bounds.east < BOX.east + dx
    assert BOX.south - dy < scene.bounds.south <= BOX.south


# --- the georeferenced files open in the right place --------------------------------


def test_the_geotiff_opens_where_the_scene_is(tmp_path: Path) -> None:
    store = RawStore(tmp_path / "raw")
    rendered = render_scene(store, [_store_window(store)], BOX, tmp_path / "out")

    with rasterio.open(tmp_path / "out" / "scene.tif") as source:
        assert source.crs.to_epsg() == 4326
        assert source.count == 4
        assert source.tags(ns="IMAGE_STRUCTURE").get("LAYOUT") == "COG"
        assert "TIFFTAG_DATETIME" not in source.tags()
        assert source.bounds == pytest.approx(tuple(rendered.bounds.to_list()))


def test_the_world_file_read_back_gives_the_scenes_bounds(tmp_path: Path) -> None:
    store = RawStore(tmp_path / "raw")
    rendered = render_scene(store, [_store_window(store)], BOX, tmp_path / "out")

    with rasterio.open(tmp_path / "out" / "scene.png") as source:
        bounds = source.bounds

    assert tuple(bounds) == pytest.approx(tuple(rendered.bounds.to_list()), abs=1e-12)


def test_the_tiles_cover_the_scene_at_every_zoom(tmp_path: Path) -> None:
    store = RawStore(tmp_path / "raw")
    rendered = render_scene(store, [_store_window(store)], BOX, tmp_path / "out")
    left, bottom, right, top = transform_bounds(
        "EPSG:4326", "EPSG:3857", *rendered.bounds.to_list()
    )

    assert (rendered.zoom_min, rendered.zoom_max) == (11, 14)
    for z in range(rendered.zoom_min, rendered.zoom_max + 1):
        tiles = [
            tile_bounds(z, *_tile_xy(one.path))
            for one in rendered.files
            if one.path.startswith(f"tiles/{z}/")
        ]
        assert tiles
        assert min(tile[0] for tile in tiles) <= left
        assert min(tile[1] for tile in tiles) <= bottom
        assert max(tile[2] for tile in tiles) >= right
        assert max(tile[3] for tile in tiles) >= top
    one_tile = next(one for one in rendered.files if one.path.startswith("tiles/"))
    with Image.open(tmp_path / "out" / one_tile.path) as image:
        assert (image.format, image.size, image.mode) == ("WEBP", (256, 256), "RGBA")


# --- the graticule, against a row counted from the coordinates ---------------------


def test_a_latitude_line_is_drawn_on_the_row_its_coordinate_falls_in(
    tmp_path: Path,
) -> None:
    store = RawStore(tmp_path / "raw")
    paths = [_store_window(store, colour=(20, 20, 20))]
    render_scene(store, paths, BOX, tmp_path / "out")
    scene = compose(store, paths, BOX)
    drawn = _png(tmp_path / "out" / "display.png")

    transform, (height, width) = display_transform(scene)
    # 12.97° is half-way down a box from 12.98° to 12.96°.
    row = _TOP + int((BOX.north - 12.97) / -transform[4])
    column = _LEFT + width // 3

    assert row == _TOP + row_of(transform, 12.97)
    assert drawn[row, column, :3].min() > 150
    assert drawn[row - 3, column, :3].max() < 40
    assert drawn[row + 3, column, :3].max() < 40
    assert drawn.shape[0] > _TOP + height + _BOTTOM_PAD


def test_a_small_scene_is_enlarged_by_repeating_its_pixels(tmp_path: Path) -> None:
    store = RawStore(tmp_path / "raw")
    west = _store_window(store, item_id="A", colour=(200, 0, 0), east_part=0.5)
    east = _store_window(store, item_id="B", colour=(0, 0, 200), west_part=0.5)
    render_scene(store, [west, east], BOX, tmp_path / "out")
    drawn = _png(tmp_path / "out" / "display.png")
    row = _TOP + 40

    left = drawn[row, _LEFT + 5, :3].tolist()
    right = drawn[row, drawn.shape[1] - 80, :3].tolist()

    assert left == [200, 0, 0]
    assert right == [0, 0, 200]


# --- each archive's own colour -----------------------------------------------------


def test_landsat_reflectance_is_stretched_once_and_fixed(tmp_path: Path) -> None:
    store = RawStore(tmp_path / "raw")
    # 11818 * 0.0000275 - 0.2 = 0.125 reflectance: half of the white point.
    paths = [
        _store_window(
            store,
            source_id="landsat_c2_l2",
            item_id="LC09_L2SP_144051_20261110_02_T1",
            asset=asset,
            colour=(11818,),
            dtype="uint16",
            resolution=30.0,
        )
        for asset in ("red", "green", "blue")
    ]

    scene = compose(store, paths, BOX)

    assert scene.rgba[:, 5, 5].tolist() == [127, 127, 127, 255]
    assert scene.resolution_m == 30.0


def test_a_landsat_scene_missing_a_band_is_refused(tmp_path: Path) -> None:
    store = RawStore(tmp_path / "raw")
    paths = [
        _store_window(
            store,
            source_id="landsat_c2_l2",
            item_id="LC09",
            asset=asset,
            colour=(11818,),
            dtype="uint16",
            resolution=30.0,
        )
        for asset in ("red", "green")
    ]

    with pytest.raises(RenderError, match="lacks its blue window"):
        compose(store, paths, BOX)


def test_gibs_tiles_are_cut_to_the_box_on_their_own_grid(tmp_path: Path) -> None:
    store = RawStore(tmp_path / "raw")

    scene = compose(store, _store_gibs_tiles(store, (90, 120, 60)), BOX)

    pixel = tile_span_deg(GIBS_SOURCE.level) / 512
    assert scene.pixel_size_deg == (pixel, pixel)
    assert ((scene.bounds.west + 180.0) / pixel) == pytest.approx(
        round((scene.bounds.west + 180.0) / pixel), abs=1e-6
    )
    assert scene.bounds.west <= BOX.west < scene.bounds.west + pixel
    assert scene.bounds.north - pixel < BOX.north <= scene.bounds.north
    assert scene.rgba[3].all()
    assert np.abs(scene.rgba[:3, 0, 0].astype(int) - [90, 120, 60]).max() <= 2
    assert scene.resolution_m == pytest.approx(243.0, abs=1.0)


# --- what is refused ------------------------------------------------------------------


def test_a_classification_window_is_never_drawn(tmp_path: Path) -> None:
    store = RawStore(tmp_path / "raw")
    path = _store_window(store, asset="scl", colour=(4,), payload_kind="data")

    with pytest.raises(RenderError, match="a record, not a picture"):
        compose(store, [path], BOX)


def test_one_picture_comes_from_one_source(tmp_path: Path) -> None:
    store = RawStore(tmp_path / "raw")
    paths = [_store_window(store), *_store_gibs_tiles(store, (1, 2, 3))]

    with pytest.raises(RenderError, match="one source"):
        compose(store, paths, BOX)


def test_render_will_not_write_over_a_directory(tmp_path: Path) -> None:
    store = RawStore(tmp_path / "raw")
    (tmp_path / "out").mkdir()

    with pytest.raises(FileExistsError):
        render_scene(store, [_store_window(store)], BOX, tmp_path / "out")
