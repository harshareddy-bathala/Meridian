"""Published scenes, as their index and manifests state them and the API serves them.

``meridian-ingest imagery refresh`` publishes each scene into
``<published>/imagery/<hash12>/`` with a ``manifest.json``
(``imagery-manifest-1``) and rewrites ``imagery/index.json``
(``imagery-index-1``) (D-375). The deployment names the index with
``IMAGERY_INDEX``.

**Both are checked, not trusted**, as the evidence index is (D-332, D-369):
- every schema string must match exactly, and no key may be unknown;
- a scene is named by twelve hex digits and listed once;
- a manifest lists the four files every scene publishes and at least one tile,
  each once, by a name a scene may have;
- every marker cites at least one record, and a caption hashes to the digest
  its manifest states;
- a box is never a polygon (D-326).

The API also checks what the models cannot see: a manifest's bytes hash to
the name it is published under, and its scene is the one the index entry says
(``meridian.api.public.imagery``).

This module imports nothing from ``meridian_ingest``, which the platform image
does not install (D-138). ``tests/unit/test_imagery_endpoint_models.py`` holds
its fields equal to the records the publisher writes.

Every scene is public archive imagery, credited, but for the demonstration's
synthetic scene, which every layer labels simulated (rule 5, D-378). None is
received by Meridian yet (D-086).
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "MANIFEST_SCHEMA",
    "REQUIRED_FILES",
    "ImageryIndexEntry",
    "ImageryIndexFile",
    "ImageryList",
    "ImageryScene",
    "ImagerySceneDetail",
    "SceneAnnotation",
    "SceneBox",
    "SceneCitation",
    "SceneFile",
    "SceneInput",
    "SceneManifest",
    "ScenePoint",
]

MANIFEST_SCHEMA = "imagery-manifest-1"
REQUIRED_FILES = ("scene.tif", "scene.png", "scene.pgw", "display.png")
"""The four files every scene publishes beside its tiles."""

HASH12 = r"^[0-9a-f]{12}$"
_SHA256 = r"^[0-9a-f]{64}$"
_SOURCE_ID = r"^[a-z][a-z0-9_]{1,63}$"
FILE_PATH = (
    r"^(?:tiles/[0-9]{1,2}/[0-9]{1,7}/[0-9]{1,7}\.webp"
    r"|[a-z0-9_]{1,40}\.(?:tif|png|pgw|txt))$"
)
"""A published file's path, as the publisher allows one: a tile, or one flat name."""

SourceClass = Literal["high_resolution", "older_record", "daily_wide"]
Latitude = Annotated[float, Field(ge=-90.0, le=90.0)]
Longitude = Annotated[float, Field(ge=-180.0, le=180.0)]


class _Strict(BaseModel):
    """A part of a published record: every key known, nothing coerced."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ImageryIndexEntry(_Strict):
    """One published scene, as the index names it."""

    hash: str = Field(pattern=HASH12)
    area_id: int = Field(ge=1)
    source_class: SourceClass
    source_id: str = Field(pattern=_SOURCE_ID)
    acquired_at: datetime
    published_at: datetime
    size_bytes: int = Field(ge=0)
    simulated: bool


class ImageryIndexFile(_Strict):
    """``imagery-index-1``: every scene published, newest acquisition first."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        validate_by_name=True,
        serialize_by_alias=True,
    )

    schema_: Literal["imagery-index-1"] = Field(alias="schema")
    scenes: list[ImageryIndexEntry]

    @model_validator(mode="after")
    def _each_once(self) -> Self:
        names = [one.hash for one in self.scenes]
        if len(names) != len(set(names)):
            raise ValueError(f"a scene is listed twice: {names}")
        return self


class ScenePoint(_Strict):
    """A fire or a place: a point on the ground."""

    type: Literal["point"]
    lat_deg: Latitude
    lon_deg: Longitude


class SceneBox(_Strict):
    """An area or its change: the published box, never its polygon (D-326)."""

    type: Literal["box"]
    bbox: list[float] = Field(min_length=4, max_length=4)


class SceneCitation(_Strict):
    """The record a marker was placed from."""

    source_id: str = Field(pattern=_SOURCE_ID)
    record_id: str = Field(min_length=1, max_length=300)
    url: str = Field(pattern=r"^(?:https://|/api/v1/)")
    licence: str = Field(min_length=1)


