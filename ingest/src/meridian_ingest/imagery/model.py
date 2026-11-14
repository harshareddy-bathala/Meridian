"""The records imagery is made of, checked when they are made and when they are read.

Defined before any code that fills them, so the search, the renderer, the
annotator and the publisher agree on one shape (D-371), and the platform's
reader can be written against the same fields without importing this
distribution (D-138): the API checks the published JSON with its own strict
models, and a test holds the two equal.

Stdlib only. Nothing here needs the imagery extra, so the shapes can be read,
checked and tested on an installation that cannot draw.

**The rules each record keeps:**

- an :class:`Annotation` carries at least one :class:`Citation`, so a marker
  without a source cannot be built, let alone published;
- a marker's place comes from a record: a point for a fire or a place name, a
  box for an area or its change, and never an area's polygon (D-326);
- an :class:`ImageryManifest`'s caption hashes to the digest it states, so a
  caption edited after publishing is a manifest that no longer reads;
- every JSON reader refuses a key it does not know, as ``raw_manifest`` does,
  because a field added by a later version and silently dropped would be a
  record that says less than it was written to say.

Reference: docs/DECISIONS.md D-133, D-326, D-371.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from meridian_ingest.raw_manifest import canonical_bytes

__all__ = [
    "ANNOTATION_KINDS",
    "INDEX_SCHEMA",
    "INPUT_KINDS",
    "MANIFEST_SCHEMA",
    "MAX_SIDE_PX",
    "METHOD_VERSION",
    "REQUIRED_FILES",
    "SOURCE_CLASSES",
    "Annotation",
    "Bounds",
    "Citation",
    "ImageryIndex",
    "ImageryManifest",
    "IndexEntry",
    "Input",
    "MalformedImageryError",
    "Point",
    "PublishedFile",
    "SceneWindow",
    "caption_sha256",
    "manifest_sha256",
]

MANIFEST_SCHEMA = "imagery-manifest-1"
INDEX_SCHEMA = "imagery-index-1"
METHOD_VERSION = "imagery-1"
"""The pipeline's version, in every manifest. A change to what a scene renders
to is a new version, so an old directory still names the method that made it."""

MAX_SIDE_PX = 4096
"""The longest side a stored window may have. A larger area is read from the
nearest overview under it, never resampled by us (D-372)."""

SourceClass = Literal["high_resolution", "older_record", "daily_wide"]
SOURCE_CLASSES: tuple[SourceClass, ...] = (
    "high_resolution",
    "older_record",
    "daily_wide",
)
"""The public archive's classes (D-371). Images received by Meridian's own
stations arrive with Stage 40, which adds their class; until then the gallery
says in words that there are none."""

AnnotationKind = Literal["fire", "area", "change", "place"]
ANNOTATION_KINDS: tuple[AnnotationKind, ...] = ("fire", "area", "change", "place")

InputKind = Literal[
    "catalogue", "scene", "firms", "gazetteer", "areas_summary", "template"
]
INPUT_KINDS: tuple[InputKind, ...] = (
    "catalogue",
    "scene",
    "firms",
    "gazetteer",
    "areas_summary",
    "template",
)
"""What a manifest names as an input. Each is a raw artefact, a published file
or a template, named by its digest, so a scene can be rendered again from the
same inputs and land on the same directory (rule 8)."""

REQUIRED_FILES = ("scene.tif", "scene.png", "scene.pgw", "display.png")
"""The four files every scene publishes beside its tiles."""

_SOURCE_ID = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HASH12 = re.compile(r"^[0-9a-f]{12}$")
_EPSG = re.compile(r"^EPSG:[0-9]{4,6}$")
_FILE = re.compile(
    r"^(?:tiles/[0-9]{1,2}/[0-9]{1,7}/[0-9]{1,7}\.webp"
    r"|[a-z0-9_]{1,40}\.(?:tif|png|pgw|txt))$"
)
"""A published file's path: a tile ``tiles/<z>/<x>/<y>.webp``, or one flat name.
No string from a source ever becomes one (D-141); these are names we choose."""

_TEXT_MAX = 2000
_LAT_LIMIT = 90.0
_LON_LIMIT = 180.0
_BOX_SIDES = 4
_AFFINE_TERMS = 6
_PIXEL_AXES = 2
_ZOOM_LIMIT = 22
"""XYZ's deepest level. Nothing open is finer than a tile at 22."""


