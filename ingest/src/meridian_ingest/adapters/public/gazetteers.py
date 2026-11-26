"""Place names for labelling imagery: GeoNames for small areas, Natural Earth for large.

**What a row is.** A populated place: a name, a point and a population, from
one of two gazetteers. GeoNames' ``cities1000`` lists every place of at least
a thousand people, so it names the towns inside a city-sized area. Natural
Earth's populated places list far fewer, which is what a picture a hundred
kilometres across can carry (D-374).

**Reference only. No sample is written.** A place name labels a picture; it
is not a quantity, and nothing in a prediction, a verdict or a report reads
one. So each normaliser reads its file whole, to refuse one that is not the
format, and returns an empty batch. ``load`` then records where the file came
from and when, and writes no row beside it. The imagery annotator reads the
same file back from the raw store with :func:`read_geonames` or
:func:`read_natural_earth` (D-374).

**Format.**

- GeoNames: ``cities1000.zip`` holds ``cities1000.txt``, tab-separated UTF-8
  with no header and nineteen columns, as the dump's readme lists them. The
  ones read are the id (0), the name (1), latitude (4), longitude (5) and
  population (14).
- Natural Earth: ``ne_10m_populated_places_simple.geojson``, a GeoJSON
  feature collection of points, from the project's own repository. Each
  feature's ``name``, ``pop_max`` and ``ne_id`` are read, with its point.

Both are fetched once a month: neither changes faster, and each is one file.

Reference: docs/DECISIONS.md D-133, D-134, D-220, D-374.
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from meridian_ingest.adapters.protocol import (
    FetchRequest,
    SourceDescriptor,
    refuse_a_tile,
)
from meridian_ingest.adapters.public.common import version_from_headers
from meridian_ingest.normalise.records import NormalisationError, NormalisedBatch
from meridian_ingest.raw_store import StoredArtefact
from meridian_ingest.retrieval import RemoteArtefact, RetrievedArtefact

__all__ = [
    "GEONAMES",
    "GEONAMES_URL",
    "NATURAL_EARTH",
    "NATURAL_EARTH_URL",
    "GazetteerAdapter",
    "GazetteerNormaliser",
    "Place",
    "clean_name",
    "read_geonames",
    "read_natural_earth",
]

GEONAMES = SourceDescriptor(
    source_id="geonames_cities1000",
    source_class="regional_product",
    name="GeoNames cities1000 place names (labels on imagery only)",
    licence="CC BY 4.0",
    terms_url="https://download.geonames.org/export/dump/readme.txt",
    attribution_entry="GeoNames cities1000",
    access_constraint="none",
)

NATURAL_EARTH = SourceDescriptor(
    source_id="natural_earth_places",
    source_class="regional_product",
    name="Natural Earth populated places (labels on imagery only)",
    licence="Public domain",
    terms_url="https://www.naturalearthdata.com/about/terms-of-use/",
    attribution_entry="Natural Earth populated places",
    access_constraint="none",
)

GEONAMES_URL = "https://download.geonames.org/export/dump/cities1000.zip"
NATURAL_EARTH_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/"
    "geojson/ne_10m_populated_places_simple.geojson"
)
"""The project's own repository, which its download page links to. Its
GeoJSON is read with the standard library, where the zipped shapefile on the
download server would need a shapefile reader."""

_GEONAMES_MEMBER = "cities1000.txt"
_GEONAMES_COLUMNS = 19
_MEMBER_MAX = 256 * 1024 * 1024
"""The most a member may expand to. The real file is about 30 MB; a member
declaring more than this is refused before it is read."""

NAME_MAX = 64


@dataclass(frozen=True, slots=True)
class Place:
    """One populated place, as a gazetteer gives it.

    ``record_id`` is the gazetteer's own id for the row, which a citation
    names: GeoNames' ``geonameid``, Natural Earth's ``ne_id``.
    """

    record_id: str
    name: str
    lat_deg: float
    lon_deg: float
    population: int


def clean_name(value: str) -> str:
    """A place's name with control and format characters dropped, at most 64.

    The D-303 rule for a name that will be drawn and published: a control
    character could break a line of a caption, a format character could
    reverse it.
    """
    kept = "".join(one for one in value if one.isprintable())
    return " ".join(kept.split())[:NAME_MAX]


class GazetteerAdapter:
    """One file, the whole gazetteer, asked for as it stands."""

    def __init__(self, descriptor: SourceDescriptor, url: str, identifier: str) -> None:
        """Hold what this gazetteer is and where its one file lives."""
        self._descriptor = descriptor
        self._url = url
        self._identifier = identifier

    @property
    def descriptor(self) -> SourceDescriptor:
        """What this source is, and what it may be used under."""
        return self._descriptor

    def plan(self, request: FetchRequest) -> tuple[RemoteArtefact, ...]:
        """The one file. A gazetteer has no interval and no box to ask about."""
        del request
        return (
            RemoteArtefact(
                url=self._url,
                original_identifier=self._identifier,
                payload_kind="data",
            ),
        )

    def source_version(self, retrieved: RetrievedArtefact) -> str:
        """The file's entity tag, else its modification time."""
        return version_from_headers(retrieved)


