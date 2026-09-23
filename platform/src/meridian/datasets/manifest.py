"""The manifest every snapshot directory carries, and the hash that names it.

A raw snapshot and an evaluation dataset are both directories of JSON Lines
files with a ``manifest.json`` beside them (D-144). The manifest lists each file
with its digest and row count, and says what the directory is: which schema it
was read under, what interval it covers, which archive sources it holds, and —
for an evaluation dataset — which raw snapshot and which configuration made it.

**The directory's hash is the manifest's, with ``created_at`` left out.** Every
file's digest is inside the manifest, so hashing the manifest covers every byte
in the directory, and leaving the creation time out is what lets the same
inputs, labelled twice, give the same hash — Stage 15's gate. The written file
still says when it was made, and states its own hash, so a reader can check it
without trusting the directory name.

**Pure.** No file is read or written here: bytes in, a manifest out, and back.
Where the bytes go is :mod:`meridian.datasets.publish`'s business.

Reference: docs/DECISIONS.md D-143, D-144.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from meridian.datasets.canonical import canonical_bytes, canonical_value
from meridian.datasets.manifest_parse import (
    MalformedManifestError,
    digest,
    instant,
    mapping,
    optional_digest,
    optional_text,
    rows_of,
    text,
    whole,
)

__all__ = [
    "KINDS",
    "MANIFEST_FORMAT",
    "DamagedManifestError",
    "FileEntry",
    "MalformedManifestError",
    "Manifest",
    "SourceEntry",
    "content_sha256",
    "file_entry",
    "manifest_bytes",
    "parse_manifest",
]

MANIFEST_FORMAT = 1
"""Bumped when the stored shape changes. An unknown format is refused."""

Kind = Literal["raw_snapshot", "evaluation_dataset"]
KINDS: tuple[Kind, ...] = ("raw_snapshot", "evaluation_dataset")

_FILE_NAME = re.compile(r"^[a-z][a-z0-9_]*\.jsonl$")
"""Ours, and plain: a file name is a table name, never a string from a row."""

_COUNT_NAME = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")

_SHA256_BYTES = 32

_FIELDS = frozenset(
    (
        "format",
        "kind",
        "schema_revision",
        "since",
        "as_of",
        "files",
        "counts",
        "sources",
        "derived_from",
        "transformation_version",
        "config_sha256",
        "parameters",
        "created_at",
        "content_sha256",
    )
)


class DamagedManifestError(MalformedManifestError):
    """A manifest whose stated hash is not the hash of what it says.

    Its own class because it means something different from a manifest that
    cannot be parsed: this one was written whole and has changed since.
    """


@dataclass(frozen=True, slots=True)
class FileEntry:
    """One file in a snapshot directory."""

    name: str
    sha256: bytes
    rows: int

    def __post_init__(self) -> None:
        """Refuse a name, digest or count that could not describe a file."""
        if not _FILE_NAME.match(self.name):
            message = f"{self.name!r} is not a snapshot file name"
            raise MalformedManifestError(message)
        _check_digest(self.name, self.sha256)
        if self.rows < 0:
            message = f"{self.name} claims {self.rows} rows"
            raise MalformedManifestError(message)


@dataclass(frozen=True, slots=True)
class SourceEntry:
    """An archive source a snapshot holds rows from, with the terms they came under.

    Copied from ``ingest_provenance`` at export, so a dataset carries the answer
    to "were we allowed to use this" with it rather than pointing at a database
    the reader does not have (D-134).
    """

    source_id: str
    licence: str
    terms_url: str
    attribution_entry: str
    records: int


@dataclass(frozen=True, slots=True)
class Manifest:
    """What a snapshot directory is, and every file in it."""

    kind: Kind
    schema_revision: str
    """The migration head the rows were read under."""

    since: datetime
    as_of: datetime
    """The export transaction's own time (D-143). An evaluation dataset keeps
    its raw snapshot's, since labelling reads no clock."""

    files: tuple[FileEntry, ...]
    created_at: datetime
    """When this directory was written. Recorded, and never hashed."""

    counts: Mapping[str, int] = field(default_factory=dict)
    """Named totals, such as ``passes.measured`` and ``passes.simulated`` —
    reported apart, never summed across the two populations (rule 5)."""

    sources: tuple[SourceEntry, ...] = ()
    derived_from: bytes | None = None
    """For an evaluation dataset, the raw snapshot's hash."""

    transformation_version: str | None = None
    config_sha256: bytes | None = None
    parameters: Mapping[str, object] = field(default_factory=dict)
    """The labelling configuration's values, JSON-native, so the settle margin
    and the silent-satellite window are readable beside the hash of the file
    they came from."""

    def __post_init__(self) -> None:
        """Refuse a manifest that could not describe one directory."""
        if self.kind not in KINDS:
            message = f"unknown snapshot kind {self.kind!r}"
            raise MalformedManifestError(message)
        if self.since > self.as_of:
            message = f"since {self.since} is after as_of {self.as_of}"
            raise MalformedManifestError(message)
        _unique("file", [one.name for one in self.files])
        _unique("source", [one.source_id for one in self.sources])
        for name, count in self.counts.items():
            if not _COUNT_NAME.match(name) or count < 0:
                message = f"count {name!r} = {count} is not a count"
                raise MalformedManifestError(message)
        self._check_lineage()

    def _check_lineage(self) -> None:
        """An evaluation dataset names its inputs; a raw snapshot has none."""
        lineage = (self.derived_from, self.transformation_version, self.config_sha256)
        if self.kind == "evaluation_dataset":
            if any(one is None for one in lineage):
                message = (
                    "an evaluation dataset names the raw snapshot, transformation "
                    "version and configuration it came from"
                )
                raise MalformedManifestError(message)
            _check_digest("derived_from", self.derived_from)
            _check_digest("config_sha256", self.config_sha256)
        elif any(one is not None for one in lineage) or self.parameters:
            message = "a raw snapshot is read from the database, not derived"
            raise MalformedManifestError(message)


