"""``meridian report`` — build an evaluation report, and regenerate one to check it.

Two verbs:

* ``build --snapshot DIR --config FILE --seed N [--output DIR]`` reads a raw
  snapshot and one configuration, derives every seed from ``N``, and
  publishes a sealed run: ``report.md``, one results file per section, the
  configuration as given, and a manifest. Without ``--output`` the run goes
  under ``<datasets root>/reports/<hash prefix>``. Run twice, it names the same
  directory and writes nothing the second time;
* ``verify <run> [--snapshot DIR]`` finds the run's snapshot by its hash,
  builds the run again from the configuration and seed it recorded, and exits
  0 only if the hash is the same. It prints which files differ, and how this
  machine differs from the one that made the run.

Neither opens a database or a socket: a report is computed from a snapshot,
never from the live tables (D-143) and never from a service (rule 8).

Reference: docs/DECISIONS.md D-234 to D-236.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from meridian.cli_snapshot import (
    DATASETS_ROOT_ENV,
    DEFAULT_DATASETS_ROOT,
    EXIT_CORRUPT,
    datasets_root,
)
from meridian.datasets.manifest import content_sha256
from meridian.datasets.publish import DamagedSnapshotError, read_directory
from meridian.datasets.seeds import MASTER_SEED_MAX
from meridian.datasets.snapshot_rows import MalformedSnapshotError
from meridian.reports.build import (
    REPORT_FILE,
    REPORTS,
    Run,
    RunExistsError,
    build_run,
    publish_run,
    with_environment,
)
from meridian.reports.config import ReportConfigError, load_report_config
from meridian.reports.environment import run_environment
from meridian.reports.verify import (
    NotARunError,
    SnapshotNotFoundError,
    locate_snapshot,
    verify_run,
)

__all__ = ["add_report_parser", "run_report"]

EXIT_FAILED = 1
"""Matches ``meridian.cli.EXIT_FAILED``; also a run that did not regenerate."""


def add_report_parser(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Wire ``meridian report`` and its two actions."""
    report = subcommands.add_parser(
        "report",
        help="build an evaluation report from a snapshot, and verify one",
        description=(
            "Every number in a report is regenerable from a snapshot, a"
            " configuration and a seed (rule 8). build writes a sealed run;"
            " verify regenerates one and compares hashes."
        ),
    )
    report.add_argument(
        "--root",
        type=Path,
        default=None,
        help=(
            f"datasets root (default: ${DATASETS_ROOT_ENV}, "
            f"else {DEFAULT_DATASETS_ROOT})"
        ),
    )
    actions = report.add_subparsers(dest="action", metavar="<action>")
    build = actions.add_parser("build", help="build and publish a run")
    build.add_argument("--snapshot", type=Path, required=True, help="a raw snapshot")
    build.add_argument(
        "--config",
        type=Path,
        required=True,
        help="see analysis/configs/evaluation.toml.example",
    )
    build.add_argument("--seed", type=int, required=True, help="the master seed")
    build.add_argument(
        "--output",
        type=Path,
        default=None,
        help="where the run goes (default: <root>/reports/<hash prefix>)",
    )
    verify = actions.add_parser("verify", help="regenerate a run and compare")
    verify.add_argument("run", type=Path, help="a run directory")
    verify.add_argument(
        "--snapshot",
        type=Path,
        default=None,
        help="the raw snapshot, if it is not under the datasets root",
    )


def run_report(args: argparse.Namespace) -> int:
    """Run one ``meridian report`` action."""
    actions = {"build": _build, "verify": _verify}
    return actions[args.action](args)


def _build(args: argparse.Namespace) -> int:
    """``meridian report build``."""
    root = datasets_root(args.root)
    try:
        run = _made(args, root)
        output = args.output or root / REPORTS / content_sha256(run.manifest).hex()[:12]
        published = publish_run(run, output)
    except DamagedSnapshotError as exc:
        _refuse("build", str(exc))
        return EXIT_CORRUPT
    except (ValueError, RunExistsError, OSError) as exc:
        return _refuse("build", str(exc))
    held = "written" if published.written else "already held, identically"
    _say(f"evaluation report: {published.path} ({held})")
    _describe(run)
    _say(f"  report             {published.path / REPORT_FILE}")
    return 0


def _made(args: argparse.Namespace, root: Path) -> Run:
    """The run, with the environment it was made in and how long it took.

    Raises:
        ValueError: A seed out of range, or a configuration or snapshot
            refused — every refusal here is one.
        DamagedSnapshotError: The snapshot does not match its manifest.
    """
    if not 0 <= args.seed <= MASTER_SEED_MAX:
        message = f"--seed must be 0 to {MASTER_SEED_MAX}, not {args.seed}"
        raise ValueError(message)
    started = datetime.now(UTC)
    config = load_report_config(args.config)
    raw = read_directory(args.snapshot)
    run = build_run(raw, config, seed=args.seed, root=root, created_at=started)
    elapsed = (datetime.now(UTC) - started).total_seconds()
    environment = run_environment(args.snapshot) | {"runtime_s": {"build": elapsed}}
    return with_environment(run, environment)


def _describe(run: Run) -> None:
    """The run's identity, and the one environment fact that can undermine it."""
    manifest = run.manifest
    _say(f"  hash               {content_sha256(manifest).hex()}")
    _say(f"  snapshot           {(manifest.derived_from or b'').hex()}")
    _say(f"  configuration      {(manifest.config_sha256 or b'').hex()}")
    _say(f"  seed               {manifest.parameters['seed']}")
    code = manifest.environment.get("code")
    if isinstance(code, dict):
        edited = " (with uncommitted changes)" if code.get("dirty") else ""
        _say(f"  code               {code.get('commit') or 'unknown'}{edited}")


def _verify(args: argparse.Namespace) -> int:
    """``meridian report verify``."""
    root = datasets_root(args.root)
    try:
        run = read_directory(args.run)
        raw = locate_snapshot(run.manifest, root=root, given=args.snapshot)
        verdict = verify_run(run, raw, root=root, environment=run_environment(raw.path))
    except DamagedSnapshotError as exc:
        _refuse("verify", str(exc))
        return EXIT_CORRUPT
    except (
        NotARunError,
        SnapshotNotFoundError,
        ReportConfigError,
        MalformedSnapshotError,
        OSError,
    ) as exc:
        return _refuse("verify", str(exc))
    if verdict.matches:
        _say(f"{args.run} regenerates identically: {verdict.recorded.hex()}")
    else:
        _refuse(
            "verify",
            f"{args.run} did not regenerate: recorded {verdict.recorded.hex()[:12]},"
            f" regenerated {verdict.regenerated.hex()[:12]}; files that differ:"
            f" {', '.join(verdict.differing_files)}",
        )
    for change in verdict.environment_changes:
        _say(f"  environment        {change}")
    return 0 if verdict.matches else EXIT_FAILED


def _say(line: str) -> None:
    print(line)  # noqa: T201 — this is a CLI; stdout is the interface


def _refuse(action: str, reason: str) -> int:
    print(  # noqa: T201 — this is a CLI; stderr is the interface
        f"meridian report {action}: {reason}", file=sys.stderr
    )
    return EXIT_FAILED