class MalformedImageryError(ValueError):
    """A record that breaks one of this module's rules, named in the message."""


def _fail(message: str) -> MalformedImageryError:
    return MalformedImageryError(message)


def _text(value: object, field: str, *, maximum: int = _TEXT_MAX) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(f"{field} must be non-empty text")
    if len(value) > maximum:
        raise _fail(f"{field} is longer than {maximum} characters")
    return value


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _fail(f"{field} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise _fail(f"{field} must be finite")
    return number


def _integer(value: object, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(f"{field} must be an integer")
    if value < minimum:
        raise _fail(f"{field} must be at least {minimum}")
    return value


def _https(value: object, field: str) -> str:
    text = _text(value, field)
    if not text.startswith("https://"):
        raise _fail(f"{field} must be an https URL")
    return text


def _instant(value: datetime, field: str) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise _fail(f"{field} must carry a time zone")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_instant(value: object, field: str) -> datetime:
    text = _text(value, field, maximum=20)
    try:
        parsed = datetime.strptime(text + "+0000", "%Y-%m-%dT%H:%M:%SZ%z")
    except ValueError:
        message = f"{field} must be a UTC instant like 2026-11-14T05:30:00Z"
        raise _fail(message) from None
    return parsed.astimezone(UTC)


def _fields(
    stored: object, expected: frozenset[str], what: str
) -> Mapping[str, object]:
    if not isinstance(stored, Mapping):
        raise _fail(f"{what} must be a JSON object")
    unknown = sorted(set(stored) - expected)
    if unknown:
        raise _fail(f"{what} carries fields this version does not know: {unknown}")
    missing = sorted(expected - set(stored))
    if missing:
        raise _fail(f"{what} is missing {missing}")
    return stored


def _sequence(value: object, field: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise _fail(f"{field} must be a JSON array")
    return value


def _not_a_class(value: object) -> str:
    return f"source_class {value!r} is not one of {SOURCE_CLASSES}"


def _source_id(value: object) -> None:
    if not isinstance(value, str) or not _SOURCE_ID.match(value):
        raise _fail(f"source_id {value!r} is not a source id")


def caption_sha256(caption: str) -> str:
    """The digest a manifest states for its caption, over its UTF-8 bytes."""
    return hashlib.sha256(caption.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Point:
    """A place on the ground, in degrees (latitude first, as ISO 6709 writes it)."""

    lat_deg: float
    lon_deg: float

    def __post_init__(self) -> None:
        """Refuse a record that breaks one of this module's rules."""
        lat = _number(self.lat_deg, "lat_deg")
        lon = _number(self.lon_deg, "lon_deg")
        if not -_LAT_LIMIT <= lat <= _LAT_LIMIT:
            raise _fail(f"lat_deg {lat} is outside -90..90")
        if not -_LON_LIMIT <= lon <= _LON_LIMIT:
            raise _fail(f"lon_deg {lon} is outside -180..180")

    def to_json(self) -> dict[str, object]:
        """The record as JSON-native values, in the shape it is published."""
        return {"type": "point", "lat_deg": self.lat_deg, "lon_deg": self.lon_deg}


@dataclass(frozen=True)
class Bounds:
    """A box in degrees: west, south, east, north, as ``areas-summary-1`` writes it.

    A box may not cross the antimeridian. Every area the team has proposed is
    far from it, and a box that wraps would need every consumer — the window
    read, the graticule, the tiles, the viewer — to wrap the same way; refusing
    it is one rule instead of five.
    """

    west: float
    south: float
    east: float
    north: float

    def __post_init__(self) -> None:
        """Refuse a record that breaks one of this module's rules."""
        west, south = _number(self.west, "west"), _number(self.south, "south")
        east, north = _number(self.east, "east"), _number(self.north, "north")
        for name, value, limit in (
            ("west", west, _LON_LIMIT),
            ("east", east, _LON_LIMIT),
            ("south", south, _LAT_LIMIT),
            ("north", north, _LAT_LIMIT),
        ):
            if not -limit <= value <= limit:
                raise _fail(f"{name} {value} is outside -{limit:g}..{limit:g}")
        if not west < east:
            raise _fail("west must be less than east: a box may not cross 180°")
        if not south < north:
            raise _fail("south must be less than north")

    def contains(self, point: Point) -> bool:
        """Whether ``point`` lies inside the box or on its edge."""
        return (
            self.south <= point.lat_deg <= self.north
            and self.west <= point.lon_deg <= self.east
        )

    def to_list(self) -> list[float]:
        """The box as ``[west, south, east, north]``."""
        return [self.west, self.south, self.east, self.north]

    def to_json(self) -> dict[str, object]:
        """The record as JSON-native values, in the shape it is published."""
        return {"type": "box", "bbox": self.to_list()}

    @classmethod
    def from_list(cls, value: object, field: str = "bbox") -> Bounds:
        """Read a box written as ``[west, south, east, north]``."""
        items = _sequence(value, field)
        if len(items) != _BOX_SIDES:
            raise _fail(f"{field} must be [west, south, east, north]")
        west, south, east, north = (_number(item, field) for item in items)
        return cls(west, south, east, north)


Geometry = Point | Bounds


def _geometry_from_json(stored: object) -> Geometry:
    if not isinstance(stored, Mapping):
        raise _fail("geometry must be a JSON object")
    kind = stored.get("type")
    if kind == "point":
        fields = _fields(stored, frozenset({"type", "lat_deg", "lon_deg"}), "point")
        return Point(
            _number(fields["lat_deg"], "lat_deg"), _number(fields["lon_deg"], "lon_deg")
        )
    if kind == "box":
        fields = _fields(stored, frozenset({"type", "bbox"}), "box")
        return Bounds.from_list(fields["bbox"])
    raise _fail(f"geometry type must be point or box, not {kind!r}")


@dataclass(frozen=True)
class Citation:
    """Where a statement on an image came from.

    ``record_id`` names the record itself, not the source: a FIRMS artefact's
    digest and the row in it, a regional report's hash, a gazetteer row's id.
    """

    source_id: str
    record_id: str
    url: str
    licence: str

    def __post_init__(self) -> None:
        """Refuse a record that breaks one of this module's rules."""
        _source_id(self.source_id)
        _text(self.record_id, "record_id", maximum=200)
        _https(self.url, "url")
        _text(self.licence, "licence", maximum=200)

    def to_json(self) -> dict[str, object]:
        """The record as JSON-native values, in the shape it is published."""
        return {
            "source_id": self.source_id,
            "record_id": self.record_id,
            "url": self.url,
            "licence": self.licence,
        }

    @classmethod
    def from_json(cls, stored: object) -> Citation:
        """Read the record back, refusing a field this version does not know."""
        fields = _fields(
            stored,
            frozenset({"source_id", "record_id", "url", "licence"}),
            "citation",
        )
        return cls(
            _text(fields["source_id"], "source_id"),
            _text(fields["record_id"], "record_id"),
            _text(fields["url"], "url"),
            _text(fields["licence"], "licence"),
        )


_GEOMETRY_OF: dict[AnnotationKind, type[Point] | type[Bounds]] = {
    "fire": Point,
    "place": Point,
    "area": Bounds,
    "change": Bounds,
}
"""Which shape each kind of marker is placed with. An area and its change are
drawn as the area's box, because a box is all D-326 publishes about one."""


@dataclass(frozen=True)
class Annotation:
    """A marker on an image: where, what kind, what it says, and its sources."""

    kind: AnnotationKind
    geometry: Geometry
    text: str
    citations: tuple[Citation, ...]

    def __post_init__(self) -> None:
        """Refuse a record that breaks one of this module's rules."""
        if self.kind not in ANNOTATION_KINDS:
            message = f"annotation kind {self.kind!r} is not one of {ANNOTATION_KINDS}"
            raise _fail(message)
        expected = _GEOMETRY_OF[self.kind]
        if not isinstance(self.geometry, expected):
            raise _fail(f"a {self.kind} annotation is placed by a {expected.__name__}")
        _text(self.text, "text", maximum=500)
        if not self.citations:
            raise _fail(f"a {self.kind} annotation must cite at least one record")
        if not all(isinstance(item, Citation) for item in self.citations):
            raise _fail("citations must be Citation records")

    def to_json(self) -> dict[str, object]:
        """The record as JSON-native values, in the shape it is published."""
        return {
            "kind": self.kind,
            "geometry": self.geometry.to_json(),
            "text": self.text,
            "citations": [item.to_json() for item in self.citations],
        }

    @classmethod
    def from_json(cls, stored: object) -> Annotation:
        """Read the record back, refusing a field this version does not know."""
        fields = _fields(
            stored, frozenset({"kind", "geometry", "text", "citations"}), "annotation"
        )
        kind = fields["kind"]
        if kind not in ANNOTATION_KINDS:
            raise _fail(f"annotation kind {kind!r} is not one of {ANNOTATION_KINDS}")
        return cls(
            kind,
            _geometry_from_json(fields["geometry"]),
            _text(fields["text"], "text"),
            tuple(
                Citation.from_json(item)
                for item in _sequence(fields["citations"], "citations")
            ),
        )


@dataclass(frozen=True)
class SceneWindow:
    """What a stored window holds: a source's own pixels over one area's box.

    The raw store keeps the window as a GeoTIFF in the scene's own CRS, at its
    native resolution or at one of its own overviews, never resampled by us
    (D-372). This record is its provenance beside the bytes. ``asset_href`` is
    the asset's address **without its query**, so a token a provider appends to
    an address is never written (D-372).
    """

    source_id: str
    item_id: str
    asset: str
    asset_href: str
    acquired_at: datetime
    crs: str
    transform: tuple[float, float, float, float, float, float]
    width: int
    height: int
    overview_level: int
    etag: str | None

    def __post_init__(self) -> None:
        """Refuse a record that breaks one of this module's rules."""
        _source_id(self.source_id)
        _text(self.item_id, "item_id", maximum=200)
        _text(self.asset, "asset", maximum=60)
        href = _https(self.asset_href, "asset_href")
        if "?" in href or "#" in href:
            raise _fail("asset_href is stored without its query or fragment (D-372)")
        _instant(self.acquired_at, "acquired_at")
        if not isinstance(self.crs, str) or not _EPSG.match(self.crs):
            raise _fail(f"crs {self.crs!r} must be an EPSG code such as EPSG:32643")
        if len(self.transform) != _AFFINE_TERMS:
            raise _fail("transform must be the six affine coefficients")
        for coefficient in self.transform:
            _number(coefficient, "transform")
        for name, side in (("width", self.width), ("height", self.height)):
            _integer(side, name, minimum=1)
            if side > MAX_SIDE_PX:
                raise _fail(f"{name} {side} exceeds {MAX_SIDE_PX} px (D-372)")
        _integer(self.overview_level, "overview_level")
        if self.etag is not None:
            _text(self.etag, "etag", maximum=200)

    def to_json(self) -> dict[str, object]:
        """The record as JSON-native values, in the shape it is published."""
        return {
            "source_id": self.source_id,
            "item_id": self.item_id,
            "asset": self.asset,
            "asset_href": self.asset_href,
            "acquired_at": _instant(self.acquired_at, "acquired_at"),
            "crs": self.crs,
            "transform": list(self.transform),
            "width": self.width,
            "height": self.height,
            "overview_level": self.overview_level,
            "etag": self.etag,
        }

    @classmethod
    def from_json(cls, stored: object) -> SceneWindow:
        """Read the record back, refusing a field this version does not know."""
        fields = _fields(
            stored,
            frozenset(
                {
                    "source_id",
                    "item_id",
                    "asset",
                    "asset_href",
                    "acquired_at",
                    "crs",
                    "transform",
                    "width",
                    "height",
                    "overview_level",
                    "etag",
                }
            ),
            "scene window",
        )
        terms = [
            _number(item, "transform")
            for item in _sequence(fields["transform"], "transform")
        ]
        if len(terms) != _AFFINE_TERMS:
            raise _fail("transform must be the six affine coefficients")
        a, b, c, d, e, f = terms
        etag = fields["etag"]
        return cls(
            _text(fields["source_id"], "source_id"),
            _text(fields["item_id"], "item_id"),
            _text(fields["asset"], "asset"),
            _text(fields["asset_href"], "asset_href"),
            _parse_instant(fields["acquired_at"], "acquired_at"),
            _text(fields["crs"], "crs"),
            (a, b, c, d, e, f),
            _integer(fields["width"], "width", minimum=1),
            _integer(fields["height"], "height", minimum=1),
            _integer(fields["overview_level"], "overview_level"),
            None if etag is None else _text(etag, "etag"),
        )


@dataclass(frozen=True)
class PublishedFile:
    """One file in a scene's directory, by its path, digest and size."""

    path: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        """Refuse a record that breaks one of this module's rules."""
        if not isinstance(self.path, str) or not _FILE.match(self.path):
            raise _fail(f"file path {self.path!r} is not one a scene publishes")
        if not isinstance(self.sha256, str) or not _SHA256.match(self.sha256):
            raise _fail(f"{self.path}: sha256 must be 64 lower-case hex digits")
        _integer(self.size_bytes, "size_bytes")

    def to_json(self) -> dict[str, object]:
        """The record as JSON-native values, in the shape it is published."""
        return {"path": self.path, "sha256": self.sha256, "size_bytes": self.size_bytes}

    @classmethod
    def from_json(cls, stored: object) -> PublishedFile:
        """Read the record back, refusing a field this version does not know."""
        fields = _fields(stored, frozenset({"path", "sha256", "size_bytes"}), "file")
        return cls(
            _text(fields["path"], "path"),
            _text(fields["sha256"], "sha256"),
            _integer(fields["size_bytes"], "size_bytes"),
        )


@dataclass(frozen=True)
class Input:
    """Something a scene was made from, named by its digest.

    ``ref`` says where to find it again: a raw-store path for an artefact, the
    report hash for the published areas summary, the name for a template.
    """

    kind: InputKind
    ref: str
    sha256: str

    def __post_init__(self) -> None:
        """Refuse a record that breaks one of this module's rules."""
        if self.kind not in INPUT_KINDS:
            raise _fail(f"input kind {self.kind!r} is not one of {INPUT_KINDS}")
        _text(self.ref, "ref", maximum=300)
        if not isinstance(self.sha256, str) or not _SHA256.match(self.sha256):
            raise _fail(f"input {self.ref}: sha256 must be 64 lower-case hex digits")

    def to_json(self) -> dict[str, object]:
        """The record as JSON-native values, in the shape it is published."""
        return {"kind": self.kind, "ref": self.ref, "sha256": self.sha256}

    @classmethod
    def from_json(cls, stored: object) -> Input:
        """Read the record back, refusing a field this version does not know."""
        fields = _fields(stored, frozenset({"kind", "ref", "sha256"}), "input")
        kind = fields["kind"]
        if kind not in INPUT_KINDS:
            raise _fail(f"input kind {kind!r} is not one of {INPUT_KINDS}")
        return cls(
            kind,
            _text(fields["ref"], "ref"),
            _text(fields["sha256"], "sha256"),
        )


_MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "method",
        "area_id",
        "area_label",
        "source_id",
        "source_class",
        "platform",
        "instrument",
        "acquired_at",
        "bounds",
        "crs",
        "pixel_size_deg",
        "resolution_m",
        "cloud_fraction",
        "cloud_method",
        "zoom_min",
        "zoom_max",
        "credit",
        "licence",
        "caption",
        "caption_sha256",
        "caption_template",
        "annotations",
        "inputs",
        "files",
    }
)


@dataclass(frozen=True)
class ImageryManifest:
    """A published scene: what it shows, how it was made, and every file in it.

    The manifest is the record, as a regional report's is (D-229): there is no
    table of images. Its digest names the directory it lives in, so the same
    inputs and method publish to the same place, and a second run writes
    nothing.

    ``cloud_fraction`` is the share of the area's box the product's own
    classification layer calls cloud, in ``[0, 1]``; ``None`` when the source
    has no such layer, and ``cloud_method`` then says so. It is never read from
    the picture (D-133).
    """

    area_id: int
    area_label: str
    source_id: str
    source_class: SourceClass
    platform: str
    instrument: str
    acquired_at: datetime
    bounds: Bounds
    pixel_size_deg: tuple[float, float]
    resolution_m: float
    cloud_fraction: float | None
    cloud_method: str
    zoom_min: int
    zoom_max: int
    credit: str
    licence: str
    caption: str
    caption_template: str
    annotations: tuple[Annotation, ...]
    inputs: tuple[Input, ...]
    files: tuple[PublishedFile, ...]
    crs: str = "EPSG:4326"
    method: str = METHOD_VERSION
    schema: str = MANIFEST_SCHEMA

    def __post_init__(self) -> None:
        """Refuse a record that breaks one of this module's rules."""
        self._check_identity()
        self._check_geometry()
        self._check_wording()
        self._check_contents()

    def _check_identity(self) -> None:
        if self.schema != MANIFEST_SCHEMA:
            raise _fail(f"schema must be {MANIFEST_SCHEMA}, not {self.schema!r}")
        if self.method != METHOD_VERSION:
            raise _fail(f"method must be {METHOD_VERSION}, not {self.method!r}")
        _integer(self.area_id, "area_id", minimum=1)
        _text(self.area_label, "area_label", maximum=200)
        _source_id(self.source_id)
        if self.source_class not in SOURCE_CLASSES:
            raise _fail(_not_a_class(self.source_class))
        _text(self.platform, "platform", maximum=60)
        _text(self.instrument, "instrument", maximum=60)
        _instant(self.acquired_at, "acquired_at")

    def _check_geometry(self) -> None:
        if not isinstance(self.bounds, Bounds):
            raise _fail("bounds must be a Bounds record")
        if self.crs != "EPSG:4326":
            raise _fail("a published scene is in EPSG:4326 (D-373)")
        sizes = [_number(size, "pixel_size_deg") for size in self.pixel_size_deg]
        if len(sizes) != _PIXEL_AXES or min(sizes) <= 0:
            raise _fail("pixel_size_deg must be two positive numbers")
        if not _number(self.resolution_m, "resolution_m") > 0:
            raise _fail("resolution_m must be positive")
        _integer(self.zoom_min, "zoom_min")
        _integer(self.zoom_max, "zoom_max")
        if not self.zoom_min <= self.zoom_max <= _ZOOM_LIMIT:
            raise _fail(f"zooms must satisfy zoom_min <= zoom_max <= {_ZOOM_LIMIT}")

    def _check_wording(self) -> None:
        if self.cloud_fraction is not None and not (
            0.0 <= _number(self.cloud_fraction, "cloud_fraction") <= 1.0
        ):
            raise _fail("cloud_fraction must lie in [0, 1]")
        _text(self.cloud_method, "cloud_method", maximum=300)
        _text(self.credit, "credit", maximum=500)
        _text(self.licence, "licence", maximum=200)
        _text(self.caption, "caption", maximum=_TEXT_MAX * 2)
        _text(self.caption_template, "caption_template", maximum=60)

    def _check_contents(self) -> None:
        if not all(isinstance(item, Annotation) for item in self.annotations):
            raise _fail("annotations must be Annotation records")
        for item in self.annotations:
            if isinstance(item.geometry, Point) and not self.bounds.contains(
                item.geometry
            ):
                raise _fail(f"a {item.kind} marker lies outside the scene's bounds")
        if not self.inputs:
            raise _fail("a scene names the inputs it was made from")
        paths = [item.path for item in self.files]
        if len(set(paths)) != len(paths):
            raise _fail("a file is listed more than once")
        missing = [name for name in REQUIRED_FILES if name not in paths]
        if missing:
            raise _fail(f"a scene publishes {missing} as well")
        if not any(path.startswith("tiles/") for path in paths):
            raise _fail("a scene publishes at least one tile")

    @property
    def caption_sha256(self) -> str:
        """The caption's digest, which the manifest states and a reader checks."""
        return caption_sha256(self.caption)

    def to_json(self) -> dict[str, object]:
        """The record as JSON-native values, in the shape it is published."""
        return {
            "schema": self.schema,
            "method": self.method,
            "area_id": self.area_id,
            "area_label": self.area_label,
            "source_id": self.source_id,
            "source_class": self.source_class,
            "platform": self.platform,
            "instrument": self.instrument,
            "acquired_at": _instant(self.acquired_at, "acquired_at"),
            "bounds": self.bounds.to_list(),
            "crs": self.crs,
            "pixel_size_deg": list(self.pixel_size_deg),
            "resolution_m": self.resolution_m,
            "cloud_fraction": self.cloud_fraction,
            "cloud_method": self.cloud_method,
            "zoom_min": self.zoom_min,
            "zoom_max": self.zoom_max,
            "credit": self.credit,
            "licence": self.licence,
            "caption": self.caption,
            "caption_sha256": self.caption_sha256,
            "caption_template": self.caption_template,
            "annotations": [item.to_json() for item in self.annotations],
            "inputs": [item.to_json() for item in self.inputs],
            "files": [item.to_json() for item in self.files],
        }

    @classmethod
    def from_json(cls, stored: object) -> ImageryManifest:
        """Read a manifest back, refusing anything this version did not write.

        Raises:
            MalformedImageryError: An unknown or missing field, a broken rule,
                or a caption that no longer hashes to the digest stated.
        """
        fields = _fields(stored, _MANIFEST_FIELDS, "manifest")
        source_class = fields["source_class"]
        if source_class not in SOURCE_CLASSES:
            raise _fail(_not_a_class(source_class))
        cloud = fields["cloud_fraction"]
        sizes = tuple(
            _number(item, "pixel_size_deg")
            for item in _sequence(fields["pixel_size_deg"], "pixel_size_deg")
        )
        if len(sizes) != _PIXEL_AXES:
            raise _fail("pixel_size_deg must be two positive numbers")
        manifest = cls(
            area_id=_integer(fields["area_id"], "area_id", minimum=1),
            area_label=_text(fields["area_label"], "area_label"),
            source_id=_text(fields["source_id"], "source_id"),
            source_class=source_class,
            platform=_text(fields["platform"], "platform"),
            instrument=_text(fields["instrument"], "instrument"),
            acquired_at=_parse_instant(fields["acquired_at"], "acquired_at"),
            bounds=Bounds.from_list(fields["bounds"], "bounds"),
            pixel_size_deg=(sizes[0], sizes[1]),
            resolution_m=_number(fields["resolution_m"], "resolution_m"),
            cloud_fraction=None if cloud is None else _number(cloud, "cloud_fraction"),
            cloud_method=_text(fields["cloud_method"], "cloud_method"),
            zoom_min=_integer(fields["zoom_min"], "zoom_min"),
            zoom_max=_integer(fields["zoom_max"], "zoom_max"),
            credit=_text(fields["credit"], "credit"),
            licence=_text(fields["licence"], "licence"),
            caption=_text(fields["caption"], "caption", maximum=_TEXT_MAX * 2),
            caption_template=_text(fields["caption_template"], "caption_template"),
            annotations=tuple(
                Annotation.from_json(item)
                for item in _sequence(fields["annotations"], "annotations")
            ),
            inputs=tuple(
                Input.from_json(item) for item in _sequence(fields["inputs"], "inputs")
            ),
            files=tuple(
                PublishedFile.from_json(item)
                for item in _sequence(fields["files"], "files")
            ),
            crs=_text(fields["crs"], "crs"),
            method=_text(fields["method"], "method"),
            schema=_text(fields["schema"], "schema"),
        )
        if fields["caption_sha256"] != manifest.caption_sha256:
            message = "the caption no longer hashes to the digest the manifest states"
            raise _fail(message)
        return manifest


def manifest_sha256(manifest: ImageryManifest) -> str:
    """The digest that names a scene's directory: its first twelve hex digits.

    Taken over the canonical JSON of the whole manifest, files and all, so two
    scenes share a directory only if every byte they publish is the same.
    """
    return hashlib.sha256(canonical_bytes(manifest.to_json())).hexdigest()


@dataclass(frozen=True)
class IndexEntry:
    """One published scene, as the listing names it."""

    hash: str
    area_id: int
    source_class: SourceClass
    source_id: str
    acquired_at: datetime
    published_at: datetime
    size_bytes: int

    def __post_init__(self) -> None:
        """Refuse a record that breaks one of this module's rules."""
        if not isinstance(self.hash, str) or not _HASH12.match(self.hash):
            raise _fail(f"hash {self.hash!r} must be twelve lower-case hex digits")
        _integer(self.area_id, "area_id", minimum=1)
        if self.source_class not in SOURCE_CLASSES:
            raise _fail(_not_a_class(self.source_class))
        _source_id(self.source_id)
        _instant(self.acquired_at, "acquired_at")
        _instant(self.published_at, "published_at")
        _integer(self.size_bytes, "size_bytes")

    def to_json(self) -> dict[str, object]:
        """The record as JSON-native values, in the shape it is published."""
        return {
            "hash": self.hash,
            "area_id": self.area_id,
            "source_class": self.source_class,
            "source_id": self.source_id,
            "acquired_at": _instant(self.acquired_at, "acquired_at"),
            "published_at": _instant(self.published_at, "published_at"),
            "size_bytes": self.size_bytes,
        }

    @classmethod
    def from_json(cls, stored: object) -> IndexEntry:
        """Read the record back, refusing a field this version does not know."""
        fields = _fields(
            stored,
            frozenset(
                {
                    "hash",
                    "area_id",
                    "source_class",
                    "source_id",
                    "acquired_at",
                    "published_at",
                    "size_bytes",
                }
            ),
            "index entry",
        )
        source_class = fields["source_class"]
        if source_class not in SOURCE_CLASSES:
            raise _fail(_not_a_class(source_class))
        return cls(
            _text(fields["hash"], "hash"),
            _integer(fields["area_id"], "area_id", minimum=1),
            source_class,
            _text(fields["source_id"], "source_id"),
            _parse_instant(fields["acquired_at"], "acquired_at"),
            _parse_instant(fields["published_at"], "published_at"),
            _integer(fields["size_bytes"], "size_bytes"),
        )


@dataclass(frozen=True)
class ImageryIndex:
    """Every scene published, which the API reads checked whole (D-369's rule).

    ``published_at`` is in the index and not the manifest: when a directory was
    published is a fact about this deployment, not about the scene, and in the
    manifest it would give the same scene a different digest every day.
    """

    scenes: tuple[IndexEntry, ...]
    schema: str = INDEX_SCHEMA

    def __post_init__(self) -> None:
        """Refuse a record that breaks one of this module's rules."""
        if self.schema != INDEX_SCHEMA:
            raise _fail(f"schema must be {INDEX_SCHEMA}, not {self.schema!r}")
        hashes = [entry.hash for entry in self.scenes]
        if len(set(hashes)) != len(hashes):
            raise _fail("a scene is listed more than once")

    def to_json(self) -> dict[str, object]:
        """The record as JSON-native values, in the shape it is published."""
        return {
            "schema": self.schema,
            "scenes": [entry.to_json() for entry in self.scenes],
        }

    @classmethod
    def from_json(cls, stored: object) -> ImageryIndex:
        """Read the record back, refusing a field this version does not know."""
        fields = _fields(stored, frozenset({"schema", "scenes"}), "index")
        return cls(
            scenes=tuple(
                IndexEntry.from_json(item)
                for item in _sequence(fields["scenes"], "scenes")
            ),
            schema=_text(fields["schema"], "schema"),
        )
