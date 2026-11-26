"""Publishing a scene: a directory named by its manifest, one index, and a size cap.

A scene is rendered into a scratch directory under ``<imagery>/.incoming/``.
Publishing it is, in this order:

1. the manifest is written beside its files, and everything is made read-only;
2. the directory is renamed to ``<imagery>/<hash12>/``, the first twelve hex
   digits of its manifest's digest, so the name is a statement about every
   byte inside (D-229's rule, applied to pictures);
3. ``index.json`` (``imagery-index-1``) is rewritten whole, by a rename;
4. only then is any directory the index no longer lists removed.

A publish interrupted before step 3 leaves the index as it was, so a reader
never meets a listed scene that is not all there. One interrupted after it
leaves a directory the next publish removes.

**A scene already shown is not shown again.** Each daily refresh fetches the
clearest scene afresh, so the raw paths it names are new even when the scene
is not. A scene of the same area, source and acquisition that would publish
the same files, markers and caption as one already listed is kept as it is,
and nothing is written. One that differs — a FIRMS day that has settled, a new
areas summary — replaces it in the index, so the gallery shows each
acquisition once.

**The cap.** When the scenes listed hold more than ``max_bytes``, the oldest
acquisitions go first; each area's latest scene from each class is kept
whatever the cap says, because an area with no picture is a worse answer than
a disk a little over its budget.

Stdlib only: publishing moves files and reads records, and decodes nothing.

Reference: docs/DECISIONS.md D-229, D-369, D-371, D-375.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from meridian_ingest.imagery.model import (
    ImageryIndex,
    ImageryManifest,
    IndexEntry,
    MalformedImageryError,
    manifest_sha256,
)
from meridian_ingest.imagery.model_checks import HASH12
from meridian_ingest.raw_manifest import canonical_bytes

__all__ = [
    "INCOMING",
    "INDEX_NAME",
    "MANIFEST_NAME",
    "PublishError",
    "Published",
    "damaged_files",
    "publish_scene",
    "read_index",
    "read_manifest",
    "retain",
    "scratch_directory",
]

INDEX_NAME = "index.json"
MANIFEST_NAME = "manifest.json"
INCOMING = ".incoming"
_LOCK = ".lock"
_STALE_SCRATCH_S = 24 * 3600
"""A scratch directory untouched this long belongs to no run still going."""


class PublishError(RuntimeError):
    """A scene that cannot be published as it stands."""


@dataclass(frozen=True, slots=True)
class Published:
    """What one publish did."""

    hash: str
    """The directory the scene is listed under: new, or the one already held."""

    written: bool
    said: str
    """What happened, as the command prints it."""

    removed: tuple[str, ...]
    """Directories the index no longer lists, and which were removed."""


def scratch_directory(root: Path) -> Path:
    """A fresh path under ``<imagery>/.incoming/`` to render a scene into.

    Not created: the renderer creates it, and refuses one that exists.
    """
    incoming = root / INCOMING
    incoming.mkdir(parents=True, exist_ok=True)
    return incoming / uuid4().hex


def publish_scene(
    root: Path,
    scratch: Path,
    manifest: ImageryManifest,
    *,
    now: datetime,
    max_bytes: int,
) -> Published:
    """Publish the scene rendered into ``scratch``, and rewrite the index.

    ``scratch`` is gone afterwards: renamed into place, or discarded because
    the scene was already held.

    Raises:
        PublishError: A directory of this name holds a different manifest,
            which a name made of the manifest's digest cannot do honestly.
        MalformedImageryError: The index or a held manifest cannot be read.
    """
    name = manifest_sha256(manifest)[:12]
    try:
        with _locked(root):
            index = read_index(root)
            shown = _already_shown(root, index, manifest)
            if shown is not None:
                kept = index.scenes
                said = f"already held as {shown}: the same picture, markers and caption"
                return Published(shown, False, said, _sweep(root, kept))
            written = _place(root, scratch, name, manifest)
            entry = _entry(root, name, manifest, now, index)
            scenes = [
                one
                for one in index.scenes
                if one.hash != name and not _same_acquisition(one, entry)
            ]
            kept = retain([*scenes, entry], max_bytes)
            _write_index(root, ImageryIndex(scenes=kept))
            said = "published" if written else "already held, identically"
            if entry not in kept:
                said = (
                    "not kept: an older acquisition than the size cap leaves room for"
                )
            return Published(name, written, said, _sweep(root, kept))
    finally:
        if scratch.exists():
            _remove(scratch)


def retain(entries: Sequence[IndexEntry], max_bytes: int) -> tuple[IndexEntry, ...]:
    """The entries kept under ``max_bytes``, newest acquisition first.

    Each area's latest scene from each class is kept whatever the cap; of the
    rest, the oldest acquisitions go first until what is left fits.
    """
    latest: dict[tuple[int, str], IndexEntry] = {}
    for one in entries:
        key = (one.area_id, one.source_class)
        if key not in latest or _age(one) > _age(latest[key]):
            latest[key] = one
    protected = set(latest.values())
    total = sum(one.size_bytes for one in entries)
    dropped: set[IndexEntry] = set()
    for one in sorted(entries, key=_age):
        if total <= max_bytes:
            break
        if one in protected:
            continue
        dropped.add(one)
        total -= one.size_bytes
    kept = [one for one in entries if one not in dropped]
    return tuple(sorted(kept, key=_listed))


def read_index(root: Path) -> ImageryIndex:
    """The index, or an empty one when nothing has been published.

    Raises:
        MalformedImageryError: The file is there and cannot be read whole. It
            is never replaced on a guess: a publish stops instead.
    """
    path = root / INDEX_NAME
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return ImageryIndex(scenes=())
    try:
        return ImageryIndex.from_json(json.loads(raw))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        message = f"{path} is not JSON: {exc}"
        raise MalformedImageryError(message) from exc


def read_manifest(root: Path, name: str) -> ImageryManifest:
    """The manifest of the scene published as ``name``.

    Raises:
        MalformedImageryError: ``name`` is not a scene's name, nothing is
            there, or its manifest breaks a rule.
    """
    if not HASH12.match(name):
        message = f"{name!r} is not a scene's name: twelve lower-case hex digits"
        raise MalformedImageryError(message)
    path = root / name / MANIFEST_NAME
    try:
        stored = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        message = f"{path} could not be read: {exc}"
        raise MalformedImageryError(message) from exc
    return ImageryManifest.from_json(stored)


def damaged_files(root: Path, name: str, manifest: ImageryManifest) -> tuple[str, ...]:
    """The files of a published scene that no longer match its manifest."""
    damaged = []
    for one in manifest.files:
        path = root / name / one.path
        try:
            data = path.read_bytes()
        except OSError:
            damaged.append(one.path)
            continue
        if (
            len(data) != one.size_bytes
            or hashlib.sha256(data).hexdigest() != one.sha256
        ):
            damaged.append(one.path)
    return tuple(damaged)


# --- the steps -------------------------------------------------------------------


@contextmanager
def _locked(root: Path) -> Iterator[None]:
    """One publish at a time, whichever process is publishing."""
    root.mkdir(parents=True, exist_ok=True)
    with (root / _LOCK).open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _already_shown(
    root: Path, index: ImageryIndex, manifest: ImageryManifest
) -> str | None:
    """A listed scene of the same acquisition that shows exactly the same."""
    for one in index.scenes:
        if not (
            one.area_id == manifest.area_id
            and one.source_id == manifest.source_id
            and one.acquired_at == manifest.acquired_at
        ):
            continue
        held = read_manifest(root, one.hash)
        if (held.files, held.caption, held.annotations) == (
            manifest.files,
            manifest.caption,
            manifest.annotations,
        ):
            return one.hash
    return None


def _place(root: Path, scratch: Path, name: str, manifest: ImageryManifest) -> bool:
    """Move the scene into ``<root>/<name>``; False when it was already there."""
    final = root / name
    written = canonical_bytes(manifest.to_json())
    if final.exists():
        held = canonical_bytes(read_manifest(root, name).to_json())
        if held != written:
            message = f"{final} holds a different manifest under the same name"
            raise PublishError(message)
        return False
    listed = {one.path for one in manifest.files}
    present = {
        str(path.relative_to(scratch)) for path in scratch.rglob("*") if path.is_file()
    }
    if present != listed:
        message = f"{scratch} holds {sorted(present ^ listed)} beside its manifest"
        raise PublishError(message)
    _write_synced(scratch / MANIFEST_NAME, written)
    _seal(scratch)
    scratch.rename(final)
    final.chmod(0o555)
    _sync_directory(root)
    return True


def _entry(
    root: Path,
    name: str,
    manifest: ImageryManifest,
    now: datetime,
    index: ImageryIndex,
) -> IndexEntry:
    for one in index.scenes:
        if one.hash == name:
            return one
    size = sum(
        path.stat().st_size for path in (root / name).rglob("*") if path.is_file()
    )
    return IndexEntry(
        hash=name,
        area_id=manifest.area_id,
        source_class=manifest.source_class,
        source_id=manifest.source_id,
        acquired_at=manifest.acquired_at,
        published_at=now,
        size_bytes=size,
    )


def _same_acquisition(one: IndexEntry, other: IndexEntry) -> bool:
    return (one.area_id, one.source_id, one.acquired_at) == (
        other.area_id,
        other.source_id,
        other.acquired_at,
    )


def _write_index(root: Path, index: ImageryIndex) -> None:
    """Replace the index whole, by a rename, so a reader sees one or the other."""
    scratch = root / f".{INDEX_NAME}.{os.getpid()}"
    _write_synced(scratch, canonical_bytes(index.to_json()))
    scratch.replace(root / INDEX_NAME)
    _sync_directory(root)


def _sweep(root: Path, kept: Sequence[IndexEntry]) -> tuple[str, ...]:
    """Remove every scene directory the index does not list, and stale scratch."""
    listed = {one.hash for one in kept}
    removed = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and HASH12.match(child.name) and child.name not in listed:
            _remove(child)
            removed.append(child.name)
    incoming = root / INCOMING
    if incoming.is_dir():
        cutoff = time.time() - _STALE_SCRATCH_S
        for child in incoming.iterdir():
            if child.stat().st_mtime < cutoff:
                _remove(child)
    return tuple(removed)


def _age(one: IndexEntry) -> tuple[datetime, datetime, str]:
    return one.acquired_at, one.published_at, one.hash


def _listed(one: IndexEntry) -> tuple[float, int, str, str]:
    """Newest acquisition first, then by area, class and name."""
    return (-one.acquired_at.timestamp(), one.area_id, one.source_class, one.hash)


def _seal(directory: Path) -> None:
    """Every file read-only, and every directory below the top one.

    The top directory is sealed after its rename: moving a directory into
    another parent rewrites its ``..``, which needs write permission on it.
    """
    for path in sorted(directory.rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)


def _remove(path: Path) -> None:
    """Remove a sealed directory: made writable again first, or rmtree fails."""
    for here, directories, _ in os.walk(path):
        os.chmod(here, 0o755)  # noqa: PTH101 — os.walk yields strings
        for one in directories:
            os.chmod(os.path.join(here, one), 0o755)  # noqa: PTH101, PTH118
    shutil.rmtree(path)


def _write_synced(path: Path, data: bytes) -> None:
    with path.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
