"""The raw store: written once, readable forever, and no remote string in a path.

Stage 14's completion gate is that a snapshot is downloaded once and normalised
repeatedly with nothing reachable. Everything the gate rests on is here, so
these tests are mostly about what the store *refuses* — an artefact that can be
overwritten, silently truncated, or reached by a path from the database is a
gate that proves nothing.

No network and no database: a raw store is a directory.

Reference: docs/DECISIONS.md D-141.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from meridian_ingest import raw_layout
from meridian_ingest.provenance import IncompleteProvenanceError, Provenance
from meridian_ingest.raw_layout import (
    ARTEFACT_NAME,
    INCOMING,
    MANIFEST_NAME,
    CorruptArtefactError,
    EmptyArtefactError,
    MissingArtefactError,
    RawStoreError,
)
from meridian_ingest.raw_manifest import (
    MANIFEST_FORMAT,
    MalformedManifestError,
    manifest_bytes,
    parse_manifest,
)
from meridian_ingest.raw_store import RawStore

SOURCE = "reference_archive"
RETRIEVED_AT = datetime(2026, 9, 20, 10, 11, 43, tzinfo=UTC)
BODY = b'{"receptions": []}'
ABANDONED = "0" * 32
"""A scratch directory a crashed run left behind: a uuid4 hex, never a record."""


@pytest.fixture
def store(tmp_path: Path) -> Iterator[RawStore]:
    """A store under ``tmp_path``, left writable so the temporary tree can go.

    Published directories are sealed read-only, which is the property under
    test; pytest's own cleanup of an old ``tmp_path`` should not be the place
    that discovers it.
    """
    root = tmp_path / "raw"
    yield RawStore(root)
    for path in sorted(root.rglob("*"), reverse=True):
        path.chmod(0o700 if path.is_dir() else 0o600)


def a_provenance(**overrides: object) -> Provenance:
    fields: dict[str, object] = {
        "source_id": SOURCE,
        "original_identifier": "receptions/2026-08.json",
        "source_version": 'W/"3f9a1c7"',
        "payload_kind": "data",
        "retrieved_at": RETRIEVED_AT,
        "media_type": "application/json",
    }
    fields.update(overrides)
    return Provenance(**fields)  # type: ignore[arg-type]


def test_a_published_artefact_reads_back_byte_for_byte(store: RawStore) -> None:
    published = store.publish(a_provenance(), [BODY[:4], BODY[4:]])

    stored = store.read(published.raw_path)

    assert published.written is True
    assert stored.read_bytes() == BODY
    assert stored.manifest.byte_count == len(BODY)
    assert stored.manifest.sha256 == hashlib.sha256(BODY).digest()
    assert stored.manifest.provenance == a_provenance()


def test_the_path_is_our_timestamp_and_our_digest_and_nothing_else(
    store: RawStore,
) -> None:
    """D-141's load-bearing property: no string from a source becomes a path.

    The identifier below is what a careless writer would turn into a filename.
    It is stored verbatim — refusing it would be treating it as a path, which
    is the mistake — and it simply never appears in one.
    """
    hostile = "../../etc/passwd"

    published = store.publish(a_provenance(original_identifier=hostile), [BODY])

    digest = hashlib.sha256(BODY).hexdigest()[:12]
    assert published.raw_path == f"{SOURCE}/20260920T101143Z-{digest}"
    assert store.read(published.raw_path).manifest.provenance.original_identifier == (
        hostile
    )
    assert "passwd" not in str(sorted(store.root.rglob("*")))


def test_a_published_directory_cannot_be_written_into(store: RawStore) -> None:
    published = store.publish(a_provenance(), [BODY])
    directory = store.root / published.raw_path

    with pytest.raises(PermissionError):
        (directory / "afterthought.txt").write_text("no", encoding="utf-8")
    with pytest.raises(PermissionError):
        (directory / ARTEFACT_NAME).write_bytes(b"different")


def test_the_same_artefact_at_the_same_instant_is_one_record(store: RawStore) -> None:
    """The retry after a crash between the rename and the database insert.

    Those two are not one transaction and cannot be, so publication has to be
    resumable — and resuming must not be a second copy of the bytes.
    """
    first = store.publish(a_provenance(), [BODY])
    second = store.publish(a_provenance(), [BODY])

    assert second.raw_path == first.raw_path
    assert second.written is False
    assert store.scan(SOURCE) == (first.raw_path,)


def test_the_same_bytes_fetched_later_are_a_second_record(store: RawStore) -> None:
    """Retrieval is an event. Two fetches happened, so two directories exist.

    Whether they are one *row* is ``ingest_records``' question, answered by its
    unique constraint on the digest — not this layer's.
    """
    first = store.publish(a_provenance(), [BODY])
    later = store.publish(
        a_provenance(retrieved_at=RETRIEVED_AT + timedelta(days=1)), [BODY]
    )

    assert later.written is True
    assert store.scan(SOURCE) == (first.raw_path, later.raw_path)


def test_scan_lists_records_in_retrieval_order(store: RawStore) -> None:
    """Which is name order, because the name begins with the retrieval instant.

    A normalisation run has to process a snapshot the same way twice for the
    gate's repeat runs to compare, and a directory listing promises nothing.
    """
    instants = [RETRIEVED_AT + timedelta(hours=hours) for hours in (5, 1, 3)]
    for index, moment in enumerate(instants):
        store.publish(a_provenance(retrieved_at=moment), [BODY, bytes([index])])

    listed = store.scan(SOURCE)

    assert [path.split("/")[1][:15] for path in listed] == [
        "20260920T111143",
        "20260920T131143",
        "20260920T151143",
    ]


def test_a_half_written_directory_is_not_a_record(store: RawStore) -> None:
    """A crash mid-publication leaves bytes with no manifest. Nothing sees them.

    Not skipped by a rule that could be forgotten: a record is a directory
    whose *name* is a timestamp and a digest, and a scratch directory's name is
    a uuid.
    """
    scratch = store.root / SOURCE / INCOMING / ABANDONED
    scratch.mkdir(parents=True)
    (scratch / ARTEFACT_NAME).write_bytes(BODY)

    assert store.scan(SOURCE) == ()
    assert store.sweep(SOURCE) == (ABANDONED,)
    assert not scratch.exists()


def test_sweeping_a_source_never_fetched_is_quiet(store: RawStore) -> None:
    assert store.sweep(SOURCE) == ()


def test_publishing_leaves_no_scratch_behind(store: RawStore) -> None:
    store.publish(a_provenance(), [BODY])

    assert store.sweep(SOURCE) == ()


def test_verify_accepts_an_intact_record(store: RawStore) -> None:
    published = store.publish(a_provenance(), [BODY])

    store.verify(published.raw_path)


def test_a_changed_byte_is_found_by_verify(store: RawStore) -> None:
    """What makes ``meridian-ingest verify`` worth an exit code of its own.

    The store cannot stop a disk rotting or an operator editing a file; it can
    make the damage nameable rather than a quietly different dataset.
    """
    published = store.publish(a_provenance(), [BODY])
    artefact = store.root / published.raw_path / ARTEFACT_NAME
    artefact.chmod(0o600)
    artefact.write_bytes(BODY.replace(b"[]", b"[1]"))

    with pytest.raises(CorruptArtefactError, match="manifest records"):
        store.verify(published.raw_path)


def test_an_artefact_with_no_bytes_is_refused(store: RawStore) -> None:
    """Far more often a fetch that failed politely than a source with nothing."""
    with pytest.raises(EmptyArtefactError):
        store.publish(a_provenance(), [])

    assert store.scan(SOURCE) == ()
    assert store.sweep(SOURCE) == ()


def test_bytes_that_did_not_reach_the_disk_are_refused(
    store: RawStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The re-read is a separate claim from the stream, and has to be able to fail.

    A short write that returned no error satisfies the streaming digest and
    nothing else, so the guard is stubbed into failing here — without this the
    second hash could be inert and every test would still pass.
    """
    monkeypatch.setattr(
        raw_layout,
        "digest_on_disk",
        lambda path: (b"\x00" * 32, 1),  # noqa: ARG005
    )

    with pytest.raises(CorruptArtefactError, match="were streamed"):
        store.publish(a_provenance(), [BODY])

    assert store.scan(SOURCE) == ()
    assert store.sweep(SOURCE) == ()


