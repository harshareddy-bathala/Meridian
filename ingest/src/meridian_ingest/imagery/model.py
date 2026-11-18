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

from meridian_ingest.imagery.model_checks import (
    ANNOTATION_KINDS,
    INDEX_SCHEMA,
    INPUT_KINDS,
    MANIFEST_SCHEMA,
    MAX_SIDE_PX,
    METHOD_VERSION,
    REQUIRED_FILES,
    SOURCE_CLASSES,
    AnnotationKind,
    InputKind,
    MalformedImageryError,
    SourceClass,
)
from meridian_ingest.imagery.model_files import Input, PublishedFile, SceneWindow
from meridian_ingest.imagery.model_geometry import (
    Annotation,
    Bounds,
    Citation,
    Geometry,
    Point,
    caption_sha256,
)
from meridian_ingest.imagery.model_manifest import (
    ImageryIndex,
    ImageryManifest,
    IndexEntry,
    manifest_sha256,
)

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
    "AnnotationKind",
    "Bounds",
    "Citation",
    "Geometry",
    "ImageryIndex",
    "ImageryManifest",
    "IndexEntry",
    "Input",
    "InputKind",
    "MalformedImageryError",
    "Point",
    "PublishedFile",
    "SceneWindow",
    "SourceClass",
    "caption_sha256",
    "manifest_sha256",
]
