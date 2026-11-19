"""Where latitude, longitude and tiles fall, against answers known without our code.

The graticule and the tile grid are arithmetic, so each is checked against a
number that comes from somewhere else: the length of a degree on WGS 84 as
geodesy tables give it, the tile OpenStreetMap serves London from, and rows
counted by hand on a grid of round numbers (D-373).
"""

from __future__ import annotations

import pytest

from meridian_ingest.imagery.graticule import (
    column_of,
    graticule,
    interval_hundredths,
    metres_per_degree,
    row_of,
    scale_bar,
)
from meridian_ingest.imagery.model import Bounds
from meridian_ingest.imagery.sources import (
    GIBS_CREDIT,
    GIBS_SOURCE,
    LANDSAT_SOURCE,
    SENTINEL2_SOURCE,
    credit_for,
)
from meridian_ingest.imagery.webmercator import (
    HALF_WORLD_M,
    tile_bounds,
    tile_of,
    tiles_covering,
    zoom_range,
)

BOX = Bounds(77.58, 12.96, 77.60, 12.98)
GRID = (0.0001, 0.0, 77.58, 0.0, -0.0001, 12.98)
"""A north-up grid of 0.0001° pixels from the box's north-west corner."""


# --- the interval and the labels ------------------------------------------------


@pytest.mark.parametrize(
    ("bounds", "hundredths"),
    [
        (BOX, 1),
        (Bounds(77.4, 12.8, 77.7, 13.1), 10),
        (Bounds(76.0, 11.5, 78.0, 13.5), 50),
        (Bounds(70.0, 5.0, 80.0, 15.0), 100),
    ],
)
def test_the_interval_is_the_finest_with_at_most_five_lines(
    bounds: Bounds, hundredths: int
) -> None:
    assert interval_hundredths(bounds) == hundredths


def test_lines_are_labelled_with_their_hemisphere_and_the_intervals_decimals() -> None:
    lines = graticule(BOX)

    assert [line.label for line in lines if line.axis == "lat"] == [
        "12.96°N",
        "12.97°N",
        "12.98°N",
    ]
    assert [line.label for line in lines if line.axis == "lon"] == [
        "77.58°E",
        "77.59°E",
        "77.60°E",
    ]


def test_south_west_and_the_equator_are_written_as_such() -> None:
    lines = graticule(Bounds(-1.2, -0.6, -0.1, 0.4))

    labels = [line.label for line in lines]
    assert "0.5°S" in labels
    assert "0.0°" in labels
    assert "1.0°W" in labels


# --- geo to pixel, through the affine transform ------------------------------------


def test_a_latitude_line_lands_on_the_row_counted_by_hand() -> None:
    # 12.98° is the top edge; 12.97° is 100 rows of 0.0001° below it.
    assert row_of(GRID, 12.98) == 0
    assert row_of(GRID, 12.97) == 100
    assert row_of((0.0001, 0.0, 77.58, 0.0, -0.00009, 12.98), 12.97) == 111


def test_a_longitude_line_lands_on_the_column_counted_by_hand() -> None:
    assert column_of(GRID, 77.58) == 0
    assert column_of(GRID, 77.59) == 100


def test_a_rotated_grid_is_refused() -> None:
    with pytest.raises(ValueError, match="north-up"):
        row_of((0.0001, 0.00001, 77.58, 0.0, -0.0001, 12.98), 12.97)


def test_a_degree_is_as_long_as_geodesy_tables_say() -> None:
    east, north = metres_per_degree(0.0)

    assert east == pytest.approx(111_319.49, abs=0.01)
    assert north == pytest.approx(110_574.27, abs=0.01)
    assert metres_per_degree(60.0)[0] == pytest.approx(55_800.00, abs=0.01)


def test_the_scale_bar_is_the_longest_round_length_that_fits() -> None:
    ruler = scale_bar(GRID, 0.0, 300)

    # 300 px of 0.0001° at the equator is 3.3 km, so 2 km is the bar.
    assert ruler.label == "2 km"
    assert ruler.metres == 2000
    assert ruler.pixels == round(2000 / 11.131949)


def test_a_short_scale_bar_says_metres() -> None:
    assert scale_bar(GRID, 12.97, 6).label == "50 m"


# --- the tile grid --------------------------------------------------------------------


def test_the_world_tile_is_the_whole_square() -> None:
    assert tile_bounds(0, 0, 0) == (
        -HALF_WORLD_M,
        -HALF_WORLD_M,
        HALF_WORLD_M,
        HALF_WORLD_M,
    )
    assert tile_bounds(1, 1, 0) == (0.0, 0.0, HALF_WORLD_M, HALF_WORLD_M)


def test_london_is_on_the_tile_openstreetmap_serves_it_from() -> None:
    assert tile_of(-0.1278, 51.5074, 10) == (511, 340)


def test_the_box_is_covered_row_by_row() -> None:
    tiles = tiles_covering(BOX, 14)

    assert tiles == sorted(tiles, key=lambda tile: (tile[1], tile[0]))
    assert tile_of(77.59, 12.97, 14) in tiles
    assert all(x in (11722, 11723) for x, _ in tiles)


@pytest.mark.parametrize(
    ("resolution_m", "zooms"),
    [(10.0, (11, 14)), (30.0, (10, 13)), (244.0, (7, 10))],
)
def test_the_deepest_zoom_shows_every_pixel_the_archive_gave(
    resolution_m: float, zooms: tuple[int, int]
) -> None:
    low, deepest = zoom_range(resolution_m, 12.97)
    at_deepest = 2 * HALF_WORLD_M / 256 / 2**deepest * 0.97449

    assert (low, deepest) == zooms
    assert at_deepest <= resolution_m < 2 * at_deepest


# --- the credit -----------------------------------------------------------------------


def test_every_picture_carries_its_sources_credit() -> None:
    assert (
        credit_for(SENTINEL2_SOURCE, 2026)
        == "Contains modified Copernicus Sentinel data 2026"
    )
    assert "U.S. Geological Survey" in credit_for(LANDSAT_SOURCE, 2026)
    assert credit_for(GIBS_SOURCE, 2026) == GIBS_CREDIT