class SceneAnnotation(_Strict):
    """One marker: what it is, where, and the records it cites."""

    kind: Literal["fire", "area", "change", "place"]
    geometry: Annotated[ScenePoint | SceneBox, Field(discriminator="type")]
    label: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1)
    citations: list[SceneCitation] = Field(min_length=1)


class SceneInput(_Strict):
    """Something the scene was made from, named by its digest."""

    kind: Literal[
        "catalogue",
        "classification",
        "scene",
        "firms",
        "gazetteer",
        "areas_summary",
        "template",
    ]
    ref: str = Field(min_length=1, max_length=300)
    sha256: str = Field(pattern=_SHA256)


class SceneFile(_Strict):
    """One file in the scene's directory, with what it must hash to."""

    path: str = Field(pattern=FILE_PATH)
    sha256: str = Field(pattern=_SHA256)
    size_bytes: int = Field(ge=0)


class SceneManifest(_Strict):
    """``imagery-manifest-1``: what a scene shows, how it was made, and its files."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        validate_by_name=True,
        serialize_by_alias=True,
    )

    schema_: Literal["imagery-manifest-1"] = Field(alias="schema")
    method: Literal["imagery-1"]
    area_id: int = Field(ge=1)
    area_label: str = Field(min_length=1)
    source_id: str = Field(pattern=_SOURCE_ID)
    source_class: SourceClass
    platform: str = Field(min_length=1)
    instrument: str = Field(min_length=1)
    acquired_at: datetime
    bounds: list[float] = Field(min_length=4, max_length=4)
    crs: Literal["EPSG:4326"]
    pixel_size_deg: list[float] = Field(min_length=2, max_length=2)
    resolution_m: float = Field(gt=0)
    cloud_fraction: float | None = Field(ge=0.0, le=1.0)
    cloud_method: str = Field(min_length=1)
    rendering: str = Field(min_length=1)
    zoom_min: int = Field(ge=0, le=22)
    zoom_max: int = Field(ge=0, le=22)
    simulated: bool
    """True for the demonstration's synthetic scene alone (rule 5, D-378)."""

    credit: str = Field(min_length=1)
    licence: str = Field(min_length=1)
    caption: str = Field(min_length=1)
    caption_sha256: str = Field(pattern=_SHA256)
    caption_template: str = Field(min_length=1)
    annotations: list[SceneAnnotation]
    inputs: list[SceneInput] = Field(min_length=1)
    files: list[SceneFile]

    @model_validator(mode="after")
    def _whole(self) -> Self:
        if hashlib.sha256(self.caption.encode()).hexdigest() != self.caption_sha256:
            raise ValueError("the caption does not hash to the digest it states")
        if self.zoom_min > self.zoom_max:
            raise ValueError("zoom_min is above zoom_max")
        paths = [one.path for one in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("a file is listed twice")
        missing = [name for name in REQUIRED_FILES if name not in paths]
        if missing or not any(path.startswith("tiles/") for path in paths):
            raise ValueError(f"a scene publishes {missing} and a tile as well")
        return self

    def file(self, path: str) -> SceneFile | None:
        """The listed file at ``path``, or ``None``."""
        return next((one for one in self.files if one.path == path), None)


class ImageryScene(BaseModel):
    """One published scene as the gallery lists it: enough to draw its card."""

    hash: str
    area_id: int
    area_label: str
    source_class: SourceClass
    source_id: str
    platform: str
    instrument: str
    acquired_at: datetime
    published_at: datetime
    cloud_fraction: float | None
    caption: str
    """The picture's alt text as well as its caption."""

    credit: str
    display: str
    """The display picture's address, ``/imagery/<hash>/display.png``."""

    size_bytes: int
    simulated: bool
    """True only for the demonstration's synthetic scene; an archive's scene is
    its archive's measurement (rule 5, D-378)."""


class ImageryList(BaseModel):
    """One page of published scenes, newest acquisition first."""

    status: Literal["computed"] = "computed"
    items: list[ImageryScene]
    next_cursor: str | None = None


class ImagerySceneDetail(BaseModel):
    """One published scene: its manifest, and where its files are served."""

    status: Literal["computed"] = "computed"
    hash: str
    published_at: datetime
    base: str
    """Every listed file is at ``base`` + its path; ``manifest.json`` too."""

    manifest: SceneManifest
    simulated: bool
