"""Writing a snapshot directory once, and reading one back only if it is whole.

A snapshot directory is published the way the raw store publishes an artefact
(D-141): every file is written into a scratch directory and synced, the
manifest is written last, the files are sealed read-only, and the directory is
renamed into place under its hash. A rename is atomic, so a directory either
exists whole under its name or does not exist; the syncs make that survive a
power cut; and a rename onto a non-empty directory fails, so nothing is
overwritten (D-144).

Written for this package rather than imported from ``meridian_ingest``: D-138
points that dependency one way, from ingest to the platform, and never back.

**Reading is verifying.** :func:`read_directory` checks every file against the
manifest and refuses a directory holding a file the manifest does not list.
The labeller reads a raw snapshot only through it, so an edited snapshot stops
a label run instead of quietly producing a different hash.

Reference: docs/DECISIONS.md D-138, D-141, D-144.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from meridian.datasets.manifest import (
    MalformedManifestError,
    Manifest,
    content_sha256,
    manifest_bytes,
    parse_manifest,
)

__all__ = [
    "INCOMING",
    "MANIFEST_NAME",
    "DamagedSnapshotError",
    "PublishedDirectory",
    "SnapshotDirectory",
    "publish_directory",
    "read_directory",
]

MANIFEST_NAME = "manifest.json"
INCOMING = ".incoming"
"""Scratch directories live here, under the root they will be renamed into, so
the rename never crosses a filesystem."""


class DamagedSnapshotError(RuntimeError):
    """A snapshot directory that is not what its manifest says it is."""


@dataclass(frozen=True, slots=True)
class PublishedDirectory:
    """Where a snapshot landed, and whether this call wrote it."""

    path: Path
    manifest: Manifest
    written: bool
    """False when a directory with this hash was already there — the same
    inputs, published again, which is the gate passing rather than a clash."""


@dataclass(frozen=True, slots=True)
class SnapshotDirectory:
    """A verified snapshot: its manifest and every file's bytes."""

    path: Path
    manifest: Manifest
    files: Mapping[str, bytes]


def publish_directory(
    parent: Path, name: str, manifest: Manifest, files: Mapping[str, bytes]
) -> PublishedDirectory:
    """Write a snapshot directory once, atomically, under ``parent / name``.

    Args:
        parent: The directory it goes in, created if missing.
        name: Its name, which the caller builds from the manifest's hash.
        manifest: What it holds. Every file it lists must be in ``files``.
        files: File name to bytes.

    Returns:
        Where it landed, and whether this call wrote it.

    Raises:
        DamagedSnapshotError: ``files`` and the manifest disagree, or ``name``
            already holds a directory with a different hash.
    """
    _check_matches(manifest, files)
    scratch = parent / INCOMING / uuid4().hex
    scratch.mkdir(parents=True)
    try:
        for file_name, data in sorted(files.items()):
            _write_synced(scratch / file_name, data)
        _write_synced(scratch / MANIFEST_NAME, manifest_bytes(manifest))
        for one in scratch.iterdir():
            one.chmod(0o444)
        _sync_directory(scratch)
        return _place(scratch, parent / name, manifest)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def read_directory(path: Path) -> SnapshotDirectory:
    """Read a snapshot directory back, and refuse it unless it is whole.

    Args:
        path: The directory.

    Returns:
        Its manifest and every file's bytes.

    Raises:
        DamagedSnapshotError: The manifest is missing, unreadable or edited, a
            file is missing or does not match its digest, or a file is present
            that the manifest does not list.
    """
    try:
        manifest = parse_manifest((path / MANIFEST_NAME).read_bytes())
    except (OSError, MalformedManifestError) as exc:
        message = f"{path} has no readable manifest: {exc}"
        raise DamagedSnapshotError(message) from exc
    listed = {one.name for one in manifest.files}
    present = {one.name for one in path.iterdir()} - {MANIFEST_NAME}
    if present != listed:
        message = (
            f"{path} holds {sorted(present - listed)} unlisted and is missing "
            f"{sorted(listed - present)}"
        )
        raise DamagedSnapshotError(message)
    files = {one.name: (path / one.name).read_bytes() for one in manifest.files}
    _check_matches(manifest, files)
    return SnapshotDirectory(path=path, manifest=manifest, files=files)


def _check_matches(manifest: Manifest, files: Mapping[str, bytes]) -> None:
    """Every listed file present, with its digest and its row count."""
    if set(files) != {one.name for one in manifest.files}:
        message = (
            f"the manifest lists {sorted(one.name for one in manifest.files)} "
            f"but the files are {sorted(files)}"
        )
        raise DamagedSnapshotError(message)
    for entry in manifest.files:
        data = files[entry.name]
        if hashlib.sha256(data).digest() != entry.sha256:
            message = f"{entry.name} does not match the digest in its manifest"
            raise DamagedSnapshotError(message)
        if data.count(b"\n") != entry.rows:
            message = f"{entry.name} does not hold the {entry.rows} rows listed"
            raise DamagedSnapshotError(message)


def _place(scratch: Path, final: Path, manifest: Manifest) -> PublishedDirectory:
    """Rename into place, or find the same snapshot already there."""
    try:
        scratch.rename(final)
    except OSError as exc:
        if not final.is_dir():
            message = f"could not publish {final}: {exc}"
            raise DamagedSnapshotError(message) from exc
        existing = read_directory(final).manifest
        if content_sha256(existing) != content_sha256(manifest):
            message = f"{final} already holds a different snapshot"
            raise DamagedSnapshotError(message) from exc
        return PublishedDirectory(path=final, manifest=existing, written=False)
    final.chmod(0o555)
    _sync_directory(final.parent)
    return PublishedDirectory(path=final, manifest=manifest, written=True)


def _write_synced(path: Path, data: bytes) -> None:
    """Write a file and make sure it reached the disk before anything names it."""
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _sync_directory(path: Path) -> None:
    """Make a directory's entries durable, including one just renamed into it."""
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
