"""Markers are placed from records and cite them; the caption is a function of facts.

Every record here is invented and written in its provider's shape — GeoNames'
nineteen tab columns inside ``cities1000.zip``, Natural Earth's GeoJSON
points, FIRMS' area CSV — so the readers are tested against the format and no
provider's data is committed (D-142). Nothing reaches a network, and nothing
here decodes an image: annotation never sees a raster (D-374).

Reference: docs/DECISIONS.md D-133, D-142, D-303, D-326, D-374.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from meridian_ingest.imagery.annotate import annotate_scene, marker_key, read_changes
from meridian_ingest.imagery.annotate_fires import MAX_FIRES, fire_records
from meridian_ingest.imagery.areas import read_published_areas
from meridian_ingest.imagery.caption import CAPTION_TEMPLATE, CaptionFacts, caption
from meridian_ingest.imagery.gazetteer import (
    gazetteer_for,
    long_side_km,
    place_labels,
)
from meridian_ingest.imagery.model import (
    Annotation,
    AnnotationKind,
    Bounds,
    Citation,
    Point,
)
from meridian_ingest.imagery.sources import (
    GIBS_CLOUD_METHOD,
    SENTINEL2_SOURCE,
    cloud_method_for,
)
from meridian_ingest.provenance import Provenance
from meridian_ingest.raw_store import RawStore, StoredArtefact

BOX = Bounds(77.46, 12.83, 77.75, 13.14)
"""About 31 km by 34 km: a small area, so GeoNames names it."""

WIDE = Bounds(76.0, 11.6, 77.2, 12.9)
"""About 130 km across: a large area, so Natural Earth names it."""

ACQUIRED = datetime(2026, 11, 12, 5, 21, 30, tzinfo=UTC)
RETRIEVED = datetime(2026, 11, 13, 6, 0, tzinfo=UTC)
REPORT = "cd" * 32
KARNATAKA = {"west": 74.0, "south": 11.5, "east": 78.6, "north": 18.5}

Town = tuple[int, str, float, float, int]
TOWNS: tuple[Town, ...] = (
    (9100001, "Aranpur", 12.97, 77.59, 840_000),
    (9100002, "Belavadi", 12.99, 77.60, 120_000),
    (9100004, "Doddagiri", 12.90, 77.70, 61_000),
    (9100003, "Chinnahalli", 13.05, 77.50, 61_000),
    (9100005, "Elahanka Kere", 13.12, 77.48, 4_000),
    (9100006, "Far Away", 15.00, 75.00, 2_000_000),
)
"""Inside :data:`BOX` but the last. The second is two kilometres from the
first, and the third and fourth tie on population, so thinning has a
neighbour to drop and a tie to break by name."""


# --- records, in each provider's format ------------------------------------------


def geonames_zip(rows: Sequence[Town] = TOWNS) -> bytes:
    lines = []
    for geonameid, name, lat, lon, population in rows:
        columns = [""] * 19
        columns[0], columns[1], columns[2] = str(geonameid), name, name
        columns[4], columns[5], columns[14] = (
            f"{lat:.5f}",
            f"{lon:.5f}",
            str(population),
        )
        lines.append("\t".join(columns))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("cities1000.txt", "\n".join(lines) + "\n")
    return buffer.getvalue()


def natural_earth_json(rows: Sequence[Town]) -> bytes:
    features = [
        {
            "type": "Feature",
            "properties": {"name": name, "pop_max": population, "ne_id": ne_id},
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
        }
        for ne_id, name, lat, lon, population in rows
    ]
    return json.dumps({"type": "FeatureCollection", "features": features}).encode()


Fire = tuple[float, float, datetime, float | None]


def firms_csv(rows: Sequence[Fire]) -> bytes:
    lines = [
        "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,"
        "instrument,confidence,version,bright_ti5,frp,daynight"
    ]
    for lat, lon, at, frp in rows:
        power = "" if frp is None else f"{frp:.2f}"
        lines.append(
            f"{lat:.5f},{lon:.5f},330.1,0.39,0.36,{at:%Y-%m-%d},{at:%H%M},N,"
            f"VIIRS,h,2.0NRT,290.2,{power},D"
        )
    return ("\n".join(lines) + "\n").encode()


def put(  # noqa: PLR0913 — a stored artefact's provenance, each a lever
    store: RawStore,
    source_id: str,
    data: bytes,
    *,
    retrieved_at: datetime = RETRIEVED,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
    extent: dict[str, float] | None = None,
) -> StoredArtefact:
    published = store.publish(
        Provenance(
            source_id=source_id,
            original_identifier=f"{source_id}/synthetic",
            source_version='"synthetic"',
            payload_kind="data",
            retrieved_at=retrieved_at,
            media_type="application/octet-stream",
            valid_from=valid_from,
            valid_to=valid_to,
            spatial_extent=extent,
        ),
        [data],
    )
    return store.read(published.raw_path)


def put_day(
    store: RawStore,
    day: date,
    rows: Sequence[Fire],
    *,
    retrieved_at: datetime = RETRIEVED,
    extent: dict[str, float] | None = None,
) -> StoredArtefact:
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return put(
        store,
        "nasa_firms",
        firms_csv(rows),
        retrieved_at=retrieved_at,
        valid_from=start,
        valid_to=start + timedelta(days=1),
        extent=KARNATAKA if extent is None else extent,
    )


def summary(tmp_path: Path, changes: list[dict[str, object]] | None = None) -> Path:
    path = tmp_path / "areas-summary.json"
    body = {
        "schema": "areas-summary-1",
        "report_sha256": REPORT,
        "areas": [
            {
                "area_id": 1,
                "label": "Bengaluru urban",
                "area_km2": 741.0,
                "bbox": BOX.to_list(),
            }
        ],
        "changes": [
            {
                "area_id": 1,
                "quantity": "fire_radiative_power",
                "verdict": "within",
                "reason": "the 90% interval reaches -0.3 or does not cross it",
                "baseline": {
                    "from": "2026-09-01T00:00:00+00:00",
                    "until": "2026-10-01",
                },
                "current": {"from": "2026-10-01T00:00:00+00:00", "until": "2026-11-01"},
                "baseline_mean": 4.0,
                "current_mean": 3.5,
                "change": -0.125,
                "low": -0.4,
                "high": 0.1,
            },
            {"area_id": 2, "quantity": "ndvi", "verdict": "alert"},
        ]
        if changes is None
        else changes,
    }
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


@pytest.fixture
def store(tmp_path: Path) -> RawStore:
    return RawStore(tmp_path / "raw")


# --- fires ------------------------------------------------------------------------


def test_a_fire_is_marked_inside_the_box_within_a_day_of_the_scene(
    store: RawStore,
) -> None:
    put_day(
        store,
        date(2026, 11, 12),
        [
            (12.97, 77.59, ACQUIRED + timedelta(hours=3), 12.4),
            (15.00, 75.00, ACQUIRED + timedelta(hours=3), 99.0),
        ],
    )
    put_day(
        store, date(2026, 11, 10), [(12.97, 77.59, ACQUIRED - timedelta(days=2), 50.0)]
    )

    fires = fire_records(store, BOX, ACQUIRED)

    assert [one.label for one in fires.annotations] == ["Fire"]
    assert fires.detected == 1
    assert "12.4 MW" in fires.annotations[0].text
    assert "high confidence" in fires.annotations[0].text


def test_the_window_edges_are_a_day_either_side(store: RawStore) -> None:
    edge = ACQUIRED + timedelta(days=1)
    put_day(
        store,
        edge.date(),
        [(12.97, 77.59, edge, 1.0), (12.97, 77.59, edge + timedelta(minutes=1), 2.0)],
    )

    fires = fire_records(store, BOX, ACQUIRED)

    assert fires.detected == 1
    assert "1.0 MW" in fires.annotations[0].text


def test_a_marker_cites_its_artefacts_digest_and_its_row(store: RawStore) -> None:
    artefact = put_day(
        store,
        date(2026, 11, 12),
        [(15.0, 75.0, ACQUIRED, 1.0), (13.0, 77.6, ACQUIRED, 3.0)],
    )

    (marker,) = fire_records(store, BOX, ACQUIRED).annotations
    (citation,) = marker.citations

    assert citation.source_id == "nasa_firms"
    assert citation.record_id == f"{artefact.manifest.sha256.hex()[:12]} row 2"
    assert citation.url.startswith("https://firms.")


def test_the_newest_fetch_of_a_day_is_the_one_read(store: RawStore) -> None:
    day = date(2026, 11, 12)
    put_day(store, day, [(12.97, 77.59, ACQUIRED, 1.0)])
    newer = put_day(
        store,
        day,
        [(12.98, 77.60, ACQUIRED, 2.0)],
        retrieved_at=RETRIEVED + timedelta(hours=6),
    )

    fires = fire_records(store, BOX, ACQUIRED)

    assert [one.text for one in fires.annotations] == [
        "Active fire detected 12 Nov 2026 05:21 UTC by VIIRS on Suomi NPP, "
        "2.0 MW fire radiative power, high confidence"
    ]
    assert [one.ref for one in fires.inputs] == [newer.raw_path]


def test_a_day_not_fetched_is_not_a_day_without_fire(store: RawStore) -> None:
    put_day(store, date(2026, 11, 12), [])
    put_day(
        store,
        date(2026, 11, 13),
        [(12.97, 77.59, ACQUIRED, 1.0)],
        extent={"west": 77.5, "south": 12.9, "east": 77.7, "north": 13.0},
    )

    fires = fire_records(store, BOX, ACQUIRED)

    assert fires.days == (date(2026, 11, 11), date(2026, 11, 12), date(2026, 11, 13))
    assert fires.held == (date(2026, 11, 12),)
    assert fires.missing == (date(2026, 11, 11), date(2026, 11, 13))
    assert fires.annotations == ()


def test_only_the_most_powerful_are_marked(store: RawStore) -> None:
    rows: list[Fire] = [
        (12.9 + n / 1000, 77.6, ACQUIRED, float(n)) for n in range(MAX_FIRES + 5)
    ]
    rows.append((12.95, 77.6, ACQUIRED, None))
    put_day(store, date(2026, 11, 12), rows)

    fires = fire_records(store, BOX, ACQUIRED)

    assert fires.detected == MAX_FIRES + 6
    assert len(fires.annotations) == MAX_FIRES
    assert not any("no fire radiative power" in one.text for one in fires.annotations)
    assert not any(" 4.0 MW" in one.text for one in fires.annotations)


# --- places -----------------------------------------------------------------------


def test_the_areas_size_picks_its_gazetteer() -> None:
    assert long_side_km(BOX) == pytest.approx(34.3, abs=0.2)
    assert gazetteer_for(BOX) == "geonames_cities1000"
    assert gazetteer_for(WIDE) == "natural_earth_places"
    assert gazetteer_for(WIDE, "geonames_cities1000") == "geonames_cities1000"


def test_places_are_thinned_by_population_then_name(store: RawStore) -> None:
    put(store, "geonames_cities1000", geonames_zip())

    labels = place_labels(store, BOX)

    assert [one.label for one in labels.annotations()] == [
        "Aranpur",
        "Chinnahalli",
        "Doddagiri",
        "Elahanka Kere",
    ]
    first = labels.annotations()[0]
    assert (
        first.text
        == "Aranpur, a populated place of 840,000 people as GeoNames lists it"
    )
    assert first.citations[0].url == "https://www.geonames.org/9100001"
    assert first.citations[0].licence == "CC BY 4.0"


def test_a_large_area_is_named_from_natural_earth(store: RawStore) -> None:
    put(store, "geonames_cities1000", geonames_zip())
    rows = ((1159112629, "Mandyapura", 12.5, 76.9, 0),)
    held = put(store, "natural_earth_places", natural_earth_json(rows))

    labels = place_labels(store, WIDE)

    assert [one.text for one in labels.annotations()] == [
        "Mandyapura, a populated place in Natural Earth"
    ]
    assert labels.inputs()[0].sha256 == held.manifest.sha256.hex()


def test_no_gazetteer_held_is_no_labels_and_no_fallback(store: RawStore) -> None:
    put(store, "natural_earth_places", natural_earth_json(TOWNS))

    labels = place_labels(store, BOX)

    assert (labels.source_id, labels.raw_path, labels.places) == (
        "geonames_cities1000",
        None,
        (),
    )
    assert labels.inputs() == ()


# --- the whole scene --------------------------------------------------------------


def test_every_marker_cites_a_record_and_is_numbered_in_one_order(
    store: RawStore, tmp_path: Path
) -> None:
    put_day(store, date(2026, 11, 12), [(12.97, 77.59, ACQUIRED, 4.0)])
    put(store, "geonames_cities1000", geonames_zip())
    path = summary(tmp_path)
    published = read_published_areas(path)

    markers = annotate_scene(
        store, path, published, published.areas[0], acquired_at=ACQUIRED
    )

    kinds = [one.kind for one in markers.annotations]
    assert kinds == ["area", "change", "fire", "place", "place", "place", "place"]
    assert all(one.citations for one in markers.annotations)
    area, change = markers.annotations[:2]
    assert area.geometry == BOX
    assert change.geometry == BOX
    assert area.citations[0].url == "/api/v1/areas"
    assert REPORT[:12] in area.citations[0].record_id
    assert [one.kind for one in markers.inputs] == [
        "areas_summary",
        "firms",
        "gazetteer",
    ]
    assert markers.inputs[0].sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert markers.credits == (
        "Fire detections: NASA FIRMS.",
        "Place names © GeoNames, CC BY 4.0.",
    )
    assert marker_key(markers.annotations) == (
        "Markers: 1 the area's box; 2 a change over it; 3 a fire detection; "
        "places are named where they lie."
    )


def test_a_change_says_what_by_how_much_and_its_verdict(tmp_path: Path) -> None:
    (change,) = read_changes(summary(tmp_path), 1)

    assert change.text() == (
        "Change in fire radiative power, 2026-10-01 to 2026-11-01 against "
        "2026-09-01 to 2026-10-01: within, -0.125 (interval -0.4 to +0.1); "
        "the 90% interval reaches -0.3 or does not cross it"
    )


def test_a_change_without_a_measure_says_why(tmp_path: Path) -> None:
    path = summary(
        tmp_path,
        [
            {
                "area_id": 1,
                "quantity": "ndvi",
                "verdict": "insufficient",
                "reason": "fewer than 3 values in a period",
                "baseline": {"from": "2026-09-01", "until": "2026-10-01"},
                "current": {"from": "2026-10-01", "until": "2026-11-01"},
                "change": None,
                "low": None,
                "high": None,
            }
        ],
    )

    (change,) = read_changes(path, 1)

    assert change.text().endswith(": insufficient, fewer than 3 values in a period")


def test_runs_of_markers_are_keyed_as_ranges() -> None:
    cites = (Citation("nasa_firms", "ab row 1", "https://firms.invalid/", "open"),)

    def marker(kind: AnnotationKind) -> Annotation:
        where: Point | Bounds = BOX if kind in {"area", "change"} else Point(13.0, 77.6)
        return Annotation(kind, where, "label", "text", cites)

    kinds: tuple[AnnotationKind, ...] = ("area", "change", "change", "fire", "fire")
    assert marker_key([marker(one) for one in kinds]) == (
        "Markers: 1 the area's box; 2–3 changes over it; 4–5 fire detections."
    )
    assert marker_key([]) == ""


# --- the caption ------------------------------------------------------------------


def facts(**overrides: object) -> CaptionFacts:
    fields: dict[str, object] = {
        "area_label": "Bengaluru urban",
        "source_class": "high_resolution",
        "platform": "Sentinel-2B",
        "instrument": "MSI",
        "acquired_at": ACQUIRED,
        "bounds": BOX,
        "resolution_m": 10.0,
        "cloud_fraction": 0.04,
        "cloud_method": cloud_method_for(SENTINEL2_SOURCE),
        "credit": "Contains modified Copernicus Sentinel data 2026",
        "changes": 1,
        "fire_days": (date(2026, 11, 11), date(2026, 11, 12), date(2026, 11, 13)),
        "fire_days_held": (date(2026, 11, 11), date(2026, 11, 12), date(2026, 11, 13)),
        "fires_detected": 3,
        "fires_shown": 3,
        "gazetteer": "GeoNames",
        "places_named_by": "GeoNames",
        "places": 4,
    }
    fields.update(overrides)
    return CaptionFacts(**fields)  # type: ignore[arg-type]


PINNED = (
    "High-resolution satellite image of Bengaluru urban, taken by Sentinel-2B's "
    "MSI on 12 November 2026 at 05:21 UTC. It covers 12.83°N to 13.14°N and "
    "77.46°E to 77.75°E, and its files keep the source's 10 m pixels. Cloud "
    "covers 4% of the area's box, as counted from the product's scene "
    "classification layer (SCL). Marked: the area's bounding box and 1 change "
    "over it, from Meridian's published areas summary; 3 active fire detections "
    "by NASA FIRMS within a day of the image; 4 place names from GeoNames. "
    "Public archive imagery, credited: Contains modified Copernicus Sentinel "
    "data 2026."
)


PINNED_SHA256 = "f65521aeb2ef1cb974e2211873f0408ac96eb58664c4214e2b92fb615efbf242"
"""``imagery-caption-1`` on :func:`facts`. Changing it means a new template."""


def test_the_caption_is_its_template_on_fixed_facts() -> None:
    """A released template is never edited: a changed sentence is a new version."""
    written = caption(facts())

    assert CAPTION_TEMPLATE == "imagery-caption-1"
    assert written == PINNED
    assert hashlib.sha256(written.encode()).hexdigest() == PINNED_SHA256


def test_the_caption_regenerates_from_the_same_facts() -> None:
    assert caption(facts()) == caption(facts())


@pytest.mark.parametrize(
    ("overrides", "said"),
    [
        (
            {"cloud_fraction": None, "cloud_method": GIBS_CLOUD_METHOD},
            "No cloud share is stated: GIBS publishes no cloud layer for this "
            "view, and none is read from the picture.",
        ),
        ({"cloud_fraction": 0.0}, "No cloud lies over the area's box, as counted"),
        ({"cloud_fraction": 0.004}, "Cloud covers less than 1% of the area's box"),
        (
            {"fires_detected": 0, "fires_shown": 0},
            "no active fire detected by NASA FIRMS within a day of the image",
        ),
        (
            {"fires_detected": 40, "fires_shown": 30},
            "the 30 most powerful of 40 active fire detections",
        ),
        (
            {"fire_days_held": (), "fires_detected": 0, "fires_shown": 0},
            "fires not checked, as no NASA FIRMS record was held for 2026-11-11, "
            "2026-11-12 and 2026-11-13",
        ),
        (
            {"fire_days_held": (date(2026, 11, 12),)},
            "with 2026-11-11 and 2026-11-13 not checked, as no record was held",
        ),
        ({"places_named_by": None, "places": 0}, "no place names, as no GeoNames file"),
        ({"places": 0}, "no GeoNames place lies inside the box"),
        ({"changes": 0}, "Marked: the area's bounding box, from Meridian's"),
        ({"source_class": "daily_wide"}, "Daily wide-view satellite image of"),
    ],
)
def test_the_caption_says_absence_as_absence(
    overrides: dict[str, object], said: str
) -> None:
    assert said in caption(facts(**overrides))
