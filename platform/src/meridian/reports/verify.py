"""Regenerating a run from what it recorded, and saying whether it came out the same.

This is Stage 22's completion gate as a command: *every number and figure can
be regenerated from a snapshot, configuration, seed, and code version.* A run
holds its configuration's bytes and its master seed, and names its raw
snapshot by hash, so :func:`verify_run` needs the run and the snapshot and
nothing else. It builds the run again and compares the two hashes.

**The snapshot is found by its hash, never by trust.** A path given with
``--snapshot`` is checked; otherwise the path recorded in the environment is
tried, then every snapshot under the datasets root whose name ends in the
hash's prefix. Whatever is found is read through ``read_directory`` and its
whole hash compared, so a different snapshot with a colliding prefix, or one
edited in place, is never used.

**A difference names its files and its environment.** When the hashes differ,
the files whose digests differ are listed, and the recorded environment is
compared with this one — commit, uncommitted changes, Python and dependency
versions — because a changed library is the first thing to rule out (D-235).
The environment is compared when the hashes match too, and shown, since the
same numbers from different code is worth knowing.

Reference: docs/DECISIONS.md D-235.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from meridian.datasets.diagnosis_runs import (
    DIAGNOSES,
    DiagnosisRun,
    read_diagnosis_run,
)
from meridian.datasets.fault_runs import FAULTS, FaultRun, read_fault_run
from meridian.datasets.manifest import Manifest, content_sha256
from meridian.datasets.publish import SnapshotDirectory, read_directory
from meridian.reports.build import (
    CONFIG_FILE,
    RunInputs,
    build_run,
    with_environment,
)
from meridian.reports.config import parse_report_config

__all__ = [
    "NotARunError",
    "SnapshotNotFoundError",
    "Verdict",
    "locate_diagnosis_runs",
    "locate_fault_runs",
    "locate_snapshot",
    "verify_run",
]

_SNAPSHOTS = "snapshots"
_UNCOMPARED = frozenset(
    ("snapshot_path", "runtime_s", "fault_run_paths", "diagnosis_run_paths")
)
"""Environment entries that describe where or how long, not what, and are
never a cause of a different number."""


class NotARunError(ValueError):
    """``verify`` was pointed at a directory that is not an evaluation report."""


class SnapshotNotFoundError(LookupError):
    """The raw snapshot a run was built from is not where it could be found."""


@dataclass(frozen=True, slots=True)
class Verdict:
    """Whether a run regenerated identically, and what differed if not."""

    recorded: bytes
    regenerated: bytes
    differing_files: tuple[str, ...]
    environment_changes: tuple[str, ...]

    @property
    def matches(self) -> bool:
        """The regenerated hash is the recorded one."""
        return self.recorded == self.regenerated


def locate_snapshot(
    run: Manifest, *, root: Path, given: Path | None
) -> SnapshotDirectory:
    """The raw snapshot ``run`` was built from, found and checked by its hash.

    Args:
        run: The run's manifest.
        root: The datasets root to search under.
        given: A path the caller named, which must be that snapshot.

    Returns:
        The snapshot, verified.

    Raises:
        NotARunError: ``run`` is not an evaluation report.
        SnapshotNotFoundError: Nowhere searched holds it, or ``given`` is a
            different snapshot.
        DamagedSnapshotError: A candidate does not match its own manifest.
    """
    _require_run(run)
    wanted = run.derived_from or b""
    for path in _candidates(run, root, given):
        found = read_directory(path)
        if content_sha256(found.manifest) == wanted:
            return found
        if given is not None:
            message = (
                f"{given} is snapshot {content_sha256(found.manifest).hex()[:12]},"
                f" but this run was built from {wanted.hex()[:12]}"
            )
            raise SnapshotNotFoundError(message)
    message = (
        f"no snapshot {wanted.hex()[:12]} under {root / _SNAPSHOTS}; name it"
        " with --snapshot"
    )
    raise SnapshotNotFoundError(message)


def locate_fault_runs(
    run: Manifest, *, root: Path, given: Sequence[Path] = ()
) -> tuple[FaultRun, ...]:
    """Every fault run ``run`` judged, each found and checked by its hash.

    Looked for, in order, among the paths given, at the path the run recorded,
    and under ``<root>/faults``; whatever is found is read whole and its hash
    compared, as a snapshot is (D-240).

    Raises:
        SnapshotNotFoundError: A recorded fault run is nowhere searched.
        DamagedSnapshotError: A candidate does not match its own manifest.
    """
    recorded = run.parameters.get("fault_runs", [])
    hints = run.environment.get("fault_run_paths", {})
    found = []
    for wanted in recorded if isinstance(recorded, list) else []:
        digest = str(wanted)
        hinted = hints.get(digest) if isinstance(hints, Mapping) else None
        candidates = [
            *given,
            *([Path(str(hinted))] if hinted else []),
            *sorted((root / FAULTS).glob(f"{digest[:12]}*")),
        ]
        match = _fault_run(candidates, digest)
        if match is None:
            message = (
                f"no fault run {digest[:12]} under {root / FAULTS};"
                " name it with --faults"
            )
            raise SnapshotNotFoundError(message)
        found.append(match)
    return tuple(found)


def _fault_run(candidates: Sequence[Path], digest: str) -> FaultRun | None:
    for path in candidates:
        if path.is_dir():
            run = read_fault_run(path)
            if content_sha256(run.directory.manifest).hex() == digest:
                return run
    return None


def locate_diagnosis_runs(
    run: Manifest, *, root: Path, given: Sequence[Path] = ()
) -> tuple[DiagnosisRun, ...]:
    """Every diagnosis run ``run`` judged, each found and checked by its hash.

    Looked for as a fault run is: among the paths given, at the recorded path,
    then under ``<root>/diagnoses`` (D-278).

    Raises:
        SnapshotNotFoundError: A recorded diagnosis run is nowhere searched.
        DamagedSnapshotError: A candidate does not match its own manifest.
    """
    recorded = run.parameters.get("diagnosis_runs", [])
    hints = run.environment.get("diagnosis_run_paths", {})
    found = []
    for wanted in recorded if isinstance(recorded, list) else []:
        digest = str(wanted)
        hinted = hints.get(digest) if isinstance(hints, Mapping) else None
        candidates = [
            *given,
            *([Path(str(hinted))] if hinted else []),
            *sorted((root / DIAGNOSES).glob(f"{digest[:12]}*")),
        ]
        match = next(
            (
                one
                for one in (
                    read_diagnosis_run(path) for path in candidates if path.is_dir()
                )
                if content_sha256(one.directory.manifest).hex() == digest
            ),
            None,
        )
        if match is None:
            message = (
                f"no diagnosis run {digest[:12]} under {root / DIAGNOSES};"
                " name it with --diagnoses"
            )
            raise SnapshotNotFoundError(message)
        found.append(match)
    return tuple(found)


def verify_run(  # noqa: PLR0913 — a run, its inputs, and where to build it
    run: SnapshotDirectory,
    raw: SnapshotDirectory,
    *,
    faults: tuple[FaultRun, ...] = (),
    diagnoses: tuple[DiagnosisRun, ...] = (),
    root: Path,
    environment: Mapping[str, object],
) -> Verdict:
    """Build ``run`` again from its recorded inputs and compare.

    Args:
        run: The run, as :func:`read_directory` verified it.
        raw: Its raw snapshot, as :func:`locate_snapshot` found it.
        faults: Its fault runs, as :func:`locate_fault_runs` found them.
        diagnoses: Its diagnosis runs, as :func:`locate_diagnosis_runs` found
            them.
        root: The datasets root the evaluation dataset is published under.
        environment: This machine's environment, to compare with the recorded.

    Returns:
        Both hashes, the files that differ and the environment that differs.

    Raises:
        NotARunError: ``run`` is not an evaluation report, or records no seed.
        ReportConfigError: Its configuration is no longer accepted.
    """
    manifest = run.manifest
    seed = _require_run(manifest)
    rebuilt = build_run(
        RunInputs(raw, parse_report_config(run.files[CONFIG_FILE]), faults, diagnoses),
        seed=seed,
        root=root,
        created_at=manifest.created_at,
    )
    before = {one.name: one.sha256 for one in manifest.files}
    after = {one.name: one.sha256 for one in rebuilt.manifest.files}
    differing = [name for name in before | after if before.get(name) != after.get(name)]
    return Verdict(
        recorded=content_sha256(manifest),
        regenerated=content_sha256(rebuilt.manifest),
        differing_files=tuple(sorted(differing)),
        # Against what this rebuild measured too (the solver's version), not
        # only the machine: the recorded block holds both.
        environment_changes=_changes(
            manifest.environment,
            with_environment(rebuilt, environment).manifest.environment,
        ),
    )


def _require_run(manifest: Manifest) -> int:
    """The run's master seed, refusing anything that is not a run."""
    seed = manifest.parameters.get("seed")
    if manifest.kind != "evaluation_report" or not isinstance(seed, int):
        message = f"a {manifest.kind.replace('_', ' ')} is not a report"
        raise NotARunError(message)
    return seed


