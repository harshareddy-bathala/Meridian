"""``meridian.datasets.manifest`` — what names a snapshot directory, and what may not.

The property the gate rests on is at the top: the hash covers everything the
directory holds and nothing about when it was made. The rest is what a parser
of a file nobody else should have written must refuse.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from meridian.datasets.manifest import (
    DamagedManifestError,
    FileEntry,
    MalformedManifestError,
    Manifest,
    SourceEntry,
    content_sha256,
    file_entry,
    manifest_bytes,
    parse_manifest,
)

SINCE = datetime(2026, 8, 1, tzinfo=UTC)
AS_OF = datetime(2026, 9, 23, 6, 30, 12, 345000, tzinfo=UTC)
CREATED = datetime(2026, 9, 23, 6, 30, 14, 2000, tzinfo=UTC)
RAW_HASH = hashlib.sha256(b"a raw snapshot").digest()
CONFIG_HASH = hashlib.sha256(b"settle_margin_s = 86400").digest()


def raw(**overrides: Any) -> Manifest:
    """A raw snapshot's manifest, files already in name order."""
    fields: dict[str, Any] = {
        "kind": "raw_snapshot",
        "schema_revision": "0016",
        "since": SINCE,
        "as_of": AS_OF,
        "files": (
            file_entry("observations.jsonl", b'{"a":1}\n{"a":2}\n'),
            file_entry("passes.jsonl", b'{"p":1}\n'),
        ),
        "created_at": CREATED,
        "counts": {"passes.measured": 1, "passes.simulated": 0},
        "sources": (
            SourceEntry(
                source_id="reference_archive",
                licence="Apache-2.0",
                terms_url="https://example.org/terms",
                attribution_entry="The reference adapter's fixtures are ours",
                records=2,
            ),
        ),
    }
    return Manifest(**(fields | overrides))


def evaluation(**overrides: Any) -> Manifest:
    """An evaluation dataset's manifest, naming its inputs."""
    fields: dict[str, Any] = {
        "kind": "evaluation_dataset",
        "files": (file_entry("labels.jsonl", b'{"label":"confirmed_miss"}\n'),),
        "sources": (),
        "derived_from": RAW_HASH,
        "transformation_version": "labels-1",
        "config_sha256": CONFIG_HASH,
        "parameters": {"settle_margin_s": 86400, "silent_window_s": 43200},
    }
    return raw(**(fields | overrides))


# --- the hash ---------------------------------------------------------------


def test_when_it_was_made_is_not_part_of_the_hash() -> None:
    """Labelled twice, the same inputs give the same hash — the gate."""
    later = raw(created_at=CREATED + timedelta(days=3))

    assert content_sha256(later) == content_sha256(raw())


def test_the_order_files_were_listed_in_is_not_part_of_the_hash() -> None:
    files = raw().files

    assert content_sha256(raw(files=tuple(reversed(files)))) == content_sha256(raw())


@pytest.mark.parametrize(
    "change",
    [
        {"schema_revision": "0017"},
        {"since": SINCE + timedelta(days=1)},
        {"as_of": AS_OF + timedelta(seconds=1)},
        {"files": (file_entry("passes.jsonl", b'{"p":2}\n'),)},
        {"counts": {"passes.measured": 2, "passes.simulated": 0}},
        {"sources": ()},
    ],
    ids=["schema", "since", "as_of", "a-file", "counts", "sources"],
)
def test_anything_the_directory_holds_changes_the_hash(change: dict[str, Any]) -> None:
    assert content_sha256(raw(**change)) != content_sha256(raw())


@pytest.mark.parametrize(
    "change",
    [
        {"derived_from": hashlib.sha256(b"another snapshot").digest()},
        {"transformation_version": "labels-2"},
        {"config_sha256": hashlib.sha256(b"settle_margin_s = 3600").digest()},
        {"parameters": {"settle_margin_s": 3600, "silent_window_s": 43200}},
        {"summary": {"populations": {"own": {"ess": 2.5}}}},
    ],
    ids=["raw-snapshot", "transformation", "config", "parameters", "summary"],
)
def test_an_evaluation_datasets_inputs_change_its_hash(change: dict[str, Any]) -> None:
    assert content_sha256(evaluation(**change)) != content_sha256(evaluation())


