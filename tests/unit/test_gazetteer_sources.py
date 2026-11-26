"""The two gazetteers: one file each, read whole, and no sample written (D-374).

Each is planned as one artefact, refused when it is not its format, and
normalised to an empty batch, so ``load`` records where the file came from
and writes nothing beside it. A place name labels a picture; it is never a
quantity.

Reference: docs/DECISIONS.md D-133, D-142, D-374.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from meridian_ingest.adapters import REGISTRY
from meridian_ingest.adapters.protocol import FetchRequest
from meridian_ingest.adapters.public.gazetteers import (
    GEONAMES,
    NATURAL_EARTH,
    clean_name,
    read_geonames,
    read_natural_earth,
)
from meridian_ingest.normalise.records import NormalisationError
from meridian_ingest.provenance import Provenance
from meridian_ingest.raw_store import RawStore, StoredArtefact

DAY = 86_400
TOWNS = (
    (9100001, "Aranpur", 12.97, 77.59, 840_000),
    (9100002, "Belavadi", 12.99, 77.60, 0),
)
"""Invented places, in the providers' shapes (D-142)."""


def geonames_zip() -> bytes:
    """A ``cities1000.zip`` in the readme's nineteen tab-separated columns."""
    lines = []
    for geonameid, name, lat, lon, population in TOWNS:
        columns = [""] * 19
        columns[0], columns[1], columns[2] = str(geonameid), name, name
        columns[4], columns[5], columns[14] = f"{lat}", f"{lon}", str(population)
        lines.append("\t".join(columns))
    return _zip_of("cities1000.txt", "\n".join(lines) + "\n")


def natural_earth_json() -> bytes:
    """Natural Earth's populated places as GeoJSON points."""
    features = [
        {
            "type": "Feature",
            "properties": {"name": name, "pop_max": population, "ne_id": ne_id},
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
        }
        for ne_id, name, lat, lon, population in TOWNS
    ]
    return json.dumps({"type": "FeatureCollection", "features": features}).encode()


def put(store: RawStore, source_id: str, data: bytes) -> StoredArtefact:
    """``data`` stored as a fetch of ``source_id`` would have stored it."""
    published = store.publish(
        Provenance(
            source_id=source_id,
            original_identifier="gazetteer",
            source_version='"synthetic"',
            payload_kind="data",
            retrieved_at=datetime(2026, 11, 13, 6, 0, tzinfo=UTC),
            media_type="application/octet-stream",
        ),
        [data],
    )
    return store.read(published.raw_path)


def _zip_of(name: str, text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, text)
    return buffer.getvalue()


@pytest.fixture
def store(tmp_path: Path) -> RawStore:
    return RawStore(tmp_path / "raw")


@pytest.mark.parametrize(
    ("source_id", "url"),
    [
        (
            "geonames_cities1000",
            "https://download.geonames.org/export/dump/cities1000.zip",
        ),
        (
            "natural_earth_places",
            "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/"
            "geojson/ne_10m_populated_places_simple.geojson",
        ),
    ],
)
def test_each_is_one_file_asked_for_once_a_month(source_id: str, url: str) -> None:
    registration = REGISTRY[source_id]
    planned = registration.adapter.plan(FetchRequest())

    assert [(one.url, one.payload_kind) for one in planned] == [(url, "data")]
    assert registration.cadence_s == 30 * DAY
    assert not registration.enabled_by_default
    assert registration.key_env is None


def test_both_are_credited_under_their_own_terms() -> None:
    assert (GEONAMES.licence, NATURAL_EARTH.licence) == ("CC BY 4.0", "Public domain")
    attribution = (Path(__file__).parents[2] / "ATTRIBUTION.md").read_text("utf-8")
    assert GEONAMES.attribution_entry in attribution
    assert NATURAL_EARTH.attribution_entry in attribution


@pytest.mark.parametrize(
    ("source_id", "data"),
    [
        ("geonames_cities1000", geonames_zip()),
        ("natural_earth_places", natural_earth_json()),
    ],
)
def test_a_gazetteer_normalises_to_nothing(
    store: RawStore, source_id: str, data: bytes
) -> None:
    artefact = put(store, source_id, data)
    batch = REGISTRY[source_id].normaliser.normalise(artefact)

    assert batch.samples == ()
    assert batch.receptions == ()
    assert batch.element_sets == ()


def test_geonames_reads_id_name_point_and_population() -> None:
    places = read_geonames(geonames_zip())

    assert len(places) == len(TOWNS)
    first = places[0]
    assert (first.record_id, first.name) == ("9100001", "Aranpur")
    assert (first.lat_deg, first.lon_deg, first.population) == (12.97, 77.59, 840_000)
    assert places[1].population == 0


def test_natural_earth_reads_the_point_not_a_property() -> None:
    places = read_natural_earth(natural_earth_json())

    assert [one.record_id for one in places][:2] == ["9100001", "9100002"]
    assert (places[0].lat_deg, places[0].lon_deg) == (12.97, 77.59)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"not a zip", "not a GeoNames dump"),
        (_zip_of("allCountries.txt", "x"), "not a GeoNames dump"),
        (_zip_of("cities1000.txt", "1\tAranpur\n"), "row 1 has 2 columns"),
        (
            _zip_of("cities1000.txt", "x" + "\t" * 18 + "\n"),
            "row 1 is not a GeoNames row",
        ),
    ],
)
def test_geonames_refuses_what_is_not_its_dump(
    store: RawStore, data: bytes, message: str
) -> None:
    artefact = put(store, "geonames_cities1000", data)

    with pytest.raises(NormalisationError, match=message):
        REGISTRY["geonames_cities1000"].normaliser.normalise(artefact)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"{", "not GeoJSON"),
        (b'{"type": "Feature"}', "not a GeoJSON feature collection"),
        (
            b'{"type": "FeatureCollection", "features": [{"properties": {}, '
            b'"geometry": {"type": "Polygon", "coordinates": []}}]}',
            "feature 1 is not a point",
        ),
        (
            b'{"type": "FeatureCollection", "features": [{"properties": '
            b'{"name": "A"}, "geometry": {"type": "Point", "coordinates": [1, 2]}}]}',
            "feature 1 has no ne_id",
        ),
        (
            b'{"type": "FeatureCollection", "features": [{"properties": '
            b'{"name": "A", "ne_id": 3}, "geometry": {"type": "Point", '
            b'"coordinates": [1, 95]}}]}',
            "lies off the globe",
        ),
    ],
)
def test_natural_earth_refuses_what_is_not_its_collection(
    data: bytes, message: str
) -> None:
    with pytest.raises(NormalisationError, match=message):
        read_natural_earth(data)


@pytest.mark.parametrize(
    ("raw", "cleaned"),
    [
        ("Aranpur", "Aranpur"),
        ("Aran\npur", "Aranpur"),
        ("  Bela   vadi ", "Bela vadi"),
        ("\u202eruPnarA", "ruPnarA"),
        ("x" * 80, "x" * 64),
    ],
)
def test_a_name_loses_what_could_break_or_reverse_a_line(
    raw: str, cleaned: str
) -> None:
    assert clean_name(raw) == cleaned
