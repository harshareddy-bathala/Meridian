"""``meridian.datasets.publish`` — a snapshot directory is written once, and read whole.

No database: a directory is files and a manifest. What is asserted is the
raw store's discipline applied to snapshots (D-141, D-144) — atomic, synced,
sealed, never overwritten — and that reading one back is verifying it.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from meridian.datasets import publish
from meridian.datasets.manifest import Manifest, file_entry
from meridian.datasets.publish import (
    INCOMING,
    MANIFEST_NAME,
    DamagedSnapshotError,
    publish_directory,
    read_directory,
)

FILES = {
    "passes.jsonl": b'{"id":1}\n{"id":2}\n',
    "observations.jsonl": b"",
}


def manifest(files: dict[str, bytes] = FILES, **overrides: object) -> Manifest:
    fields: dict[str, object] = {
        "kind": "raw_snapshot",
        "schema_revision": "0016",
        "since": datetime(2026, 8, 1, tzinfo=UTC),
        "as_of": datetime(2026, 9, 23, 6, 30, tzinfo=UTC),
        "files": tuple(file_entry(name, data) for name, data in sorted(files.items())),
        "created_at": datetime(2026, 9, 23, 6, 31, tzinfo=UTC),
    }
    return Manifest(**(fields | overrides))  # type: ignore[arg-type]


@pytest.fixture
def parent(tmp_path: Path) -> Iterator[Path]:
    """Where snapshots go, made writable again afterwards so it can be removed."""
    root = tmp_path / "snapshots"
    yield root
    for path in sorted(root.rglob("*"), reverse=True):
        path.chmod(0o700 if path.is_dir() else 0o600)
    if root.exists():
        root.chmod(0o700)


def writable(directory: Path) -> None:
    """Undo the seal, to damage a snapshot on purpose."""
    directory.chmod(0o700)
    for one in directory.iterdir():
        one.chmod(0o600)


# --- publishing -----------------------------------------------------------


def test_a_published_snapshot_reads_back_whole(parent: Path) -> None:
    published = publish_directory(parent, "one", manifest(), FILES)

    read = read_directory(published.path)

    assert published.written is True
    assert read.manifest == manifest()
    assert dict(read.files) == FILES


def test_a_published_snapshot_is_sealed(parent: Path) -> None:
    published = publish_directory(parent, "one", manifest(), FILES)

    assert published.path.stat().st_mode & 0o777 == 0o555
    for one in published.path.iterdir():
        assert one.stat().st_mode & 0o777 == 0o444


def test_publishing_the_same_snapshot_again_writes_nothing(parent: Path) -> None:
    """The same inputs, published twice — the gate passing, not a clash."""
    publish_directory(parent, "one", manifest(), FILES)

    again = publish_directory(
        parent, "one", manifest(created_at=datetime(2026, 9, 24, tzinfo=UTC)), FILES
    )

    assert again.written is False
    assert again.manifest.created_at == datetime(2026, 9, 23, 6, 31, tzinfo=UTC)


def test_a_different_snapshot_under_a_taken_name_is_refused(parent: Path) -> None:
    publish_directory(parent, "one", manifest(), FILES)
    other = FILES | {"passes.jsonl": b'{"id":3}\n'}

    with pytest.raises(DamagedSnapshotError, match="different snapshot"):
        publish_directory(parent, "one", manifest(other), other)


def test_files_that_disagree_with_the_manifest_are_refused_before_writing(
    parent: Path,
) -> None:
    with pytest.raises(DamagedSnapshotError, match="digest"):
        publish_directory(
            parent, "one", manifest(), FILES | {"passes.jsonl": b'{"id":9}\n'}
        )

    assert not (parent / "one").exists()


def test_publishing_leaves_no_scratch_behind(parent: Path) -> None:
    publish_directory(parent, "one", manifest(), FILES)

    assert list((parent / INCOMING).iterdir()) == []


def test_publishing_syncs_what_it_renames_into_place(
    parent: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every file, the directory that moves, and where it moves to. By inode."""
    synced: set[tuple[int, int]] = set()
    real = publish.os.fsync

    def record(descriptor: int) -> None:
        facts = publish.os.fstat(descriptor)
        synced.add((facts.st_dev, facts.st_ino))
        real(descriptor)

    monkeypatch.setattr(publish.os, "fsync", record)

    published = publish_directory(parent, "one", manifest(), FILES)

    for path in (*published.path.iterdir(), published.path, parent):
        facts = path.stat()
        assert (facts.st_dev, facts.st_ino) in synced, f"{path.name} was not synced"


# --- reading back is verifying ----------------------------------------------


def test_an_edited_file_is_found(parent: Path) -> None:
    published = publish_directory(parent, "one", manifest(), FILES)
    writable(published.path)
    (published.path / "passes.jsonl").write_bytes(b'{"id":1}\n{"id":3}\n')

    with pytest.raises(DamagedSnapshotError, match="digest"):
        read_directory(published.path)


def test_a_file_the_manifest_does_not_list_is_found(parent: Path) -> None:
    published = publish_directory(parent, "one", manifest(), FILES)
    writable(published.path)
    (published.path / "extra.jsonl").write_bytes(b"")

    with pytest.raises(DamagedSnapshotError, match=r"extra\.jsonl"):
        read_directory(published.path)


def test_a_missing_file_is_found(parent: Path) -> None:
    published = publish_directory(parent, "one", manifest(), FILES)
    writable(published.path)
    (published.path / "observations.jsonl").unlink()

    with pytest.raises(DamagedSnapshotError, match="missing"):
        read_directory(published.path)


def test_an_edited_manifest_is_found(parent: Path) -> None:
    published = publish_directory(parent, "one", manifest(), FILES)
    writable(published.path)
    written = published.path / MANIFEST_NAME
    written.write_bytes(written.read_bytes().replace(b'"0016"', b'"0017"'))

    with pytest.raises(DamagedSnapshotError, match="no readable manifest"):
        read_directory(published.path)


def test_a_directory_with_no_manifest_is_not_a_snapshot(tmp_path: Path) -> None:
    with pytest.raises(DamagedSnapshotError, match="no readable manifest"):
        read_directory(tmp_path)