@pytest.mark.parametrize(
    "raw_path",
    [
        "../../etc/passwd",
        f"{SOURCE}/../{SOURCE}",
        f"{SOURCE}/20260920T101143Z-3f9a1c7d4b2e/../..",
        "Reference_Archive/20260920T101143Z-3f9a1c7d4b2e",
        f"{SOURCE}/whatever",
        SOURCE,
    ],
)
def test_a_path_this_store_could_not_have_written_is_refused(
    store: RawStore, raw_path: str
) -> None:
    """``raw_path`` arrives from the database, which is not a reason to trust it."""
    with pytest.raises(RawStoreError):
        store.read(raw_path)


def test_a_legal_path_with_nothing_under_it_says_so(store: RawStore) -> None:
    with pytest.raises(MissingArtefactError):
        store.read(f"{SOURCE}/20260920T101143Z-3f9a1c7d4b2e")


def test_the_manifest_is_byte_identical_for_the_same_artefact(
    store: RawStore, tmp_path: Path
) -> None:
    """Determinism, so a republication can be compared rather than trusted."""
    published = store.publish(a_provenance(), [BODY])
    written = (store.root / published.raw_path / MANIFEST_NAME).read_bytes()

    elsewhere = RawStore(tmp_path / "second")
    twin = elsewhere.publish(a_provenance(), [BODY])

    assert written == (elsewhere.root / twin.raw_path / MANIFEST_NAME).read_bytes()
    assert written.endswith(b"\n")