def test_a_summary_reads_back_as_it_was_written() -> None:
    """Floats, nulls and nesting survive the round trip, so the hash does too."""
    summary = {"populations": {"own": {"ess": 8 / 3, "unweighted": None}}}
    manifest = evaluation(summary=summary)

    assert parse_manifest(manifest_bytes(manifest)).summary == summary


def test_a_manifest_without_a_summary_is_written_as_it_always_was() -> None:
    """Stage 15's manifests keep their hash: no summary, no ``summary`` key."""
    written = json.loads(manifest_bytes(evaluation()))

    assert "summary" not in written
    assert parse_manifest(manifest_bytes(evaluation())).summary == {}


def test_a_file_entry_is_measured_from_its_bytes() -> None:
    entry = file_entry("passes.jsonl", b'{"p":1}\n{"p":2}\n')

    assert entry.rows == 2
    assert entry.sha256 == hashlib.sha256(b'{"p":1}\n{"p":2}\n').digest()
    assert file_entry("passes.jsonl", b"").rows == 0


def test_a_file_cut_short_is_refused() -> None:
    """Otherwise it counts one row fewer than it holds and still looks whole."""
    with pytest.raises(MalformedManifestError, match="newline"):
        file_entry("passes.jsonl", b'{"p":1}\n{"p":')


# --- written and read back ------------------------------------------------


@pytest.mark.parametrize("manifest", [raw(), evaluation()], ids=["raw", "evaluation"])
def test_a_manifest_reads_back_as_it_was_written(manifest: Manifest) -> None:
    assert parse_manifest(manifest_bytes(manifest)) == manifest


def test_the_written_file_says_when_and_states_its_own_hash() -> None:
    written = json.loads(manifest_bytes(raw()))

    assert written["created_at"] == "2026-09-23T06:30:14.002Z"
    assert written["content_sha256"] == content_sha256(raw()).hex()
    assert manifest_bytes(raw()).endswith(b"}\n")


def test_the_written_bytes_are_the_same_every_time() -> None:
    assert manifest_bytes(raw()) == manifest_bytes(raw())


def test_a_manifest_edited_after_it_was_written_is_damaged() -> None:
    """It still parses; it no longer hashes to what it says."""
    written = json.loads(manifest_bytes(raw()))
    written["files"][0]["rows"] = 3

    with pytest.raises(DamagedManifestError, match="changed after it was written"):
        parse_manifest(json.dumps(written).encode())


@pytest.mark.parametrize(
    ("edit", "match"),
    [
        (lambda m: m.update(format=2), "unknown manifest format"),
        (lambda m: m.update(extra=1), "unknown \\['extra'\\]"),
        (lambda m: m.pop("counts"), "missing \\['counts'\\]"),
        (lambda m: m.update(kind="snapshot"), "unknown snapshot kind"),
        (lambda m: m.update(since="2026-08-01T00:00:00+05:30"), "UTC"),
        (lambda m: m["files"][0].update(rows=True), "non-negative integer"),
        (lambda m: m["files"][0].update(rows="2"), "non-negative integer"),
        (lambda m: m["files"][0].update(sha256="AB" * 32), "lowercase hex"),
        (lambda m: m["files"][0].update(size=1), "has fields"),
    ],
    ids=[
        "format",
        "unknown-field",
        "missing-field",
        "kind",
        "offset",
        "bool-count",
        "text-count",
        "upper-hex",
        "extra-file-field",
    ],
)
def test_a_manifest_this_writer_did_not_write_is_refused(edit: Any, match: str) -> None:
    written = json.loads(manifest_bytes(raw()))
    edit(written)

    with pytest.raises(MalformedManifestError, match=match):
        parse_manifest(json.dumps(written).encode())


def test_bytes_that_are_not_json_are_refused() -> None:
    with pytest.raises(MalformedManifestError, match="not readable JSON"):
        parse_manifest(b"\xff{")


# --- what a manifest cannot say ---------------------------------------------


def test_an_evaluation_dataset_must_name_its_inputs() -> None:
    with pytest.raises(
        MalformedManifestError, match="names the directory it was made from"
    ):
        evaluation(config_sha256=None)


