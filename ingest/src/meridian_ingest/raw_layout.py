"""Where a retrieved artefact goes, and how its bytes are written and sealed.

The names, the patterns, and the four operations on bytes that
:mod:`meridian_ingest.raw_store` composes into a publication. Split from it so
each stays a length someone reads in one sitting, on the seam the client
already uses between ``capture_folder.py`` and ``manifest.py``: this module
knows what a record is called and how it is made; that one knows what may be
done with one.

The layout::

    <root>/<source_id>/
        .incoming/<uuid4>/            the only writable place
        20260920T101143Z-3f9a1c7d4b2e/
            artefact.bin              the bytes exactly as received
            manifest.json             what they are and where they came from

Reference: docs/DECISIONS.md D-141.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from datetime import UTC
from pathlib import Path

from meridian_ingest.provenance import Provenance
from meridian_ingest.raw_manifest import RawManifest

__all__ = [
    "ARTEFACT_NAME",
    "DIRECTORY_NAME",
    "INCOMING",
    "MANIFEST_NAME",
    "CorruptArtefactError",
    "EmptyArtefactError",
    "MissingArtefactError",
    "RawStoreError",
    "capture",
    "digest_on_disk",
    "directory_name",
    "seal_files",
]

ARTEFACT_NAME = "artefact.bin"
MANIFEST_NAME = "manifest.json"
INCOMING = ".incoming"

DIRECTORY_NAME = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{12}$")
"""``<retrieved_at>-<digest prefix>``, and the reason ``.incoming`` is invisible.

A scan accepts only names matching this, so a half-written directory is not
skipped by a rule someone could forget — it is not a record, because a record
is a name of this shape.
"""

_READ_BLOCK = 1 << 20
"""A megabyte at a time when re-reading, so a large artefact is not held twice."""


class RawStoreError(Exception):
    """Something is wrong with the raw store or with what is being put in it."""


class EmptyArtefactError(RawStoreError):
    """A retrieval that produced no bytes.

    Refused rather than stored. A zero-byte artefact publishes a record saying
    something was retrieved, and every later count treats it as evidence — it
    is far more often a fetch that failed politely than a source with nothing
    to say.
    """


class CorruptArtefactError(RawStoreError):
    """Bytes on disk that do not match the digest taken over them."""


class MissingArtefactError(RawStoreError):
    """A raw path with no readable record under it."""


def capture(
    scratch: Path, provenance: Provenance, chunks: Iterable[bytes]
) -> RawManifest:
    """Write the bytes into a scratch directory and describe what was written.

    Args:
        scratch: An empty writable directory under ``.incoming``.
        provenance: What the adapter declares about the artefact.
        chunks: The bytes as they arrive.

    Returns:
        The manifest for them, with the digest and length measured here.

    Raises:
        EmptyArtefactError: The chunks produced no bytes.
        CorruptArtefactError: What was read back is not what was streamed.

    Note:
        **Two digests over one artefact, deliberately.** The streaming digest
        proves what came off the wire; the re-read digest proves what reached
        the disk. They are different claims, and a short write that returned no
        error would satisfy only the first.
    """
    path = scratch / ARTEFACT_NAME
    streamed = hashlib.sha256()
    counted = 0
    with path.open("wb") as handle:
        for chunk in chunks:
            streamed.update(chunk)
            counted += len(chunk)
            handle.write(chunk)
    if counted == 0:
        message = f"{provenance.original_identifier} produced no bytes"
        raise EmptyArtefactError(message)

    settled, length = digest_on_disk(path)
    if settled != streamed.digest() or length != counted:
        message = (
            f"{counted} bytes were streamed, hashing to {streamed.hexdigest()[:12]}, "
            f"but {length} bytes read back hashing to {settled.hex()[:12]}"
        )
        raise CorruptArtefactError(message)
    return RawManifest(provenance=provenance, sha256=settled, byte_count=counted)


def digest_on_disk(path: Path) -> tuple[bytes, int]:
    """The digest and length of a file, read back a block at a time.

    Args:
        path: The file to read.

    Returns:
        Its sha256 as thirty-two raw bytes, and its length.
    """
    digest = hashlib.sha256()
    length = 0
    with path.open("rb") as handle:
        while block := handle.read(_READ_BLOCK):
            digest.update(block)
            length += len(block)
    return digest.digest(), length


def seal_files(scratch: Path) -> None:
    """Make a scratch directory's two files read-only, before the directory moves.

    Args:
        scratch: The directory holding a complete artefact and its manifest.

    Note:
        The directory itself is sealed *after* the rename, not here: moving a
        directory into a different parent updates its ``..`` entry, which needs
        write permission on the directory being moved. Sealing it first would
        make publication fail for everyone except root — which is to say
        everywhere except the one place nobody is watching.
    """
    for name in (ARTEFACT_NAME, MANIFEST_NAME):
        (scratch / name).chmod(0o444)


def directory_name(manifest: RawManifest) -> str:
    """``<retrieved_at>-<digest prefix>``: our clock and our hash, nothing else.

    Args:
        manifest: The artefact's provenance and measured digest.

    Returns:
        The directory name, for example ``20260920T101143Z-3f9a1c7d4b2e``.

    Note:
        Seconds, not milliseconds, because the name is read by people. Two
        artefacts retrieved within one second collide only if they are also the
        same bytes, in which case they are the same artefact and the store says
        so rather than overwriting either.
    """
    stamp = manifest.provenance.retrieved_at.astimezone(UTC)
    return f"{stamp:%Y%m%dT%H%M%SZ}-{manifest.sha256.hex()[:12]}"
