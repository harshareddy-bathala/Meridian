"""The evidence dataset: a package of receptions, regenerable to its hash.

Module 17, Stage 30. **A package is a pure function of a raw snapshot, a
configuration and a seed** (rule 8): building one reads no database, no clock
beyond the ``created_at`` it is given, and no network, so building it twice
from the same three gives the same bytes, and the same content hash on any
machine (SC-10).

A package is a sealed directory of kind ``evidence_dataset``:

* ``receptions.jsonl`` — measured receptions, one per line (D-310);
* ``receptions_simulated.jsonl`` — only when the configuration names simulated
  receptions (D-104, D-311);
* ``config.toml`` — the configuration's exact bytes, which ``verify`` rebuilds
  from;
* ``datasheet.md`` and ``licence.txt`` (D-313).

Its manifest names the snapshot by hash, the transformation, the
configuration's hash, the seed and the populations. When and where it was
built, and from which code, are the manifest's environment, which is not
hashed (D-235, D-312).

Reference: docs/DECISIONS.md D-102, D-104, D-143, D-144, D-235, D-310 to D-314;
docs/EVALUATION.md §11.5.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from meridian.datasets.canonical import canonical_line
from meridian.datasets.evidence_config import (
    EvidenceConfigFile,
    evidence_config_sha256,
    parse_evidence_config,
)
from meridian.datasets.evidence_datasheet import LICENCE, LICENCE_ID, datasheet
from meridian.datasets.evidence_rows import reception_rows
from meridian.datasets.manifest import Manifest, content_sha256, file_entry
from meridian.datasets.publish import (
    PublishedDirectory,
    SnapshotDirectory,
    publish_directory,
    read_directory,
)
from meridian.datasets.seeds import MASTER_SEED_MAX

__all__ = [
    "CONFIG_FILE",
    "EVIDENCE",
    "FILES",
    "TRANSFORMATION_VERSION",
    "EvidenceCheck",
    "EvidencePackage",
    "NotAnEvidencePackageError",
    "SnapshotNotFoundError",
    "build_evidence_dataset",
    "locate_snapshot",
    "publish_evidence_dataset",
    "read_evidence_package",
    "verify_evidence_dataset",
    "with_environment",
]

TRANSFORMATION_VERSION = "evidence-1"
EVIDENCE = "evidence"
SNAPSHOTS = "snapshots"
CONFIG_FILE = "config.toml"
FILES = {"measured": "receptions.jsonl", "simulated": "receptions_simulated.jsonl"}
"""Each population's file. A population not asked for has no file at all."""


class NotAnEvidencePackageError(ValueError):
    """A directory, or an input, that is not what a package needs."""


class SnapshotNotFoundError(LookupError):
    """The raw snapshot a package was built from is not where it was looked for."""


@dataclass(frozen=True, slots=True)
class EvidencePackage:
    """A package built in memory, before it is sealed."""

    manifest: Manifest
    files: Mapping[str, bytes]


def build_evidence_dataset(
    raw: SnapshotDirectory,
    config: EvidenceConfigFile,
    *,
    seed: int,
    created_at: datetime,
) -> EvidencePackage:
    """Build a package from a raw snapshot, a configuration and a seed.

    Raises:
        NotAnEvidencePackageError: ``raw`` is not a raw snapshot, or ``seed``
            is out of range.
        MalformedSnapshotError: A row the package needs is missing.
    """
    if raw.manifest.kind != "raw_snapshot":
        message = f"a package is built from a raw snapshot, not a {raw.manifest.kind}"
        raise NotAnEvidencePackageError(message)
    if not 0 <= seed <= MASTER_SEED_MAX:
        message = f"the seed must be 0..{MASTER_SEED_MAX}, not {seed}"
        raise NotAnEvidencePackageError(message)
    populations = config.config.populations
    rows = reception_rows(raw, populations)
    counts = {name: len(rows[name]) for name in populations}
    snapshot = content_sha256(raw.manifest)
    files: dict[str, bytes] = {
        FILES[name]: b"".join(canonical_line(row) for row in rows[name])
        for name in populations
    }
    files[CONFIG_FILE] = config.text
    files["datasheet.md"] = datasheet(
        snapshot_sha256=snapshot,
        since=raw.manifest.since,
        as_of=raw.manifest.as_of,
        schema_revision=raw.manifest.schema_revision,
        transformation_version=TRANSFORMATION_VERSION,
        seed=seed,
        counts=counts,
    )
    files["licence.txt"] = LICENCE
    manifest = Manifest(
        kind="evidence_dataset",
        schema_revision=raw.manifest.schema_revision,
        since=raw.manifest.since,
        as_of=raw.manifest.as_of,
        files=tuple(file_entry(name, data) for name, data in sorted(files.items())),
        created_at=created_at,
        counts={f"receptions.{name}": count for name, count in counts.items()},
        derived_from=snapshot,
        transformation_version=TRANSFORMATION_VERSION,
        config_sha256=evidence_config_sha256(config.config),
        parameters={
            "seed": seed,
            "populations": list(populations),
            "licence": LICENCE_ID,
        },
    )
    return EvidencePackage(manifest=manifest, files=files)