def _candidates(run: Manifest, root: Path, given: Path | None) -> list[Path]:
    """Where to look, in order: the named path, the recorded one, the root."""
    if given is not None:
        return [given]
    recorded = run.environment.get("snapshot_path")
    hinted = [Path(recorded)] if isinstance(recorded, str) else []
    prefix = (run.derived_from or b"").hex()[:12]
    searched = sorted((root / _SNAPSHOTS).glob(f"*-{prefix}"))
    return [path for path in [*hinted, *searched] if path.is_dir()]


def _changes(
    recorded: Mapping[str, object], current: Mapping[str, object]
) -> tuple[str, ...]:
    """Each environment entry that differs, as ``name: recorded → current``."""
    before, after = _flat(recorded), _flat(current)
    return tuple(
        f"{name}: {before.get(name, 'absent')} → {after.get(name, 'absent')}"
        for name in sorted(before.keys() | after.keys())
        if name.split(".")[0] not in _UNCOMPARED and before.get(name) != after.get(name)
    )


def _flat(held: Mapping[str, object], prefix: str = "") -> dict[str, object]:
    """A nested mapping as dotted names, so two can be compared entry by entry."""
    flat: dict[str, object] = {}
    for name, value in held.items():
        if isinstance(value, Mapping):
            flat |= _flat(value, f"{prefix}{name}.")
        else:
            flat[f"{prefix}{name}"] = value
    return flat
