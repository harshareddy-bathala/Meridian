"""Where a marker is placed and what it cites: points, boxes, citations, markers.

Reference: docs/DECISIONS.md D-326, D-371.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass

from meridian_ingest.imagery import model_checks as check
from meridian_ingest.imagery.model_checks import ANNOTATION_KINDS, AnnotationKind


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
        lat = check.number(self.lat_deg, "lat_deg")
        lon = check.number(self.lon_deg, "lon_deg")
        if not -check.LAT_LIMIT <= lat <= check.LAT_LIMIT:
            raise check.fail(f"lat_deg {lat} is outside -90..90")
        if not -check.LON_LIMIT <= lon <= check.LON_LIMIT:
            raise check.fail(f"lon_deg {lon} is outside -180..180")

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
        west, south = check.number(self.west, "west"), check.number(self.south, "south")
        east, north = check.number(self.east, "east"), check.number(self.north, "north")
        for name, value, limit in (
            ("west", west, check.LON_LIMIT),
            ("east", east, check.LON_LIMIT),
            ("south", south, check.LAT_LIMIT),
            ("north", north, check.LAT_LIMIT),
        ):
            if not -limit <= value <= limit:
                raise check.fail(f"{name} {value} is outside -{limit:g}..{limit:g}")
        if not west < east:
            raise check.fail("west must be less than east: a box may not cross 180°")
        if not south < north:
            raise check.fail("south must be less than north")

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
        items = check.sequence(value, field)
        if len(items) != check.BOX_SIDES:
            raise check.fail(f"{field} must be [west, south, east, north]")
        west, south, east, north = (check.number(item, field) for item in items)
        return cls(west, south, east, north)


Geometry = Point | Bounds


def _geometry_from_json(stored: object) -> Geometry:
    if not isinstance(stored, Mapping):
        raise check.fail("geometry must be a JSON object")
    kind = stored.get("type")
    if kind == "point":
        fields = check.fields(
            stored, frozenset({"type", "lat_deg", "lon_deg"}), "point"
        )
        return Point(
            check.number(fields["lat_deg"], "lat_deg"),
            check.number(fields["lon_deg"], "lon_deg"),
        )
    if kind == "box":
        fields = check.fields(stored, frozenset({"type", "bbox"}), "box")
        return Bounds.from_list(fields["bbox"])
    raise check.fail(f"geometry type must be point or box, not {kind!r}")


@dataclass(frozen=True)
class Citation:
    """Where a statement on an image came from.

    ``record_id`` names the record itself, not the source: a FIRMS artefact's
    digest and the row in it, a regional report's hash, a gazetteer row's id.
    ``url`` is where a reader can look the record up: the source's page, or,
    for a record Meridian publishes itself, the path it is served at on the
    same host as the image (``/api/v1/areas``), which is right wherever the
    platform is deployed.
    """

    source_id: str
    record_id: str
    url: str
    licence: str

    def __post_init__(self) -> None:
        """Refuse a record that breaks one of this module's rules."""
        check.source_id(self.source_id)
        check.text(self.record_id, "record_id", maximum=200)
        check.citation_url(self.url, "url")
        check.text(self.licence, "licence", maximum=200)

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
        fields = check.fields(
            stored,
            frozenset({"source_id", "record_id", "url", "licence"}),
            "citation",
        )
        return cls(
            check.text(fields["source_id"], "source_id"),
            check.text(fields["record_id"], "record_id"),
            check.text(fields["url"], "url"),
            check.text(fields["licence"], "licence"),
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
    """A marker on an image: where, what kind, what it says, and its sources.

    ``label`` is what the picture and the map write beside the marker — a
    place's name, "Fire", the area's name — and ``text`` is the explanation a
    reader opens it for. Two fields, so nothing has to cut a label out of a
    sentence.
    """

    kind: AnnotationKind
    geometry: Geometry
    label: str
    text: str
    citations: tuple[Citation, ...]

    def __post_init__(self) -> None:
        """Refuse a record that breaks one of this module's rules."""
        if self.kind not in ANNOTATION_KINDS:
            message = f"annotation kind {self.kind!r} is not one of {ANNOTATION_KINDS}"
            raise check.fail(message)
        expected = _GEOMETRY_OF[self.kind]
        if not isinstance(self.geometry, expected):
            raise check.fail(
                f"a {self.kind} annotation is placed by a {expected.__name__}"
            )
        check.text(self.label, "label", maximum=64)
        check.text(self.text, "text", maximum=500)
        if not self.citations:
            raise check.fail(f"a {self.kind} annotation must cite at least one record")
        if not all(isinstance(item, Citation) for item in self.citations):
            raise check.fail("citations must be Citation records")

    def to_json(self) -> dict[str, object]:
        """The record as JSON-native values, in the shape it is published."""
        return {
            "kind": self.kind,
            "geometry": self.geometry.to_json(),
            "label": self.label,
            "text": self.text,
            "citations": [item.to_json() for item in self.citations],
        }

    @classmethod
    def from_json(cls, stored: object) -> Annotation:
        """Read the record back, refusing a field this version does not know."""
        fields = check.fields(
            stored,
            frozenset({"kind", "geometry", "label", "text", "citations"}),
            "annotation",
        )
        kind = fields["kind"]
        if kind not in ANNOTATION_KINDS:
            raise check.fail(
                f"annotation kind {kind!r} is not one of {ANNOTATION_KINDS}"
            )
        return cls(
            kind,
            _geometry_from_json(fields["geometry"]),
            check.text(fields["label"], "label"),
            check.text(fields["text"], "text"),
            tuple(
                Citation.from_json(item)
                for item in check.sequence(fields["citations"], "citations")
            ),
        )
