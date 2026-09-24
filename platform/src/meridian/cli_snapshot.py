"""``meridian snapshot`` — export a raw snapshot, label it, and check either.

Four verbs, and the line between the first two is Stage 15's argument (D-143):

* ``export --since …`` is the only one that opens a database. It reads every
  table inside one repeatable-read, read-only transaction, asks the registry
  about listening, and publishes a sealed raw snapshot. Its end is the
  transaction's own time, which is why there is no ``--until``.
* ``label <raw snapshot> [--config …]`` opens nothing but files. The same raw
  snapshot and the same configuration always give the same directory, so the
  gate is demonstrated by running it twice and reading the name.
* ``completeness <dataset> [--threshold …]`` prints each population's
  completeness and weight diagnostics, at the dataset's threshold or another
  (D-151, D-153). It reads the dataset and nothing else.
* ``verify <directory>`` checks a snapshot or dataset against its manifest,
  and exits :data:`EXIT_CORRUPT` when it does not match — the same code, for
  the same reason, as ``meridian-ingest verify``.

Everything goes under one datasets root: ``--root``, else
``MERIDIAN_DATASETS_ROOT``, else ``data/datasets`` — gitignored, and outside
the database backup, which says so (D-144).

Reference: docs/DECISIONS.md D-143, D-144, D-145, D-146, D-147, D-151, D-153.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from meridian.config import load_settings
from meridian.datasets.completeness_report import report_lines
from meridian.datasets.evaluation import NotARawSnapshotError, build_evaluation_dataset
from meridian.datasets.export import (
    SchemaMissingError,
    export_snapshot,
    read_snapshot,
    snapshot_transaction,
)
from meridian.datasets.label_config import LabelConfigError, load_label_config
from meridian.datasets.manifest import MalformedManifestError, Manifest, content_sha256
from meridian.datasets.publish import (
    DamagedSnapshotError,
    PublishedDirectory,
    read_directory,
)
from meridian.datasets.result_reader import NoSelectionError, read_results
from meridian.datasets.snapshot_rows import MalformedSnapshotError
from meridian.registry.psycopg_registry import PsycopgRegistry
from meridian.store.pool import DatabaseUnreachableError, connect_once

__all__ = [
    "DATASETS_ROOT_ENV",
    "DEFAULT_DATASETS_ROOT",
    "EXIT_CORRUPT",
    "add_snapshot_parser",
    "run_snapshot",
]

DATASETS_ROOT_ENV = "MERIDIAN_DATASETS_ROOT"
DEFAULT_DATASETS_ROOT = Path("data/datasets")

EXIT_FAILED = 1
"""Matches ``meridian.cli.EXIT_FAILED``."""

EXIT_CORRUPT = 3
"""A snapshot that does not match its manifest. Distinct from 1 so a script can
tell "verify could not run" from "this snapshot has changed since it was
written", as ``meridian-ingest verify`` does."""


