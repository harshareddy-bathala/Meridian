"""Retrieved artefacts, held exactly as they arrived and never edited again.

Stage 14's completion gate is that a snapshot is downloaded once and then
normalised repeatedly with no network. That makes this tree the thing the gate
rests on, and the one artefact in the system that cannot be recreated without
going back to the source (D-141).

**No string from a source is ever a path element.** ``source_id`` is ours and
pattern-checked; the directory name is our timestamp and our own digest.
``original_identifier`` lives in the manifest and in ``ingest_records`` and
nowhere else. That is ``capture_folder.py``'s lesson applied by construction
rather than by validation — there is no remote value here to sanitise.

**Publication is a directory rename**, and that is the whole immutability
story. Bytes stream into a scratch directory, hashed as they arrive and
re-hashed from disk afterwards, the manifest is written inside, and only then
is the directory moved into place. A rename onto a non-empty directory fails,
so the filesystem enforces "written once" rather than a check of ours. A crash
leaves a scratch directory with no manifest, which :meth:`RawStore.scan` cannot
see because its name matches no record, and which :meth:`RawStore.sweep`
removes.

**The unit of publication is the directory**, because half of one would be a
manifest describing bytes that are not there.

**This tree is not in the database backup.** ``deploy/tools/backup.py`` dumps
Postgres; the runbook says what to copy alongside it.

The layout and the operations on bytes are
:mod:`meridian_ingest.raw_layout`'s.

Reference: docs/DECISIONS.md D-068, D-141.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from meridian_ingest.provenance import SOURCE_ID, Provenance
from meridian_ingest.raw_layout import (
    ARTEFACT_NAME,
    DIRECTORY_NAME,
    INCOMING,
    MANIFEST_NAME,
    CorruptArtefactError,
    MissingArtefactError,
    RawStoreError,
    capture,
    digest_on_disk,
    directory_name,
    seal_files,
    sync_directory,
    write_synced,
)
from meridian_ingest.raw_manifest import RawManifest, manifest_bytes, parse_manifest

__all__ = [
    "CorruptArtefactError",
    "MissingArtefactError",
    "PublishedArtefact",
    "RawStore",
    "RawStoreError",
    "StoredArtefact",
]
"""The errors are re-exported from :mod:`meridian_ingest.raw_layout` because
they are what this module's methods raise, and a caller should not have to know
which half of the pair defined them to write an ``except``."""


@dataclass(frozen=True, slots=True)
class StoredArtefact:
    """One published record: where it is, and what it says it is."""

    raw_path: str
    """Relative to the raw root, which is what ``ingest_records`` stores."""

    manifest: RawManifest
    artefact: Path
    """The bytes. A path rather than the bytes themselves, because a normaliser
    should decide when to read an artefact it may be about to refuse."""

    def read_bytes(self) -> bytes:
        """The artefact's bytes, unverified.

        Returns:
            Everything in ``artefact.bin``.

        Note:
            Use :meth:`RawStore.verify` when the question is whether the tree is
            intact. Re-hashing every artefact on every normalisation would make
            the gate's repeat runs slow enough that someone stops running them.
        """
        return self.artefact.read_bytes()


@dataclass(frozen=True, slots=True)
class PublishedArtefact:
    """The outcome of a publication attempt."""

    raw_path: str
    manifest: RawManifest
    written: bool
    """False when this exact artefact was already stored under this name.

    Which happens on a retry after a crash between the rename and the database
    insert — the two are not one transaction, and cannot be.
    """


class RawStore:
    """The tree under one raw root.

    Args:
        root: Usually ``data/ingest/raw``. Created on first publication.
    """

    def __init__(self, root: Path) -> None:
        """Point at ``root``. Nothing is read or created yet."""
        self._root = root

    @property
    def root(self) -> Path:
        """The directory this store writes under."""
        return self._root

    def publish(
        self, provenance: Provenance, chunks: Iterable[bytes]
    ) -> PublishedArtefact:
        """Store one retrieved artefact, atomically.

        Args:
            provenance: What the adapter declares about it. Already complete —
                :class:`~meridian_ingest.provenance.Provenance` refuses to exist
                otherwise.
            chunks: The bytes as they arrive. An iterable so a large artefact
                never has to be held in memory, and so the digest is taken over
                what was actually written.

        Returns:
            Where it landed, what the manifest says, and whether this call wrote
            it.

        Raises:
            EmptyArtefactError: The chunks produced no bytes.
            CorruptArtefactError: The bytes re-read from disk do not match the
                bytes that were streamed, or this name already holds a different
                artefact.

        Note:
            The digest and the byte count in the returned manifest are measured
            here, never declared. A length a source reported is a claim about a
            download; this layer exists to hold evidence.
        """
        source_dir = self._root / provenance.source_id
        scratch = source_dir / INCOMING / uuid4().hex
        scratch.mkdir(parents=True)
        try:
            manifest = capture(scratch, provenance, chunks)
            write_synced(scratch / MANIFEST_NAME, manifest_bytes(manifest))
            seal_files(scratch)
            sync_directory(scratch)
            return self._place(scratch, manifest)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def read(self, raw_path: str) -> StoredArtefact:
        """The record at one raw path.

        Args:
            raw_path: ``<source_id>/<directory>``, as ``ingest_records`` stores
                it.

        Returns:
            The manifest and the path to the bytes.

        Raises:
            RawStoreError: The path is not one this store could have written.
            MissingArtefactError: Nothing readable is there.
            MalformedManifestError: The manifest cannot be parsed.

        Note:
            The path is checked against the two patterns rather than resolved
            and compared, so ``..`` is refused by not being a legal name at all.
            It arrives from the database, which is not a reason to trust it: a
            row written by a future loader is exactly what a check like this is
            for.
        """
        directory = self._directory_for(raw_path)
        try:
            raw = (directory / MANIFEST_NAME).read_bytes()
        except OSError as exc:
            message = f"{raw_path} holds no readable manifest: {exc}"
            raise MissingArtefactError(message) from exc
        return StoredArtefact(
            raw_path=raw_path,
            manifest=parse_manifest(raw),
            artefact=directory / ARTEFACT_NAME,
        )

    def scan(self, source_id: str) -> tuple[str, ...]:
        """Every published record for one source, in name order.

        Args:
            source_id: The source to list.

        Returns:
            Raw paths, empty when the source has never been fetched.

        Note:
            Name order is retrieval order, because the name begins with the
            retrieval instant — so a normalisation run processes a snapshot the
            same way twice, which the gate needs and a directory listing does
            not otherwise promise.
        """
        return tuple(f"{source_id}/{name}" for name in self._record_names(source_id))

    def verify(self, raw_path: str) -> None:
        """Re-hash one record's bytes and check them against its manifest.

        Args:
            raw_path: The record to check.

        Raises:
            CorruptArtefactError: The bytes no longer match the digest or the
                length recorded when they arrived.
            MissingArtefactError: Nothing readable is there.

        Note:
            Separate from :meth:`read` on purpose. Verification is an operator's
            question about the tree, answered over the whole store by a command
            that exits distinctly when it finds a mismatch; normalisation asks a
            different question and should not pay for this one.
        """
        stored = self.read(raw_path)
        try:
            digest, length = digest_on_disk(stored.artefact)
        except OSError as exc:
            message = f"{raw_path} holds no readable artefact: {exc}"
            raise MissingArtefactError(message) from exc
        if digest != stored.manifest.sha256 or length != stored.manifest.byte_count:
            message = (
                f"{raw_path} is {length} bytes hashing to {digest.hex()[:12]}, but "
                f"its manifest records {stored.manifest.byte_count} bytes hashing "
                f"to {stored.manifest.sha256.hex()[:12]}"
            )
            raise CorruptArtefactError(message)

    def sweep(self, source_id: str) -> tuple[str, ...]:
        """Remove every abandoned scratch directory for one source.

        Args:
            source_id: The source to sweep.

        Returns:
            The names removed, so a caller can log that a previous run died
            partway rather than discovering it as disk use.

        Note:
            **A start-up operation.** It removes every scratch directory it
            finds, including one a concurrent fetch is filling, so it runs
            before fetching begins and not beside it. Distinguishing the two
            would need a lock or a clock, and a scratch directory is by
            definition worthless — the bytes were never published.
        """
        incoming = self._root / source_id / INCOMING
        if not incoming.is_dir():
            return ()
        swept = []
        for scratch in sorted(incoming.iterdir()):
            shutil.rmtree(scratch, ignore_errors=True)
            swept.append(scratch.name)
        return tuple(swept)

    def _record_names(self, source_id: str) -> Iterator[str]:
        """Directory names under one source that are records, in order."""
        source_dir = self._root / source_id
        if not source_dir.is_dir():
            return
        for entry in sorted(source_dir.iterdir()):
            if entry.is_dir() and DIRECTORY_NAME.fullmatch(entry.name):
                yield entry.name

    def _directory_for(self, raw_path: str) -> Path:
        """One raw path as a directory, refusing anything this store never wrote."""
        source_id, _, name = raw_path.partition("/")
        if not SOURCE_ID.fullmatch(source_id) or not DIRECTORY_NAME.fullmatch(name):
            message = f"{raw_path!r} is not a raw store path"
            raise RawStoreError(message)
        return self._root / source_id / name

    def _place(self, scratch: Path, manifest: RawManifest) -> PublishedArtefact:
        """Move a complete scratch directory into place, or find it already there."""
        source_id = manifest.provenance.source_id
        name = directory_name(manifest)
        raw_path = f"{source_id}/{name}"
        final = self._root / source_id / name
        try:
            scratch.rename(final)
        except OSError as exc:
            return self._already_stored(final, raw_path, manifest, exc)
        final.chmod(0o555)
        sync_directory(final.parent)
        return PublishedArtefact(raw_path=raw_path, manifest=manifest, written=True)

    def _already_stored(
        self, final: Path, raw_path: str, manifest: RawManifest, exc: OSError
    ) -> PublishedArtefact:
        """A rename that failed because the name is taken — by what, exactly.

        The same artefact at the same instant is a retry, and returning the
        stored record is what lets one resume. A *different* artefact under this
        name cannot happen — the name carries the digest — so if it has, the
        tree is not what it claims and the load stops.
        """
        if not final.is_dir():
            message = f"could not publish {raw_path}: {exc}"
            raise RawStoreError(message) from exc
        stored = self.read(raw_path)
        if stored.manifest.sha256 != manifest.sha256:
            message = (
                f"{raw_path} already holds an artefact hashing to "
                f"{stored.manifest.sha256.hex()[:12]}, not "
                f"{manifest.sha256.hex()[:12]}"
            )
            raise CorruptArtefactError(message)
        return PublishedArtefact(
            raw_path=raw_path, manifest=stored.manifest, written=False
        )