Reader = Callable[[bytes], tuple[Place, ...]]


class GazetteerNormaliser:
    """Reads the file to refuse a broken one, and writes nothing (D-374)."""

    def __init__(self, reader: Reader, transformation_version: str) -> None:
        """Hold the reader that checks the format, and the version it is."""
        self._reader = reader
        self.transformation_version = transformation_version

    def normalise(self, artefact: StoredArtefact) -> NormalisedBatch:
        """An empty batch, once the file has been read whole.

        Raises:
            NormalisationError: The bytes are not this gazetteer's format.
        """
        refuse_a_tile(artefact.manifest.provenance)
        self._reader(artefact.read_bytes())
        return NormalisedBatch(transformation_version=self.transformation_version)


def read_geonames(data: bytes) -> tuple[Place, ...]:
    """Every place in a ``cities1000.zip``.

    Raises:
        NormalisationError: Not a zip, no ``cities1000.txt`` inside, a member
            too large, or a row that is not the readme's nineteen columns.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        info = archive.getinfo(_GEONAMES_MEMBER)
    except (zipfile.BadZipFile, KeyError) as exc:
        message = f"not a GeoNames dump holding {_GEONAMES_MEMBER}: {exc}"
        raise NormalisationError(message) from exc
    if info.file_size > _MEMBER_MAX:
        message = f"{_GEONAMES_MEMBER} declares {info.file_size} bytes, too many"
        raise NormalisationError(message)
    try:
        text = archive.read(info).decode("utf-8")
    except (zipfile.BadZipFile, UnicodeDecodeError) as exc:
        message = f"{_GEONAMES_MEMBER} could not be read as UTF-8: {exc}"
        raise NormalisationError(message) from exc
    return tuple(
        _geonames_row(line, number)
        for number, line in enumerate(text.splitlines(), start=1)
        if line
    )


def _geonames_row(line: str, number: int) -> Place:
    columns = line.split("\t")
    if len(columns) != _GEONAMES_COLUMNS:
        message = f"row {number} has {len(columns)} columns, not {_GEONAMES_COLUMNS}"
        raise NormalisationError(message)
    try:
        record_id = str(int(columns[0]))
        lat, lon = float(columns[4]), float(columns[5])
        population = int(columns[14] or 0)
    except ValueError as exc:
        message = f"row {number} is not a GeoNames row: {exc}"
        raise NormalisationError(message) from exc
    return _place(
        record_id, columns[1], (lat, lon), population=population, where=f"row {number}"
    )


def read_natural_earth(data: bytes) -> tuple[Place, ...]:
    """Every place in Natural Earth's populated places GeoJSON.

    Raises:
        NormalisationError: Not JSON, not a feature collection, or a feature
            that is not a named point.
    """
    try:
        stored = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        message = f"not GeoJSON: {exc}"
        raise NormalisationError(message) from exc
    if not isinstance(stored, Mapping) or stored.get("type") != "FeatureCollection":
        message = "not a GeoJSON feature collection"
        raise NormalisationError(message)
    features = stored.get("features")
    if not isinstance(features, list):
        message = "a feature collection with no features array"
        raise NormalisationError(message)
    return tuple(
        _natural_earth_feature(one, number)
        for number, one in enumerate(features, start=1)
    )


def _natural_earth_feature(feature: object, number: int) -> Place:
    where = f"feature {number}"
    if not isinstance(feature, Mapping):
        raise NormalisationError(f"{where} is not an object")
    properties, geometry = feature.get("properties"), feature.get("geometry")
    if not isinstance(properties, Mapping) or not isinstance(geometry, Mapping):
        raise NormalisationError(f"{where} has no properties or no geometry")
    coordinates = geometry.get("coordinates")
    if (
        geometry.get("type") != "Point"
        or not isinstance(coordinates, list)
        or len(coordinates) < 2  # noqa: PLR2004 — longitude, latitude
        or not all(_is_number(one) for one in coordinates[:2])
    ):
        raise NormalisationError(f"{where} is not a point")
    record_id, name = _number(properties.get("ne_id")), properties.get("name")
    if record_id is None or not isinstance(name, str):
        raise NormalisationError(f"{where} has no ne_id or no name")
    population = _number(properties.get("pop_max")) or 0.0
    return _place(
        str(int(record_id)),
        name,
        (float(coordinates[1]), float(coordinates[0])),
        population=max(0, int(population)),
        where=where,
    )


def _number(value: object) -> float | None:
    """``value`` as a number, or None for anything else, a boolean included."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _is_number(value: object) -> bool:
    return _number(value) is not None


def _place(
    record_id: str, name: str, at: tuple[float, float], *, population: int, where: str
) -> Place:
    lat, lon = at
    cleaned = clean_name(name)
    if not cleaned:
        raise NormalisationError(f"{where} has no name")
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):  # noqa: PLR2004
        raise NormalisationError(f"{where} lies off the globe: {lat}, {lon}")
    return Place(record_id, cleaned, lat, lon, max(0, population))
