"""``meridian.catalogue_file`` — reading a catalogue document, and refusing one.

No marker: parsing needs a filesystem and nothing else. The element sets below
are real in shape and checksum, which they have to be — the reader validates
both, and a fixture that failed validation would test the refusal path
everywhere by accident.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from meridian.catalogue_file import MalformedCatalogueError, read_catalogue

LINE1 = "1 57166U 23091A   26223.50000000  .00000100  00000-0  50000-4 0  9990"
LINE2 = "2 57166  98.7041 210.4322 0002726  80.4113 279.7297 14.22000000160126"

OTHER_LINE1 = "1 59051U 24039A   26223.50000000  .00000100  00000-0  50000-4 0  9998"
OTHER_LINE2 = "2 59051  98.6215  95.1180 0003410 120.7742 239.3688 14.22000000 90128"


def satellite(**overrides: Any) -> dict[str, Any]:
    """One catalogue entry, with the fields a test varies exposed."""
    entry: dict[str, Any] = {
        "satellite_id": "norad:57166",
        "name": "Meteor-M2-3",
        "transmitters": [{"centre_freq_hz": 137100000, "mode": "lrpt"}],
        "element_sets": [{"line1": LINE1, "line2": LINE2}],
    }
    entry.update(overrides)
    return entry


def written(tmp_path: Path, document: object) -> Path:
    """Put a document on disk and return its path."""
    path = tmp_path / "catalogue.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_a_document_becomes_the_rows_it_describes(tmp_path: Path) -> None:
    """One entry, three kinds of row, each carrying its satellite."""
    document = read_catalogue(written(tmp_path, {"satellites": [satellite()]}))

    assert [one.satellite_id for one in document.satellites] == ["norad:57166"]
    assert [one.centre_freq_hz for one in document.transmitters] == [137100000]
    assert [one.satellite_id for one in document.element_sets] == ["norad:57166"]


def test_the_epoch_is_read_from_the_lines_rather_than_declared(tmp_path: Path) -> None:
    """The epoch belongs to the element set, not to whoever transcribed it.

    A file that could state its own epoch could state one that disagreed with
    the lines, and element-set age — measured from this instant — would then be
    wrong for every prediction made from the set, with nothing to catch it.
    """
    document = read_catalogue(written(tmp_path, {"satellites": [satellite()]}))

    (element_set,) = document.element_sets
    assert element_set.epoch.isoformat() == "2026-08-11T12:00:00+00:00"


def test_element_sets_are_recorded_as_manual(tmp_path: Path) -> None:
    """A hand-held file cannot vouch for where its lines came from (D-079)."""
    document = read_catalogue(written(tmp_path, {"satellites": [satellite()]}))

    assert [one.source for one in document.element_sets] == ["manual"]


def test_several_satellites_keep_their_own_children(tmp_path: Path) -> None:
    """The flattening must not attach one satellite's downlink to another."""
    second = satellite(
        satellite_id="norad:59051",
        name="Meteor-M2-4",
        transmitters=[{"centre_freq_hz": 137900000, "mode": "lrpt"}],
        element_sets=[{"line1": OTHER_LINE1, "line2": OTHER_LINE2}],
    )

    document = read_catalogue(written(tmp_path, {"satellites": [satellite(), second]}))

    assert [
        (one.satellite_id, one.centre_freq_hz) for one in document.transmitters
    ] == [
        ("norad:57166", 137100000),
        ("norad:59051", 137900000),
    ]


def test_the_optional_fields_are_optional(tmp_path: Path) -> None:
    """Regime defaults to LEO; polarisation and bandwidth may simply be unknown."""
    document = read_catalogue(written(tmp_path, {"satellites": [satellite()]}))

    (one,) = document.satellites
    (transmitter,) = document.transmitters
    assert one.orbital_regime == "leo"
    assert transmitter.polarisation is None
    assert transmitter.bandwidth_hz is None


def test_a_missing_field_names_where_it_was_missing(tmp_path: Path) -> None:
    """A catalogue is edited by hand, so a refusal has to say which entry."""
    broken = satellite()
    del broken["name"]

    with pytest.raises(MalformedCatalogueError, match=r"satellites\[0\]\.name"):
        read_catalogue(written(tmp_path, {"satellites": [broken]}))


def test_a_frequency_that_is_not_whole_is_refused(tmp_path: Path) -> None:
    """Frequencies are integers in hertz — never floats, never megahertz."""
    broken = satellite(transmitters=[{"centre_freq_hz": 137.1, "mode": "lrpt"}])

    with pytest.raises(MalformedCatalogueError, match="centre_freq_hz"):
        read_catalogue(written(tmp_path, {"satellites": [broken]}))


def test_an_element_set_that_fails_its_checksum_is_refused(tmp_path: Path) -> None:
    """The reason the reader validates at all.

    The propagator accepts two lines of arbitrary text and hands back an epoch
    in 1999. A typo would therefore load without complaint, produce no passes
    over any station, and give nobody a reason why.
    """
    broken = satellite(element_sets=[{"line1": LINE1[:-1] + "7", "line2": LINE2}])

    with pytest.raises(MalformedCatalogueError, match=r"element_sets\[0\]"):
        read_catalogue(written(tmp_path, {"satellites": [broken]}))


def test_a_truncated_element_set_line_is_refused(tmp_path: Path) -> None:
    """Every field is read by column, so a short line is not a short element set."""
    broken = satellite(element_sets=[{"line1": LINE1[:40], "line2": LINE2}])

    with pytest.raises(MalformedCatalogueError, match=r"element_sets\[0\]"):
        read_catalogue(written(tmp_path, {"satellites": [broken]}))


def test_a_file_that_is_not_json_names_the_file(tmp_path: Path) -> None:
    """The operator's own path is the useful half of this message."""
    path = tmp_path / "catalogue.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(MalformedCatalogueError, match=r"catalogue\.json"):
        read_catalogue(path)


def test_a_json_array_is_not_a_catalogue(tmp_path: Path) -> None:
    """Valid JSON is not the same as the document this loader reads."""
    with pytest.raises(MalformedCatalogueError, match="not a catalogue object"):
        read_catalogue(written(tmp_path, [satellite()]))


def test_a_missing_satellites_array_is_refused(tmp_path: Path) -> None:
    """An empty object would otherwise load as a catalogue of nothing."""
    with pytest.raises(MalformedCatalogueError, match="satellites"):
        read_catalogue(written(tmp_path, {}))


def test_an_empty_catalogue_is_allowed(tmp_path: Path) -> None:
    """Nothing tracked yet is a state, not an error — a deployment starts there."""
    document = read_catalogue(written(tmp_path, {"satellites": []}))

    assert document.satellites == ()
    assert document.transmitters == ()
    assert document.element_sets == ()


def test_the_shipped_development_catalogue_reads(tmp_path: Path) -> None:  # noqa: ARG001
    """The file `docker compose --profile sim up` depends on must be loadable.

    Checked here rather than only in the compose gate, because a typo in it
    would otherwise surface as a simulator that registers and is never given
    anything to do — the hardest failure in this stage to trace back.
    """
    document = read_catalogue(Path("deploy/catalogue/development.json"))

    assert len(document.satellites) == 2
    assert {one.centre_freq_hz for one in document.transmitters} == {
        137100000,
        137900000,
    }
    assert all(one.source == "manual" for one in document.element_sets)