def add_snapshot_parser(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Wire ``meridian snapshot`` and its four actions."""
    snapshot = subcommands.add_parser(
        "snapshot",
        help="export, label and verify dataset snapshots",
        description=(
            "Prediction and evaluation read an immutable snapshot, never the "
            "live tables, so every number is regenerable from a snapshot, a "
            "configuration and a seed (D-143)."
        ),
    )
    snapshot.add_argument(
        "--root",
        type=Path,
        default=None,
        help=(
            f"datasets root (default: ${DATASETS_ROOT_ENV}, "
            f"else {DEFAULT_DATASETS_ROOT})"
        ),
    )
    actions = snapshot.add_subparsers(dest="action", metavar="<action>")
    export = actions.add_parser(
        "export", help="freeze the database into a raw snapshot"
    )
    export.add_argument(
        "--since",
        required=True,
        help="ISO-8601 UTC; passes from here to now are exported",
    )
    label = actions.add_parser(
        "label", help="label a raw snapshot into an evaluation dataset"
    )
    label.add_argument("snapshot", type=Path, help="a raw snapshot directory")
    label.add_argument(
        "--config",
        type=Path,
        default=None,
        help="labelling settings; see deploy/snapshot.toml.example",
    )
    completeness = actions.add_parser(
        "completeness",
        help="print an evaluation dataset's completeness and weights",
    )
    completeness.add_argument("dataset", type=Path, help="an evaluation dataset")
    completeness.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="judge station-days at this completeness instead of the dataset's",
    )
    verify = actions.add_parser(
        "verify", help="check a snapshot or dataset against its manifest"
    )
    verify.add_argument("directory", type=Path)


def run_snapshot(args: argparse.Namespace) -> int:
    """Run one ``meridian snapshot`` action."""
    actions = {
        "export": _export,
        "label": _label,
        "completeness": _completeness,
        "verify": _verify,
    }
    return actions[args.action](args)


def _root(args: argparse.Namespace) -> Path:
    """``--root``, else the environment, else the documented default."""
    if args.root is not None:
        return Path(args.root)
    return Path(os.environ.get(DATASETS_ROOT_ENV, str(DEFAULT_DATASETS_ROOT)))


def _export(args: argparse.Namespace) -> int:
    """``meridian snapshot export``."""
    try:
        since = _since(args.since)
        published = _export_from_database(since, _root(args))
    except DamagedSnapshotError as exc:
        _refuse("export", str(exc))
        return EXIT_CORRUPT
    except (
        DatabaseUnreachableError,
        SchemaMissingError,
        ValueError,
        psycopg.Error,
        OSError,
    ) as exc:
        return _refuse("export", str(exc))
    _report("raw snapshot", published)
    return 0


def _since(text: str) -> datetime:
    """``--since``, which must say which zone it is in."""
    try:
        since = datetime.fromisoformat(text)
    except ValueError as exc:
        message = f"--since {text!r} is not an ISO-8601 time"
        raise ValueError(message) from exc
    if since.tzinfo is None:
        message = "--since needs a zone, e.g. 2026-08-01T00:00:00Z"
        raise ValueError(message)
    return since


def _export_from_database(since: datetime, root: Path) -> PublishedDirectory:
    """One connection, one snapshot transaction, one registry over both.

    The transaction ends before anything is propagated (D-158): what was read
    is all the export needs afterwards.
    """
    settings = load_settings()
    with connect_once(settings) as conn:
        with snapshot_transaction(conn):
            registry = PsycopgRegistry(
                conn,
                pepper=settings.token_hash_pepper,
                recovery_window_s=settings.registration_recovery_window_s,
                now_utc=datetime.now(UTC),
            )
            read = read_snapshot(conn, registry, since=since)
        return export_snapshot(read, root=root, created_at=datetime.now(UTC))


def _label(args: argparse.Namespace) -> int:
    """``meridian snapshot label``."""
    if not Path(args.snapshot).is_dir():
        return _refuse("label", f"{args.snapshot} is not a directory")
    try:
        config = load_label_config(args.config)
        raw = read_directory(args.snapshot)
        published = build_evaluation_dataset(
            raw, config, root=_root(args), created_at=datetime.now(UTC)
        )
    except DamagedSnapshotError as exc:
        _refuse("label", str(exc))
        return EXIT_CORRUPT
    except (
        LabelConfigError,
        NotARawSnapshotError,
        MalformedSnapshotError,
        OSError,
    ) as exc:
        return _refuse("label", str(exc))
    _report("evaluation dataset", published)
    _say(f"  indeterminate      {_indeterminate(published.manifest)}")
    return 0


def _completeness(args: argparse.Namespace) -> int:
    """``meridian snapshot completeness``."""
    if not Path(args.dataset).is_dir():
        return _refuse("completeness", f"{args.dataset} is not a directory")
    try:
        dataset = read_directory(args.dataset)
        results = read_results(dataset, threshold=args.threshold)
    except DamagedSnapshotError as exc:
        _refuse("completeness", str(exc))
        return EXIT_CORRUPT
    except (
        NoSelectionError,
        LabelConfigError,
        MalformedManifestError,
        OSError,
    ) as exc:
        return _refuse("completeness", str(exc))
    _say(f"evaluation dataset {dataset.path}")
    _say(f"  hash               {content_sha256(dataset.manifest).hex()}")
    for line in report_lines(results):
        _say(line)
    return 0


def _verify(args: argparse.Namespace) -> int:
    """``meridian snapshot verify``."""
    if not Path(args.directory).is_dir():
        return _refuse("verify", f"{args.directory} is not a directory")
    try:
        directory = read_directory(args.directory)
    except DamagedSnapshotError as exc:
        _refuse("verify", str(exc))
        return EXIT_CORRUPT
    manifest = directory.manifest
    _say(
        f"{directory.path} is intact: a {manifest.kind.replace('_', ' ')} of "
        f"{len(manifest.files)} files, hash {content_sha256(manifest).hex()}"
    )
    return 0


def _report(what: str, published: PublishedDirectory) -> None:
    """Where it landed, its hash, and every count that is not zero.

    Counts are printed by name — ``passes.measured``, ``labels.confirmed_miss
    .simulated`` — rather than summed, because the only sums that mean
    anything are within one population, and the manifest already keeps those.
    """
    manifest = published.manifest
    state = "written" if published.written else "already held, identically"
    _say(f"{what}: {published.path} ({state})")
    _say(f"  hash               {content_sha256(manifest).hex()}")
    _say(f"  since … as_of      {manifest.since} … {manifest.as_of}")
    for name, count in sorted(manifest.counts.items()):
        if count:
            _say(f"  {name:<52} {count}")


def _indeterminate(manifest: Manifest) -> str:
    """What share of confirmed-listening silences could not be judged (§5)."""
    counts = manifest.counts
    judged = [
        counts.get(f"labels.{name}.measured", 0)
        for name in (
            "confirmed_miss",
            "satellite_silent",
            "satellite_state_indeterminate",
        )
    ]
    if sum(judged) == 0:
        return "no confirmed-listening silences among measured passes"
    share = judged[2] / sum(judged)
    return f"{judged[2]} of {sum(judged)} measured confirmed silences ({share:.0%})"


def _say(line: str) -> None:
    print(line)  # noqa: T201 — this is a CLI; stdout is the interface


def _refuse(action: str, reason: str) -> int:
    print(  # noqa: T201 — this is a CLI; stderr is the interface
        f"meridian snapshot {action}: {reason}", file=sys.stderr
    )
    return EXIT_FAILED
