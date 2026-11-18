"""What a scene is read from and published as: its window, its inputs, its files.

Reference: docs/DECISIONS.md D-141, D-371, D-372.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from meridian_ingest.imagery import model_checks as check
from meridian_ingest.imagery.model_checks import INPUT_KINDS, MAX_SIDE_PX, InputKind


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
        check.source_id(self.source_id)
        check.text(self.item_id, "item_id", maximum=200)
        check.text(self.asset, "asset", maximum=60)
        href = check.https(self.asset_href, "asset_href")
        if "?" in href or "#" in href:
            raise check.fail(
                "asset_href is stored without its query or fragment (D-372)"
            )
        check.instant(self.acquired_at, "acquired_at")
        if not isinstance(self.crs, str) or not check.EPSG.match(self.crs):
            raise check.fail(
                f"crs {self.crs!r} must be an EPSG code such as EPSG:32643"
            )
        if len(self.transform) != check.AFFINE_TERMS:
            raise check.fail("transform must be the six affine coefficients")
        for coefficient in self.transform:
            check.number(coefficient, "transform")
        for name, side in (("width", self.width), ("height", self.height)):
            check.integer(side, name, minimum=1)
            if side > MAX_SIDE_PX:
                raise check.fail(f"{name} {side} exceeds {MAX_SIDE_PX} px (D-372)")
        check.integer(self.overview_level, "overview_level")
        if self.etag is not None:
            check.text(self.etag, "etag", maximum=200)

    def to_json(self) -> dict[str, object]:
        """The record as JSON-native values, in the shape it is published."""
        return {
            "source_id": self.source_id,
            "item_id": self.item_id,
            "asset": self.asset,
            "asset_href": self.asset_href,
            "acquired_at": check.instant(self.acquired_at, "acquired_at"),
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
        fields = check.fields(
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
            check.number(item, "transform")
            for item in check.sequence(fields["transform"], "transform")
        ]
        if len(terms) != check.AFFINE_TERMS:
            raise check.fail("transform must be the six affine coefficients")
        a, b, c, d, e, f = terms
        etag = fields["etag"]
        return cls(
            check.text(fields["source_id"], "source_id"),
            check.text(fields["item_id"], "item_id"),
            check.text(fields["asset"], "asset"),
            check.text(fields["asset_href"], "asset_href"),
            check.parse_instant(fields["acquired_at"], "acquired_at"),
            check.text(fields["crs"], "crs"),
            (a, b, c, d, e, f),
            check.integer(fields["width"], "width", minimum=1),
            check.integer(fields["height"], "height", minimum=1),
            check.integer(fields["overview_level"], "overview_level"),
            None if etag is None else check.text(etag, "etag"),
        )


@dataclass(frozen=True)
class PublishedFile:
    """One file in a scene's directory, by its path, digest and size."""

    path: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        """Refuse a record that breaks one of this module's rules."""
        if not isinstance(self.path, str) or not check.FILE.match(self.path):
            raise check.fail(f"file path {self.path!r} is not one a scene publishes")
        if not isinstance(self.sha256, str) or not check.SHA256.match(self.sha256):
            raise check.fail(f"{self.path}: sha256 must be 64 lower-case hex digits")
        check.integer(self.size_bytes, "size_bytes")

    def to_json(self) -> dict[str, object]:
        """The record as JSON-native values, in the shape it is published."""
        return {"path": self.path, "sha256": self.sha256, "size_bytes": self.size_bytes}

    @classmethod
    def from_json(cls, stored: object) -> PublishedFile:
        """Read the record back, refusing a field this version does not know."""
        fields = check.fields(
            stored, frozenset({"path", "sha256", "size_bytes"}), "file"
        )
        return cls(
            check.text(fields["path"], "path"),
            check.text(fields["sha256"], "sha256"),
            check.integer(fields["size_bytes"], "size_bytes"),
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
            raise check.fail(f"input kind {self.kind!r} is not one of {INPUT_KINDS}")
        check.text(self.ref, "ref", maximum=300)
        if not isinstance(self.sha256, str) or not check.SHA256.match(self.sha256):
            raise check.fail(
                f"input {self.ref}: sha256 must be 64 lower-case hex digits"
            )

    def to_json(self) -> dict[str, object]:
        """The record as JSON-native values, in the shape it is published."""
        return {"kind": self.kind, "ref": self.ref, "sha256": self.sha256}

    @classmethod
    def from_json(cls, stored: object) -> Input:
        """Read the record back, refusing a field this version does not know."""
        fields = check.fields(stored, frozenset({"kind", "ref", "sha256"}), "input")
        kind = fields["kind"]
        if kind not in INPUT_KINDS:
            raise check.fail(f"input kind {kind!r} is not one of {INPUT_KINDS}")
        return cls(
            kind,
            check.text(fields["ref"], "ref"),
            check.text(fields["sha256"], "sha256"),
        )