def file_entry(name: str, data: bytes) -> FileEntry:
    """Describe one JSON Lines file from its bytes.

    Args:
        name: The file's name in the directory.
        data: Its whole contents.

    Returns:
        Its digest and row count, measured here rather than declared.

    Raises:
        MalformedManifestError: The name is not a snapshot file name, or the
            last row has no newline — a file cut short would otherwise count
            one row fewer than it holds and still look whole.
    """
    if data and not data.endswith(b"\n"):
        message = f"{name} does not end with a newline, so its last row is unfinished"
        raise MalformedManifestError(message)
    return FileEntry(
        name=name, sha256=hashlib.sha256(data).digest(), rows=data.count(b"\n")
    )


def content_sha256(manifest: Manifest) -> bytes:
    """The hash that names the directory: the manifest's, without ``created_at``.

    Args:
        manifest: The directory's manifest.

    Returns:
        Thirty-two raw bytes.
    """
    return hashlib.sha256(canonical_bytes(_hashed(manifest))).digest()


def manifest_bytes(manifest: Manifest) -> bytes:
    """The exact bytes written to ``manifest.json``.

    Args:
        manifest: The directory's manifest.

    Returns:
        Canonical JSON with ``created_at`` and the manifest's own hash added,
        and a trailing newline.
    """
    written = _hashed(manifest) | {
        "created_at": manifest.created_at,
        "content_sha256": content_sha256(manifest),
    }
    return canonical_bytes(written) + b"\n"


def parse_manifest(raw: bytes) -> Manifest:
    """Read ``manifest.json`` back, strictly, and check it against its own hash.

    Args:
        raw: The file's bytes.

    Returns:
        The manifest it describes.

    Raises:
        MalformedManifestError: Not JSON, an unknown format, a missing or
            unknown field, or a value of the wrong type.
        DamagedManifestError: It parses, but the hash it states is not the hash
            of what it says.
    """
    try:
        decoded: object = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        message = f"manifest is not readable JSON: {exc}"
        raise MalformedManifestError(message) from exc
    stored = mapping(decoded, "manifest")
    if stored.get("format") != MANIFEST_FORMAT:
        message = f"unknown manifest format {stored.get('format')!r}"
        raise MalformedManifestError(message)
    if set(stored) != _FIELDS:
        missing = sorted(_FIELDS - set(stored))
        unknown = sorted(set(stored) - _FIELDS)
        message = f"manifest fields missing {missing}, unknown {unknown}"
        raise MalformedManifestError(message)
    manifest = _from_json(stored)
    stated = digest(stored["content_sha256"], "content_sha256")
    if stated != content_sha256(manifest):
        message = (
            f"manifest states {stated.hex()[:12]} but hashes to "
            f"{content_sha256(manifest).hex()[:12]}; it changed after it was written"
        )
        raise DamagedManifestError(message)
    return manifest