def test_a_manifest_round_trips_through_its_stored_form(store: RawStore) -> None:
    published = store.publish(
        a_provenance(
            valid_from=datetime(2026, 8, 1, tzinfo=UTC),
            valid_to=datetime(2026, 9, 1, tzinfo=UTC),
            spatial_extent={"west": -8.6, "east": -5.9, "south": 51.4, "north": 53.4},
        ),
        [BODY],
    )

    stored = store.read(published.raw_path)

    assert stored.manifest == parse_manifest(manifest_bytes(stored.manifest))
    assert stored.manifest.provenance.spatial_extent == {
        "west": -8.6,
        "east": -5.9,
        "south": 51.4,
        "north": 53.4,
    }


def test_a_manifest_from_a_newer_writer_is_refused(store: RawStore) -> None:
    """It describes bytes this version does not understand. Guessing writes a row."""
    published = store.publish(a_provenance(), [BODY])
    path = store.root / published.raw_path / MANIFEST_NAME
    path.chmod(0o600)
    path.write_bytes(
        path.read_bytes().replace(
            f'"format":{MANIFEST_FORMAT}'.encode(),
            f'"format":{MANIFEST_FORMAT + 1}'.encode(),
        )
    )

    with pytest.raises(MalformedManifestError, match="unknown manifest format"):
        store.read(published.raw_path)


def test_a_manifest_carrying_an_unknown_field_is_refused(store: RawStore) -> None:
    """A field we drop is a record we only half describe."""
    published = store.publish(a_provenance(), [BODY])
    path = store.root / published.raw_path / MANIFEST_NAME
    path.chmod(0o600)
    path.write_bytes(path.read_bytes().replace(b'{"', b'{"tile_zoom":9,"', 1))

    with pytest.raises(MalformedManifestError, match="tile_zoom"):
        store.read(published.raw_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_id", "Reference Archive"),
        ("source_id", "../etc"),
        ("source_id", "a"),
        ("original_identifier", "   "),
        ("source_version", ""),
        ("media_type", ""),
        ("payload_kind", "raster"),
        ("retrieved_at", datetime(2026, 9, 20, 10, 11, 43)),  # noqa: DTZ001
    ],
)
def test_an_incomplete_provenance_cannot_be_built(field: str, value: object) -> None:
    """The refusal happens before a socket opens, not after bytes are on disk."""
    with pytest.raises(IncompleteProvenanceError):
        a_provenance(**{field: value})


def test_an_interval_with_no_start_is_refused() -> None:
    with pytest.raises(IncompleteProvenanceError, match="no start"):
        a_provenance(valid_to=datetime(2026, 9, 1, tzinfo=UTC))


def test_a_backwards_interval_is_refused() -> None:
    with pytest.raises(IncompleteProvenanceError, match="precedes"):
        a_provenance(
            valid_from=datetime(2026, 9, 1, tzinfo=UTC),
            valid_to=datetime(2026, 8, 1, tzinfo=UTC),
        )
