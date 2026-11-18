"""A published scene's manifest, the digest that names its directory, and the index.

Reference: docs/DECISIONS.md D-229, D-369, D-371.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from meridian_ingest.imagery import model_checks as check
from meridian_ingest.imagery.model_checks import (
    INDEX_SCHEMA,
    MANIFEST_SCHEMA,
    METHOD_VERSION,
    REQUIRED_FILES,
    SOURCE_CLASSES,
    SourceClass,
)
from meridian_ingest.imagery.model_files import Input, PublishedFile
from meridian_ingest.imagery.model_geometry import (
    Annotation,
    Bounds,
    Point,
    caption_sha256,
)
from meridian_ingest.raw_manifest import canonical_bytes

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
            raise check.fail(f"schema must be {MANIFEST_SCHEMA}, not {self.schema!r}")
        if self.method != METHOD_VERSION:
            raise check.fail(f"method must be {METHOD_VERSION}, not {self.method!r}")
        check.integer(self.area_id, "area_id", minimum=1)
        check.text(self.area_label, "area_label", maximum=200)
        check.source_id(self.source_id)
        if self.source_class not in SOURCE_CLASSES:
            raise check.fail(check.not_a_class(self.source_class))
        check.text(self.platform, "platform", maximum=60)
        check.text(self.instrument, "instrument", maximum=60)
        check.instant(self.acquired_at, "acquired_at")

    def _check_geometry(self) -> None:
        if not isinstance(self.bounds, Bounds):
            raise check.fail("bounds must be a Bounds record")
        if self.crs != "EPSG:4326":
            raise check.fail("a published scene is in EPSG:4326 (D-373)")
        sizes = [check.number(size, "pixel_size_deg") for size in self.pixel_size_deg]
        if len(sizes) != check.PIXEL_AXES or min(sizes) <= 0:
            raise check.fail("pixel_size_deg must be two positive numbers")
        if not check.number(self.resolution_m, "resolution_m") > 0:
            raise check.fail("resolution_m must be positive")
        check.integer(self.zoom_min, "zoom_min")
        check.integer(self.zoom_max, "zoom_max")
        if not self.zoom_min <= self.zoom_max <= check.ZOOM_LIMIT:
            raise check.fail(
                f"zooms must satisfy zoom_min <= zoom_max <= {check.ZOOM_LIMIT}"
            )

    def _check_wording(self) -> None:
        if self.cloud_fraction is not None and not (
            0.0 <= check.number(self.cloud_fraction, "cloud_fraction") <= 1.0
        ):
            raise check.fail("cloud_fraction must lie in [0, 1]")
        check.text(self.cloud_method, "cloud_method", maximum=300)
        check.text(self.credit, "credit", maximum=500)
        check.text(self.licence, "licence", maximum=200)
        check.text(self.caption, "caption", maximum=check.TEXT_MAX * 2)
        check.text(self.caption_template, "caption_template", maximum=60)

    def _check_contents(self) -> None:
        if not all(isinstance(item, Annotation) for item in self.annotations):
            raise check.fail("annotations must be Annotation records")
        for item in self.annotations:
            if isinstance(item.geometry, Point) and not self.bounds.contains(
                item.geometry
            ):
                raise check.fail(
                    f"a {item.kind} marker lies outside the scene's bounds"
                )
        if not self.inputs:
            raise check.fail("a scene names the inputs it was made from")
        paths = [item.path for item in self.files]
        if len(set(paths)) != len(paths):
            raise check.fail("a file is listed more than once")
        missing = [name for name in REQUIRED_FILES if name not in paths]
        if missing:
            raise check.fail(f"a scene publishes {missing} as well")
        if not any(path.startswith("tiles/") for path in paths):
            raise check.fail("a scene publishes at least one tile")

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
            "acquired_at": check.instant(self.acquired_at, "acquired_at"),
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
        fields = check.fields(stored, _MANIFEST_FIELDS, "manifest")
        source_class = fields["source_class"]
        if source_class not in SOURCE_CLASSES:
            raise check.fail(check.not_a_class(source_class))
        cloud = fields["cloud_fraction"]
        sizes = tuple(
            check.number(item, "pixel_size_deg")
            for item in check.sequence(fields["pixel_size_deg"], "pixel_size_deg")
        )
        if len(sizes) != check.PIXEL_AXES:
            raise check.fail("pixel_size_deg must be two positive numbers")
        manifest = cls(
            area_id=check.integer(fields["area_id"], "area_id", minimum=1),
            area_label=check.text(fields["area_label"], "area_label"),
            source_id=check.text(fields["source_id"], "source_id"),
            source_class=source_class,
            platform=check.text(fields["platform"], "platform"),
            instrument=check.text(fields["instrument"], "instrument"),
            acquired_at=check.parse_instant(fields["acquired_at"], "acquired_at"),
            bounds=Bounds.from_list(fields["bounds"], "bounds"),
            pixel_size_deg=(sizes[0], sizes[1]),
            resolution_m=check.number(fields["resolution_m"], "resolution_m"),
            cloud_fraction=None
            if cloud is None
            else check.number(cloud, "cloud_fraction"),
            cloud_method=check.text(fields["cloud_method"], "cloud_method"),
            zoom_min=check.integer(fields["zoom_min"], "zoom_min"),
            zoom_max=check.integer(fields["zoom_max"], "zoom_max"),
            credit=check.text(fields["credit"], "credit"),
            licence=check.text(fields["licence"], "licence"),
            caption=check.text(
                fields["caption"], "caption", maximum=check.TEXT_MAX * 2
            ),
            caption_template=check.text(fields["caption_template"], "caption_template"),
            annotations=tuple(
                Annotation.from_json(item)
                for item in check.sequence(fields["annotations"], "annotations")
            ),
            inputs=tuple(
                Input.from_json(item)
                for item in check.sequence(fields["inputs"], "inputs")
            ),
            files=tuple(
                PublishedFile.from_json(item)
                for item in check.sequence(fields["files"], "files")
            ),
            crs=check.text(fields["crs"], "crs"),
            method=check.text(fields["method"], "method"),
            schema=check.text(fields["schema"], "schema"),
        )
        if fields["caption_sha256"] != manifest.caption_sha256:
            message = "the caption no longer hashes to the digest the manifest states"
            raise check.fail(message)
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
        if not isinstance(self.hash, str) or not check.HASH12.match(self.hash):
            raise check.fail(f"hash {self.hash!r} must be twelve lower-case hex digits")
        check.integer(self.area_id, "area_id", minimum=1)
        if self.source_class not in SOURCE_CLASSES:
            raise check.fail(check.not_a_class(self.source_class))
        check.source_id(self.source_id)
        check.instant(self.acquired_at, "acquired_at")
        check.instant(self.published_at, "published_at")
        check.integer(self.size_bytes, "size_bytes")

    def to_json(self) -> dict[str, object]:
        """The record as JSON-native values, in the shape it is published."""
        return {
            "hash": self.hash,
            "area_id": self.area_id,
            "source_class": self.source_class,
            "source_id": self.source_id,
            "acquired_at": check.instant(self.acquired_at, "acquired_at"),
            "published_at": check.instant(self.published_at, "published_at"),
            "size_bytes": self.size_bytes,
        }

    @classmethod
    def from_json(cls, stored: object) -> IndexEntry:
        """Read the record back, refusing a field this version does not know."""
        fields = check.fields(
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
            raise check.fail(check.not_a_class(source_class))
        return cls(
            check.text(fields["hash"], "hash"),
            check.integer(fields["area_id"], "area_id", minimum=1),
            source_class,
            check.text(fields["source_id"], "source_id"),
            check.parse_instant(fields["acquired_at"], "acquired_at"),
            check.parse_instant(fields["published_at"], "published_at"),
            check.integer(fields["size_bytes"], "size_bytes"),
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
            raise check.fail(f"schema must be {INDEX_SCHEMA}, not {self.schema!r}")
        hashes = [entry.hash for entry in self.scenes]
        if len(set(hashes)) != len(hashes):
            raise check.fail("a scene is listed more than once")

    def to_json(self) -> dict[str, object]:
        """The record as JSON-native values, in the shape it is published."""
        return {
            "schema": self.schema,
            "scenes": [entry.to_json() for entry in self.scenes],
        }

    @classmethod
    def from_json(cls, stored: object) -> ImageryIndex:
        """Read the record back, refusing a field this version does not know."""
        fields = check.fields(stored, frozenset({"schema", "scenes"}), "index")
        return cls(
            scenes=tuple(
                IndexEntry.from_json(item)
                for item in check.sequence(fields["scenes"], "scenes")
            ),
            schema=check.text(fields["schema"], "schema"),
        )
