"""Publishing evidence packages for the public API, and the index it serves.

``meridian dataset publish`` puts a sealed package where the API can read it,
``<published>/evidence/<hash>/``, and rewrites ``<published>/evidence/index.json``
from every package there (D-369). The API serves the index and the files it
lists, and imports nothing from this package (D-143): it checks the index whole
and each file's digest as it serves it, the way it checks the model summary
(D-332).

**Only the public cut is published.** A package exported at full precision
holds each station's times to the millisecond and its Doppler curves, which
locate it more finely than its operator declared (D-082, D-093), and is
refused here.

**The published manifest leaves out where the package was built.** Its
environment keeps the code commit, Python and the dependency versions, which a
reader needs to compare a regeneration; it drops ``snapshot_path``, a path on
the machine that exported it. The environment is never hashed (D-235), so the
package keeps its content hash.

**The index is a function of the directories alone.** Every package is read
back through ``read_directory``, which refuses one that no longer matches its
manifest, and one that is not a public cut, or whose name is not its hash, is
refused too: the index never lists a file it has not just checked. Packages
are listed newest snapshot first.

**The index names no seed.** No public answer carries a key that does (D-088),
so the seed is read where the package states it, in its manifest, datasheet
and configuration. This export draws nothing from it (D-312).

Reference: docs/DECISIONS.md D-082, D-093, D-143, D-235, D-310, D-313, D-332,
D-369.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import replace
from pathlib import Path

from meridian.datasets.canonical import canonical_bytes
from meridian.datasets.evidence_dataset import FILES, read_evidence_package
from meridian.datasets.evidence_datasheet import LICENCE_ID, citation
from meridian.datasets.manifest import Manifest, content_sha256
from meridian.datasets.publish import (
    INCOMING,
    MANIFEST_NAME,
    PublishedDirectory,
    SnapshotDirectory,
    publish_directory,
)

__all__ = [
    "EVIDENCE_DIR",
    "INDEX_NAME",
    "INDEX_SCHEMA",
    "NotPublishableError",
    "index_bytes",
    "publish_public_package",
    "write_index",
]

EVIDENCE_DIR = "evidence"
INDEX_NAME = "index.json"
INDEX_SCHEMA = "evidence-index-1"
PUBLIC_ENVIRONMENT = ("code", "python", "dependencies")
"""What a published manifest keeps of where it was built."""

_NAME_LENGTH = 12


class NotPublishableError(ValueError):
    """A package the public API may not serve, or a published one damaged."""


def publish_public_package(
    package: SnapshotDirectory, published: Path
) -> PublishedDirectory:
    """Seal a public-cut package under ``<published>/evidence/<hash>``.

    Raises:
        NotPublishableError: The package was exported at full precision.
        DamagedSnapshotError: The place holds a different package.
    """
    held = package.manifest
    if held.parameters.get("precision") != "public":
        message = (
            f"{package.path} was exported at full precision; export it with"
            ' precision = "public" to publish it (D-369)'
        )
        raise NotPublishableError(message)
    environment = {
        name: held.environment[name]
        for name in PUBLIC_ENVIRONMENT
        if name in held.environment
    }
    manifest = replace(held, environment=environment)
    name = content_sha256(manifest).hex()[:_NAME_LENGTH]
    return publish_directory(published / EVIDENCE_DIR, name, manifest, package.files)


def write_index(published: Path) -> bytes:
    """Rewrite ``<published>/evidence/index.json`` from the packages there.

    Returns:
        The bytes written.

    Raises:
        NotPublishableError: A directory there is not a public-cut package
            named by its hash.
        DamagedSnapshotError: A package does not match its manifest.
    """
    evidence = published / EVIDENCE_DIR
    packages = [
        read_evidence_package(path)
        for path in sorted(evidence.iterdir())
        if path.is_dir() and path.name != INCOMING and not path.name.startswith(".")
    ]
    data = index_bytes(packages)
    scratch = evidence / f".{INDEX_NAME}.{os.getpid()}"
    with scratch.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    scratch.replace(evidence / INDEX_NAME)
    return data


def index_bytes(packages: list[SnapshotDirectory]) -> bytes:
    """The index of ``packages``, schema ``evidence-index-1``.

    Raises:
        NotPublishableError: One is not a public cut, or not named by its hash.
    """
    entries = sorted(
        (_entry(one) for one in packages),
        key=lambda entry: (str(entry["as_of"]), str(entry["name"])),
        reverse=True,
    )
    return canonical_bytes({"schema": INDEX_SCHEMA, "packages": entries}) + b"\n"


def _entry(package: SnapshotDirectory) -> dict[str, object]:
    manifest = package.manifest
    content = content_sha256(manifest)
    name = package.path.name
    if manifest.parameters.get("precision") != "public":
        raise NotPublishableError(f"{package.path} is not a public cut")
    if name != content.hex()[:_NAME_LENGTH]:
        message = f"{package.path} is named {name}, not by its hash {content.hex()}"
        raise NotPublishableError(message)
    manifest_file = (package.path / MANIFEST_NAME).read_bytes()
    return {
        "name": name,
        "content_sha256": content,
        "snapshot_sha256": manifest.derived_from,
        "config_sha256": manifest.config_sha256,
        "transformation": manifest.transformation_version,
        "since": manifest.since,
        "as_of": manifest.as_of,
        "created_at": manifest.created_at,
        "licence": LICENCE_ID,
        "citation": citation(content),
        "populations": _populations(manifest),
        "files": [
            _file(MANIFEST_NAME, manifest_file),
            *(_file(one.name, package.files[one.name]) for one in manifest.files),
        ],
    }


def _populations(manifest: Manifest) -> list[dict[str, object]]:
    return [
        {
            "population": population,
            "file": file_name,
            "receptions": manifest.counts[f"receptions.{population}"],
            "simulated": population == "simulated",
        }
        for population, file_name in FILES.items()
        if f"receptions.{population}" in manifest.counts
    ]


def _file(name: str, data: bytes) -> dict[str, object]:
    return {
        "name": name,
        "sha256": hashlib.sha256(data).digest(),
        "size_bytes": len(data),
    }
