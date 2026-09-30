"""An area's shape: inside, distance, centre, size — and what it refuses.

Reference: docs/DECISIONS.md D-227, D-230.
"""

from __future__ import annotations

import pytest

from meridian.regions.areas import AreaRefusedError, new_area
from meridian.regions.geometry import GeometryError, Polygon

BENGALURU = Polygon.from_bbox(77.45, 12.85, 77.75, 13.10)


def test_a_point_inside_is_inside_and_one_outside_is_not() -> None:
    assert BENGALURU.contains(12.97, 77.59)
    assert not BENGALURU.contains(13.2, 77.59)
    assert not BENGALURU.contains(12.97, 77.9)


def test_distance_is_zero_inside_and_grows_outside() -> None:
    assert BENGALURU.distance_km(12.97, 77.59) == 0.0
    north = BENGALURU.distance_km(13.10 + 1.0, 77.6)
    assert north == pytest.approx(111.195, rel=1e-3)


def test_a_one_degree_square_at_the_equator_has_its_known_area() -> None:
    square = Polygon.from_bbox(0.0, -0.5, 1.0, 0.5)
    assert square.area_km2() == pytest.approx(111.195**2, rel=1e-3)
    assert square.centroid() == pytest.approx((0.0, 0.5), abs=1e-9)


def test_a_box_shrinks_with_the_cosine_of_its_latitude() -> None:
    equator = Polygon.from_bbox(0.0, -0.5, 1.0, 0.5).area_km2()
    sixty = Polygon.from_bbox(0.0, 59.5, 1.0, 60.5).area_km2()
    assert sixty / equator == pytest.approx(0.5, rel=1e-2)


def test_geojson_round_trips_and_the_digest_names_the_shape() -> None:
    again = Polygon.from_geojson(BENGALURU.to_geojson())
    assert again == BENGALURU
    assert again.sha256 == BENGALURU.sha256
    other = Polygon.from_bbox(77.45, 12.85, 77.75, 13.11)
    assert other.sha256 != BENGALURU.sha256


def test_a_feature_holding_a_polygon_is_accepted() -> None:
    feature = {"type": "Feature", "properties": {}, "geometry": BENGALURU.to_geojson()}
    assert Polygon.from_geojson(feature) == BENGALURU


@pytest.mark.parametrize(
    ("ring", "said"),
    [
        (((0.0, 0.0), (1.0, 0.0), (1.0, 1.0)), "closed"),
        (((0.0, 0.0), (1.0, 0.0), (0.0, 0.0)), "three distinct"),
        (((0.0, 0.0), (181.0, 0.0), (1.0, 1.0), (0.0, 0.0)), "off the globe"),
        (((-179.0, 0.0), (179.0, 0.0), (179.0, 1.0), (-179.0, 0.0)), "antimeridian"),
        (
            ((0.0, 0.0), (1.0, 1.0), (1.0, 0.0), (0.0, 1.0), (0.0, 0.0)),
            "crosses itself",
        ),
    ],
)
def test_a_shape_that_is_not_one_polygon_is_refused(
    ring: tuple[tuple[float, float], ...], said: str
) -> None:
    with pytest.raises(GeometryError, match=said):
        Polygon(ring)


def test_a_multipolygon_or_a_hole_is_refused() -> None:
    with pytest.raises(GeometryError, match="Polygon"):
        Polygon.from_geojson({"type": "MultiPolygon", "coordinates": []})
    ring = [list(one) for one in BENGALURU.ring]
    with pytest.raises(GeometryError, match="one ring"):
        Polygon.from_geojson({"type": "Polygon", "coordinates": [ring, ring]})


@pytest.mark.parametrize(
    ("label", "notes"),
    [
        ("farm of someone@example.org", None),
        ("Area", "call +91 98450 12345 before visiting"),
        ("12 MG Road plot", None),
    ],
)
def test_an_area_that_would_describe_a_person_is_refused(
    label: str, notes: str | None
) -> None:
    """D-227: an area is a place and a label, never a person or their contact."""
    with pytest.raises(AreaRefusedError, match="never a person"):
        new_area(label, BENGALURU, notes)


def test_a_place_and_a_label_is_accepted() -> None:
    area = new_area("  Bengaluru urban  ", BENGALURU, "watershed of the Arkavathy")
    assert area.label == "Bengaluru urban"
    assert area.area_km2 == pytest.approx(BENGALURU.area_km2())


def test_an_empty_or_overlong_label_is_refused() -> None:
    with pytest.raises(AreaRefusedError, match="1 to 120"):
        new_area("   ", BENGALURU)
    with pytest.raises(AreaRefusedError, match="1 to 120"):
        new_area("x" * 121, BENGALURU)