def _hashed(manifest: Manifest) -> dict[str, object]:
    """Everything the hash covers, in canonical form. Files and sources sorted."""
    return {
        "format": MANIFEST_FORMAT,
        "kind": manifest.kind,
        "schema_revision": manifest.schema_revision,
        "since": manifest.since,
        "as_of": manifest.as_of,
        "files": [
            {"name": one.name, "sha256": one.sha256, "rows": one.rows}
            for one in sorted(manifest.files, key=lambda one: one.name)
        ],
        "counts": dict(manifest.counts),
        "sources": [
            {
                "source_id": one.source_id,
                "licence": one.licence,
                "terms_url": one.terms_url,
                "attribution_entry": one.attribution_entry,
                "records": one.records,
            }
            for one in sorted(manifest.sources, key=lambda one: one.source_id)
        ],
        "derived_from": manifest.derived_from,
        "transformation_version": manifest.transformation_version,
        "config_sha256": manifest.config_sha256,
        "parameters": canonical_value(manifest.parameters),
    }


def _from_json(stored: Mapping[str, object]) -> Manifest:
    """The typed manifest from its parsed JSON."""
    kind = text(stored["kind"], "kind")
    if kind not in KINDS:
        message = f"unknown snapshot kind {kind!r}"
        raise MalformedManifestError(message)
    counts = mapping(stored["counts"], "counts")
    return Manifest(
        kind="raw_snapshot" if kind == "raw_snapshot" else "evaluation_dataset",
        schema_revision=text(stored["schema_revision"], "schema_revision"),
        since=instant(stored["since"], "since"),
        as_of=instant(stored["as_of"], "as_of"),
        files=tuple(
            FileEntry(
                name=text(one["name"], "files.name"),
                sha256=digest(one["sha256"], "files.sha256"),
                rows=whole(one["rows"], "files.rows"),
            )
            for one in rows_of(stored["files"], "files", ("name", "sha256", "rows"))
        ),
        created_at=instant(stored["created_at"], "created_at"),
        counts={name: whole(value, f"counts.{name}") for name, value in counts.items()},
        sources=tuple(
            SourceEntry(
                source_id=text(one["source_id"], "sources.source_id"),
                licence=text(one["licence"], "sources.licence"),
                terms_url=text(one["terms_url"], "sources.terms_url"),
                attribution_entry=text(
                    one["attribution_entry"], "sources.attribution_entry"
                ),
                records=whole(one["records"], "sources.records"),
            )
            for one in rows_of(stored["sources"], "sources", _SOURCE_FIELDS)
        ),
        derived_from=optional_digest(stored["derived_from"], "derived_from"),
        transformation_version=optional_text(
            stored["transformation_version"], "transformation_version"
        ),
        config_sha256=optional_digest(stored["config_sha256"], "config_sha256"),
        parameters=dict(mapping(stored["parameters"], "parameters")),
    )


_SOURCE_FIELDS = ("source_id", "licence", "terms_url", "attribution_entry", "records")


def _check_digest(what: str, value: bytes | None) -> None:
    """Refuse anything that is not a sha256."""
    if value is None or len(value) != _SHA256_BYTES:
        size = "nothing" if value is None else f"{len(value)} bytes"
        message = f"{what} is {size}, not a sha256"
        raise MalformedManifestError(message)


def _unique(what: str, keys: list[str]) -> None:
    """Refuse two entries under one name; the second would hide the first."""
    seen = {one for one in keys if keys.count(one) > 1}
    if seen:
        message = f"{what} listed more than once: {sorted(seen)}"
        raise MalformedManifestError(message)
