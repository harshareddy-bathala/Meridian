"""``meridian dataset`` — export the evidence dataset, and verify one.

``export`` builds a package from a raw snapshot, a configuration and a seed and
seals it under ``<root>/evidence/``, or at ``--output``. ``verify`` reads a
package, finds the snapshot it was built from by hash, rebuilds it from its
own configuration and seed, and says whether its content hash is identical.
Neither opens a database or a socket (D-143).

Exit codes are what a script reads: **0** from ``verify`` means the package
regenerated identically, **1** that it did not or could not be checked, and
**3** that a directory is not what its manifest says.

Reference: docs/DECISIONS.md D-143, D-310 to D-315; EVALUATION.md §11.5.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from meridian.cli_snapshot import EXIT_CORRUPT, datasets_root
from meridian.datasets.evidence_config import EvidenceConfigError, load_evidence_config
from meridian.datasets.evidence_dataset import (
    NotAnEvidencePackageError,
    SnapshotNotFoundError,
    build_evidence_dataset,
    locate_snapshot,
    publish_evidence_dataset,
    read_evidence_package,
    verify_evidence_dataset,
    with_environment,
)
from meridian.datasets.manifest import Manifest, content_sha256
from meridian.datasets.publish import DamagedSnapshotError, read_directory
from meridian.datasets.row_fields import MalformedSnapshotError
from meridian.reports.environment import run_environment

__all__ = ["add_dataset_parser", "run_dataset"]

EXIT_FAILED = 1

_REFUSED = (
    EvidenceConfigError,
    NotAnEvidencePackageError,
    SnapshotNotFoundError,
    MalformedSnapshotError,
    OSError,
)


def add_dataset_parser(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Wire ``meridian dataset`` and its two actions."""
    dataset = subcommands.add_parser(
        "dataset",
        help="export the evidence dataset, or verify one regenerates",
        description=(
            "A package of Meridian's own receptions with their verdicts and "
            "diagnoses, regenerable from a raw snapshot, a configuration and a "
            "seed (D-310)."
        ),
    )
    dataset.add_argument(
        "--root",
        type=Path,
        default=None,
        help="the datasets root (default: $MERIDIAN_DATASETS_ROOT, else data/datasets)",
    )
    actions = dataset.add_subparsers(dest="action", metavar="<action>")
    export = actions.add_parser("export", help="build and seal a package")
    export.add_argument("--snapshot", type=Path, required=True)
    export.add_argument("--config", type=Path, required=True)
    export.add_argument("--seed", type=int, required=True)
    export.add_argument(
        "--output", type=Path, default=None, help="where to seal it (default: root)"
    )
    verify = actions.add_parser("verify", help="rebuild a package and compare its hash")
    verify.add_argument("package", type=Path)
    verify.add_argument(
        "--snapshot", type=Path, default=None, help="the raw snapshot, if moved"
    )


def run_dataset(args: argparse.Namespace) -> int:
    """Handle ``meridian dataset export`` and ``verify``."""
    root = datasets_root(args.root)
    try:
        if args.action == "export":
            return _export(args, root)
        return _verify(args, root)
    except DamagedSnapshotError as exc:
        print(f"meridian dataset {args.action}: {exc}", file=sys.stderr)  # noqa: T201
        return EXIT_CORRUPT
    except _REFUSED as exc:
        return _refuse(args.action, str(exc))


def _export(args: argparse.Namespace, root: Path) -> int:
    raw = read_directory(args.snapshot)
    package = build_evidence_dataset(
        raw,
        load_evidence_config(args.config),
        seed=args.seed,
        created_at=datetime.now(UTC),
    )
    package = with_environment(package, run_environment(args.snapshot))
    published = publish_evidence_dataset(package, root=root, output=args.output)
    held = "written" if published.written else "already held, identically"
    _say(f"evidence dataset: {published.path} ({held})")
    for line in _described(published.manifest):
        _say(line)
    return 0


def _verify(args: argparse.Namespace, root: Path) -> int:
    package = read_evidence_package(args.package)
    raw = locate_snapshot(package.manifest, root=root, given=args.snapshot)
    check = verify_evidence_dataset(package, raw)
    if not check.matches:
        return _refuse(
            "verify",
            f"{args.package} did not regenerate: recorded {check.recorded.hex()},"
            f" regenerated {check.regenerated.hex()}; files that differ:"
            f" {', '.join(check.differing_files) or 'none'}",
        )
    _say(f"{args.package} regenerates identically: {check.recorded.hex()}")
    return 0


def _described(manifest: Manifest) -> list[str]:
    lines = [
        f"  hash               {content_sha256(manifest).hex()}",
        f"  snapshot           {(manifest.derived_from or b'').hex()}",
        f"  configuration      {(manifest.config_sha256 or b'').hex()}",
        f"  seed               {manifest.parameters['seed']}",
    ]
    lines += [
        f"  {name:<18} {count}" for name, count in sorted(manifest.counts.items())
    ]
    return lines


def _say(line: str) -> None:
    print(line)  # noqa: T201 — this is a CLI; stdout is the interface


def _refuse(action: str, reason: str) -> int:
    print(f"meridian dataset {action}: {reason}", file=sys.stderr)  # noqa: T201
    return EXIT_FAILED