@pytest.mark.parametrize(
    "lineage",
    [
        {"derived_from": RAW_HASH},
        {"transformation_version": "labels-1"},
        {"parameters": {"settle_margin_s": 1}},
        {"summary": {"populations": {}}},
    ],
    ids=["derived-from", "transformation", "parameters", "summary"],
)
def test_a_raw_snapshot_is_not_derived_from_anything(lineage: dict[str, Any]) -> None:
    with pytest.raises(MalformedManifestError, match="read from the database"):
        raw(**lineage)


def test_a_file_listed_twice_is_refused() -> None:
    """The second entry would hide the first."""
    entry = file_entry("passes.jsonl", b"")

    with pytest.raises(MalformedManifestError, match="more than once"):
        raw(files=(entry, entry))


@pytest.mark.parametrize("name", ["passes.json", "../passes.jsonl", "Passes.jsonl"])
def test_a_file_name_that_is_not_a_table_is_refused(name: str) -> None:
    with pytest.raises(MalformedManifestError, match="not a snapshot file name"):
        FileEntry(name=name, sha256=bytes(32), rows=0)


def test_a_digest_that_is_not_a_sha256_is_refused() -> None:
    with pytest.raises(MalformedManifestError, match="not a sha256"):
        FileEntry(name="passes.jsonl", sha256=bytes(31), rows=0)


def test_an_interval_that_ends_before_it_starts_is_refused() -> None:
    with pytest.raises(MalformedManifestError, match="after as_of"):
        raw(since=AS_OF + timedelta(seconds=1))


def test_a_negative_count_is_refused() -> None:
    with pytest.raises(MalformedManifestError, match="is not a count"):
        raw(counts={"passes.measured": -1})


def test_the_manifest_cannot_be_changed_once_made() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        raw().kind = "evaluation_dataset"  # type: ignore[misc]


# --- an evaluation report (D-235) ------------------------------------------


def report(**overrides: Any) -> Manifest:
    """An evaluation report's manifest: results, a report, its configuration."""
    fields: dict[str, Any] = {
        "kind": "evaluation_report",
        "files": (
            file_entry("config.toml", b"[labels]\nsettle_margin_s = 86400"),
            file_entry("data.jsonl", b'{"row":"label"}\n'),
            file_entry("report.md", b"# Meridian evaluation report\n"),
        ),
        "derived_from": RAW_HASH,
        "transformation_version": "report-1",
        "config_sha256": CONFIG_HASH,
        "parameters": {"seed": 4471},
        "environment": {"code": {"commit": "abc", "dirty": False}},
    }
    return raw(**(fields | overrides))


def test_the_machine_that_made_a_report_is_not_part_of_its_hash() -> None:
    other = report(environment={"code": {"commit": "def", "dirty": True}})

    assert content_sha256(report()) == content_sha256(other)


def test_a_reports_environment_reads_back_and_is_written_beside_the_hash() -> None:
    written = manifest_bytes(report())

    assert parse_manifest(written).environment == report().environment
    assert json.loads(written)["environment"]["code"]["commit"] == "abc"


def test_an_edited_environment_still_verifies_because_it_names_nothing() -> None:
    """The cost of leaving it unhashed, stated as a test: a record, not a claim."""
    stored = json.loads(manifest_bytes(report()))
    stored["environment"]["code"]["commit"] = "edited"

    assert parse_manifest(json.dumps(stored).encode()).environment["code"] == {
        "commit": "edited",
        "dirty": False,
    }


def test_only_a_report_records_an_environment() -> None:
    with pytest.raises(MalformedManifestError, match="records no environment"):
        evaluation(environment={"python": "3.11"})


def test_a_reports_configuration_need_not_end_with_a_newline() -> None:
    """A file a person wrote is kept byte for byte; only a table must end whole."""
    entry = file_entry("config.toml", b"seed_free = true")

    assert entry.rows == 0


@pytest.mark.parametrize("name", ["report.md", "config.toml", "figure.svg"])
def test_a_report_file_in_a_raw_snapshot_is_refused(name: str) -> None:
    with pytest.raises(MalformedManifestError, match="does not hold"):
        raw(files=(file_entry(name, b"x\n"),))


def test_a_model_file_in_a_report_is_refused() -> None:
    with pytest.raises(MalformedManifestError, match=r"does not hold model\.json"):
        report(files=(file_entry("model.json", b"{}\n"),))


def test_a_report_holds_figures() -> None:
    held = report(files=(file_entry("reliability_a.svg", b"<svg/>\n"),))

    assert [one.name for one in held.files] == ["reliability_a.svg"]
