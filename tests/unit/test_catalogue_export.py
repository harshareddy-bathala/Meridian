"""``meridian catalogue export`` writes a file ``load`` reads back unchanged.

The independence test's offline path (D-321): what one machine fetched becomes
a file another machine with no network brings a stack up from. So an export is
judged by reading it back with the loader's own reader.

Reference: docs/DECISIONS.md D-079, D-320, D-321.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from meridian.catalogue_export import catalogue_document, write_catalogue
from meridian.catalogue_file import read_catalogue
from meridian.store.catalogue_reads import CatalogueSatellite, CatalogueTransmitter
from meridian.store.element_sets import StoredElementSet

M2_3 = (
    "1 57166U 23091A   26306.50000000 -.00000020  00000+0  50000-5 0  9994",
    "2 57166  98.5900 358.5000 0004900 120.0000 240.1000 14.24055000174151",
)
EPOCH = datetime(2026, 11, 2, 12, tzinfo=UTC)
FETCHED = datetime(2026, 11, 2, 14, tzinfo=UTC)

SATELLITES = [
    CatalogueSatellite("norad:57166", "Meteor-M2-3", "leo"),
    CatalogueSatellite("norad:59051", "Meteor-M2-4", "leo"),
]
TRANSMITTERS = [
    CatalogueTransmitter("norad:57166", 137_900_000, "lrpt", "rhcp", 150_000, 0.113778),
    CatalogueTransmitter("norad:59051", 137_900_000, "lrpt", None, None, None),
]
SETS = {
    "norad:57166": StoredElementSet(
        id=7,
        satellite_id="norad:57166",
        epoch=EPOCH,
        retrieved_at=FETCHED,
        line1=M2_3[0],
        line2=M2_3[1],
        source="celestrak",
        content_sha256=b"",
    )
}


def test_an_export_reads_back_as_the_same_catalogue(tmp_path: Path) -> None:
    path = tmp_path / "live.json"
    write_catalogue(path, catalogue_document(SATELLITES, TRANSMITTERS, SETS))
    document = read_catalogue(path)

    assert [one.satellite_id for one in document.satellites] == [
        "norad:57166",
        "norad:59051",
    ]
    assert [
        (one.centre_freq_hz, one.frame_interval_s) for one in document.transmitters
    ] == [
        (137_900_000, 0.113778),
        (137_900_000, None),
    ]
    (loaded,) = document.element_sets
    assert (loaded.line1, loaded.line2, loaded.epoch) == (*M2_3, EPOCH)


def test_a_loaded_set_is_manual_and_its_provenance_only_a_note(
    tmp_path: Path,
) -> None:
    """A file cannot claim a provenance nothing checked (D-079)."""
    path = tmp_path / "live.json"
    write_catalogue(path, catalogue_document(SATELLITES, TRANSMITTERS, SETS))

    (loaded,) = read_catalogue(path).element_sets
    assert loaded.source == "manual"
    assert loaded.ingest_record_id is None
    noted = json.loads(path.read_text("utf-8"))["satellites"][0]["element_sets"][0]
    assert noted["exported_from"] == {
        "source": "celestrak",
        "epoch": "2026-11-02T12:00:00+00:00",
        "retrieved_at": "2026-11-02T14:00:00+00:00",
    }


def test_a_satellite_with_no_set_is_exported_with_none() -> None:
    document = _as_json()
    entries = {one["satellite_id"]: one for one in document["satellites"]}
    assert entries["norad:59051"]["element_sets"] == []


def test_an_optional_field_the_catalogue_lacks_is_left_out_not_null() -> None:
    document = _as_json()
    second = document["satellites"][1]["transmitters"][0]
    assert second == {"centre_freq_hz": 137_900_000, "mode": "lrpt"}


def test_a_rewrite_leaves_no_partial_file_behind(tmp_path: Path) -> None:
    path = tmp_path / "catalogue" / "live.json"
    write_catalogue(path, catalogue_document(SATELLITES, TRANSMITTERS, SETS))
    write_catalogue(path, catalogue_document(SATELLITES, TRANSMITTERS, {}))

    assert sorted(one.name for one in path.parent.iterdir()) == ["live.json"]
    assert read_catalogue(path).element_sets == ()


def _as_json() -> Any:
    """The document as a reader of the file would see it, untyped."""
    return json.loads(json.dumps(catalogue_document(SATELLITES, TRANSMITTERS, SETS)))