def with_environment(
    package: EvidencePackage, environment: Mapping[str, object]
) -> EvidencePackage:
    """The same package, recording the machine that built it. The hash is unchanged."""
    return replace(
        package, manifest=replace(package.manifest, environment=dict(environment))
    )


def publish_evidence_dataset(
    package: EvidencePackage, *, root: Path, output: Path | None = None
) -> PublishedDirectory:
    """Seal a package under ``<root>/evidence/<hash>``, or at ``output``.

    Raises:
        DamagedSnapshotError: The place already holds a different directory.
    """
    if output is not None:
        return publish_directory(
            output.parent, output.name, package.manifest, package.files
        )
    name = content_sha256(package.manifest).hex()[:12]
    return publish_directory(root / EVIDENCE, name, package.manifest, package.files)


def read_evidence_package(path: Path) -> SnapshotDirectory:
    """A sealed package, read and checked against its manifest.

    Raises:
        DamagedSnapshotError: A file is not what the manifest says.
        NotAnEvidencePackageError: The directory is some other kind.
    """
    held = read_directory(path)
    if held.manifest.kind != "evidence_dataset":
        message = f"{path} is a {held.manifest.kind}, not an evidence dataset"
        raise NotAnEvidencePackageError(message)
    return held


def locate_snapshot(
    package: Manifest, *, root: Path, given: Path | None
) -> SnapshotDirectory:
    """The raw snapshot ``package`` was built from, found and checked by its hash.

    ``given`` first, then the path the builder recorded, then every snapshot
    under ``root`` whose name ends with the hash's prefix.

    Raises:
        SnapshotNotFoundError: Nowhere searched holds it, or ``given`` is a
            different snapshot.
        DamagedSnapshotError: A candidate does not match its own manifest.
    """
    wanted = package.derived_from or b""
    for path in _candidates(package, root, given):
        found = read_directory(path)
        if content_sha256(found.manifest) == wanted:
            return found
        if given is not None:
            message = (
                f"{given} is snapshot {content_sha256(found.manifest).hex()[:12]},"
                f" but this package was built from {wanted.hex()[:12]}"
            )
            raise SnapshotNotFoundError(message)
    message = (
        f"no snapshot {wanted.hex()[:12]} under {root / SNAPSHOTS}; name it"
        " with --snapshot"
    )
    raise SnapshotNotFoundError(message)


@dataclass(frozen=True, slots=True)
class EvidenceCheck:
    """What regenerating a package came to."""

    recorded: bytes
    regenerated: bytes
    differing_files: tuple[str, ...]

    @property
    def matches(self) -> bool:
        """Whether the package regenerated to its own content hash."""
        return self.recorded == self.regenerated


def verify_evidence_dataset(
    package: SnapshotDirectory, raw: SnapshotDirectory
) -> EvidenceCheck:
    """Rebuild ``package`` from ``raw`` and its own configuration and seed.

    Raises:
        EvidenceConfigError: The stored configuration is refused.
        NotAnEvidencePackageError: The stored seed is not one.
    """
    seed = package.manifest.parameters.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise NotAnEvidencePackageError(f"the package's seed is {seed!r}")
    rebuilt = build_evidence_dataset(
        raw,
        parse_evidence_config(package.files[CONFIG_FILE]),
        seed=seed,
        created_at=package.manifest.created_at,
    )
    names = set(package.files) | set(rebuilt.files)
    return EvidenceCheck(
        recorded=content_sha256(package.manifest),
        regenerated=content_sha256(rebuilt.manifest),
        differing_files=tuple(
            sorted(
                name
                for name in names
                if package.files.get(name) != rebuilt.files.get(name)
            )
        ),
    )


def _candidates(package: Manifest, root: Path, given: Path | None) -> list[Path]:
    if given is not None:
        return [given]
    found: list[Path] = []
    hint = package.environment.get("snapshot_path")
    if isinstance(hint, str) and Path(hint).is_dir():
        found.append(Path(hint))
    prefix = (package.derived_from or b"").hex()[:12]
    found += sorted((root / SNAPSHOTS).glob(f"*-{prefix}"))
    return found
