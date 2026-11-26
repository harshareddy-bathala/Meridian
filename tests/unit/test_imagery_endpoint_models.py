"""The API reads exactly what the publisher writes (D-138, D-376).

The platform does not install ``meridian-ingest``, so ``/api/v1/imagery``
checks the published index and manifests with strict models of its own. This
holds the two equal: a manifest and an index the publisher's records write are
read by the API's models unchanged, and the rules that name a file or a scene
are the same text on both sides.

Marked as a unit test by living in ``tests/unit``: imports both, runs nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime

from meridian.api.public.models import imagery as api
from meridian_ingest.imagery import model_checks
from meridian_ingest.imagery.model import (
    Annotation,
    Bounds,
    Citation,
    ImageryIndex,
    ImageryManifest,
    IndexEntry,
    Input,
    Point,
    PublishedFile,
)
from meridian_ingest.raw_manifest import canonical_bytes

BOX = Bounds(77.58, 12.96, 77.60, 12.98)
ACQUIRED = datetime(2026, 11, 16, 5, 21, tzinfo=UTC)
DIGEST = "ab" * 32


def a_manifest() -> ImageryManifest:
    cite = Citation("nasa_firms", "0123456789ab row 1", "https://x.example/", "open")
    return ImageryManifest(
        area_id=3,
        area_label="Bengaluru urban",
        source_id="sentinel2_l2a",
        source_class="high_resolution",
        platform="Sentinel-2B",
        instrument="MSI",
        acquired_at=ACQUIRED,
        bounds=BOX,
        pixel_size_deg=(0.0001, 0.0001),
        resolution_m=10.0,
        cloud_fraction=None,
        cloud_method="not counted",
        rendering="as published",
        zoom_min=9,
        zoom_max=12,
        credit="Contains modified Copernicus Sentinel data 2026",
        licence="Copernicus free, full and open",
        caption="A caption.",
        caption_template="imagery-caption-1",
        annotations=(
            Annotation("area", BOX, "Bengaluru urban", "the box", (cite,)),
            Annotation("fire", Point(12.97, 77.59), "Fire", "a fire", (cite,)),
        ),
        inputs=tuple(
            Input(kind, f"ref/{kind}", DIGEST) for kind in model_checks.INPUT_KINDS
        ),
        files=tuple(
            PublishedFile(path, DIGEST, 10)
            for path in (*model_checks.REQUIRED_FILES, "tiles/12/2918/1898.webp")
        ),
    )


def test_a_manifest_the_publisher_writes_is_read_by_the_api_unchanged() -> None:
    written = a_manifest().to_json()

    read = api.SceneManifest.model_validate_json(canonical_bytes(written))

    assert read.model_dump(mode="json", by_alias=True) == written | {
        "acquired_at": "2026-11-16T05:21:00Z"
    }


def test_an_index_the_publisher_writes_is_read_by_the_api_unchanged() -> None:
    entry = IndexEntry(
        "0123456789ab", 3, "older_record", "landsat_c2_l2", ACQUIRED, ACQUIRED, 99
    )
    written = ImageryIndex(scenes=(entry,)).to_json()

    read = api.ImageryIndexFile.model_validate_json(canonical_bytes(written))

    assert read.model_dump(mode="json", by_alias=True) == written


def test_both_sides_name_files_classes_and_inputs_by_the_same_rules() -> None:
    assert model_checks.FILE.pattern == api.FILE_PATH
    assert model_checks.HASH12.pattern == api.HASH12
    assert api.REQUIRED_FILES == model_checks.REQUIRED_FILES
    assert api.MANIFEST_SCHEMA == model_checks.MANIFEST_SCHEMA
    assert set(api.SourceClass.__args__) == set(model_checks.SOURCE_CLASSES)
    kinds = api.SceneInput.model_fields["kind"].annotation
    assert set(kinds.__args__) == set(model_checks.INPUT_KINDS)
