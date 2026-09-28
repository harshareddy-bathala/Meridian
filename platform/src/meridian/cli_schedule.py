"""``meridian schedule`` — the operator's way to run the scheduler, and to judge it.

Parses the horizon, reads the schedule configuration and loads its model, opens
one short-lived connection, runs ``meridian.scheduler.run`` against the real
propagator, and prints what was decided. The decisions themselves are the
scheduler's; everything here is argument handling, exit codes and rendering.

``meridian schedule evaluate <dataset> --config <file>`` opens no connection:
it replays a dataset's test span under every scheduler and the oracle
(``meridian.scheduler.replay``) and prints the comparison. The same dataset,
file and seed print the same bytes (D-172).

Split out of ``meridian.cli`` for the same reason ``cli_passes`` is: that module
owns the command tree, and this is a subcommand whose implementation needs more
than a handler and a print.

Reference: docs/DECISIONS.md D-065, D-066, D-167 to D-170, D-172.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from meridian.cli_passes import parse_horizon_bound
from meridian.cli_snapshot import (
    DATASETS_ROOT_ENV,
    DEFAULT_DATASETS_ROOT,
    EXIT_CORRUPT,
    datasets_root,
)
from meridian.config import load_settings
from meridian.orbit.skyfield_service import SkyfieldOrbitService
from meridian.prediction.live import LiveScorer, LiveScoringError
from meridian.prediction.replay import DamagedReplayError, ReplayError, load_replay
from meridian.scheduler.comparison_config import (
    comparison_config_sha256,
    load_comparison_config,
)
from meridian.scheduler.comparison_report import comparison_lines
from meridian.scheduler.replay import ReplayInvalidError, replay_schedules
from meridian.scheduler.run import (
    ScheduleInvalidError,
    ScheduleReport,
    ScheduleRequest,
    run_schedule,
)
from meridian.scheduler.schedule_config import (
    ScheduleConfig,
    ScheduleConfigError,
    load_schedule_config,
)
from meridian.scheduler.scoring import load_scorer
from meridian.store.pool import DatabaseUnreachableError, connect_once

__all__ = [
    "add_schedule_parser",
    "load_configured",
    "print_schedule_report",
    "run_schedule_evaluate",
    "run_scheduler",
]

EXIT_FAILED = 1
"""Matches ``meridian.cli.EXIT_FAILED``. Importing it from there would be a
cycle: ``cli`` imports this module to dispatch to it."""


def add_schedule_parser(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Wire ``meridian schedule``, a verb itself, and ``schedule evaluate``.

    ``--from`` and ``--to`` are checked by the handler rather than marked
    required, since ``evaluate`` takes a dataset instead of a horizon.
    """
    schedule = subcommands.add_parser(
        "schedule",
        help="assign passes to stations over a horizon",
        description=(
            "Values the generated passes under one of EVALUATION.md's "
            "configurations, takes the best schedule each antenna allows, and "
            "writes down why every pass was taken or skipped. Re-running one "
            "configuration over one horizon writes nothing (D-066)."
        ),
    )
    schedule.add_argument("--from", dest="start", help="ISO-8601 UTC (required)")
    schedule.add_argument("--to", dest="end", help="ISO-8601 UTC (required)")
    schedule.add_argument(
        "--config",
        type=Path,
        default=None,
        help="schedule settings; see deploy/schedule.toml.example (default: A)",
    )
    schedule.add_argument(
        "--root",
        type=Path,
        default=None,
        help=(
            f"datasets root, where the model and its history are (default: "
            f"${DATASETS_ROOT_ENV}, else {DEFAULT_DATASETS_ROOT})"
        ),
    )
    actions = schedule.add_subparsers(dest="action", metavar="[evaluate]")
    evaluate = actions.add_parser(
        "evaluate",
        help="compare every scheduler and the oracle on a dataset's test span",
        description=(
            "Replays the models' test span: greedy A and B, the optimiser"
            " under A to D, and the oracle, on the same station-days, and"
            " reports frames per station-hour and SC-1 (D-172). Opens no"
            " database."
        ),
    )
    evaluate.add_argument("dataset", type=Path, help="an evaluation dataset")
    evaluate.add_argument(
        "--config",
        type=Path,
        required=True,
        help="comparison settings; see deploy/schedule-evaluation.toml.example",
    )


def _say(line: str) -> None:
    print(line)  # noqa: T201 — this is a CLI; stdout is the interface


def _refuse(reason: str) -> int:
    print(f"meridian schedule: {reason}", file=sys.stderr)  # noqa: T201
    return EXIT_FAILED


def print_schedule_report(report: ScheduleReport) -> None:
    """Write one run's outcome to stdout, in the order an operator reads it."""
    _say(f"  configuration:       {report.model_config}")
    _say(f"  yield from:          {report.yield_source}")
    if report.history_as_of is not None:
        _say(f"  history as of:       {report.history_as_of.isoformat()}")
    _say(f"  stations considered: {report.stations_considered}")
    _say(f"  passes considered:   {report.candidates_considered}")
    _say(f"  scheduled:           {report.scheduled}")
    _say(f"  skipped:             {report.skipped}")
    _say(f"  rows written:        {report.rows_written}")
    _say(f"  already decided:     {report.already_decided}")
    if report.unchanged:
        _say(f"  skipped as before:   {report.unchanged} (not written again)")
    if report.revoked:
        _say(f"  revoked, offline:    {report.revoked}")
    if report.solver is not None:
        solver = report.solver
        bound = "none" if solver.bound is None else f"{solver.bound:.1f}"
        _say(f"  run:                 {report.run_id}")
        _say(
            f"  solver:              {solver.solver} {solver.version}, "
            f"{solver.status}, value {solver.objective:.1f} (bound {bound}), "
            f"{solver.runtime_s:.2f} s of {solver.time_limit_s:g}"
        )
        if solver.detail is not None:
            _say(f"  fell back because:   {solver.detail}")
    if report.stations_unavailable:
        _say(
            f"  offline, left undecided: {', '.join(report.stations_unavailable)}"
            f" ({report.passes_deferred} passes)"
        )
    if report.passes_without_a_usable_transmitter:
        # Normally empty — pass generation applies the same capability test. It
        # fills when the catalogue changed since, and naming the passes is what
        # separates that from a satellite that simply never rose.
        count = len(report.passes_without_a_usable_transmitter)
        print(  # noqa: T201 — this is a CLI; stderr is the interface
            f"  {count} pass(es) had no downlink this station can receive; "
            f"the catalogue may have changed since they were generated",
            file=sys.stderr,
        )


def load_configured(
    args: argparse.Namespace,
) -> tuple[ScheduleConfig, LiveScorer | None]:
    """The schedule configuration and its model, or why neither can be used.

    Raises:
        ScheduleConfigError: The file is refused, or names another
            configuration's model.
        LiveScoringError: The model, or the history it reads, cannot score.
    """
    config = load_schedule_config(args.config)
    return config, load_scorer(config, datasets_root(args.root))


def run_scheduler(args: argparse.Namespace) -> int:
    """Run ``meridian schedule``, or ``meridian schedule evaluate``.

    Args:
        args: The parsed command line: ``start``, ``end``, ``config`` (a
            ``schedule.toml``, or ``None`` for the defaults) and ``root``; or
            ``action`` ``evaluate`` and its arguments.

    Returns:
        ``0`` on a completed run, including one that wrote nothing — the
        expected result of re-running over an unchanged horizon. ``1`` when the
        horizon, the configuration or its model is refused, the database cannot
        be reached, or the schedule broke a constraint.
    """
    if getattr(args, "action", None) == "evaluate":
        return run_schedule_evaluate(args)
    return _run_horizon(args)


def _horizon(args: argparse.Namespace) -> tuple[datetime, datetime]:
    """The horizon ``--from`` and ``--to`` name.

    Raises:
        ValueError: Either is missing or is not an ISO-8601 UTC instant.
    """
    if args.start is None or args.end is None:
        message = "--from and --to are required, or `evaluate <dataset>`"
        raise ValueError(message)
    return (
        parse_horizon_bound(args.start, "--from"),
        parse_horizon_bound(args.end, "--to"),
    )


def _run_horizon(args: argparse.Namespace) -> int:
    """``meridian schedule --from … --to …``: decide a horizon and write it."""
    try:
        start, end = _horizon(args)
        config, scorer = load_configured(args)
    except (ValueError, LiveScoringError) as exc:
        # ScheduleConfigError is a ValueError, as the horizon's refusals are.
        return _refuse(str(exc))

    request = ScheduleRequest(
        start=start, end=end, now=datetime.now(UTC), config=config
    )
    try:
        conn = connect_once(load_settings())
    except DatabaseUnreachableError as exc:
        return _refuse(str(exc))

    try:
        with conn:
            report = run_schedule(conn, SkyfieldOrbitService(), request, scorer)
    except ScheduleInvalidError as exc:
        # A defect, not an operator's mistake: the run refused to write a
        # schedule that breaks its own constraints, and says which (D-166).
        return _refuse(str(exc))

    _say(
        f"Scheduled [{start.isoformat()}, {end.isoformat()}) "
        f"under configuration {config.configuration}"
    )
    print_schedule_report(report)
    return 0


def run_schedule_evaluate(args: argparse.Namespace) -> int:
    """Run ``meridian schedule evaluate``: replay, compare, print.

    Returns:
        ``0`` with the comparison printed. ``1`` when the configuration, the
        dataset or a model is refused, or a schedule broke a constraint. ``3``
        when a directory is not what its manifest says, as ``meridian
        snapshot verify`` exits.
    """
    root = datasets_root(args.root)
    try:
        config = load_comparison_config(args.config)
        replay = load_replay(
            args.dataset,
            config.model_paths(root),
            root=root,
            threshold=config.threshold,
        )
    except DamagedReplayError as exc:
        _refuse(str(exc))
        return EXIT_CORRUPT
    except (ScheduleConfigError, ReplayError) as exc:
        return _refuse(str(exc))
    try:
        results = replay_schedules(replay, config.schedule)
    except ReplayInvalidError as exc:
        return _refuse(str(exc))
    for line in comparison_lines(
        results,
        config_sha256=comparison_config_sha256(config),
        resamples=config.resamples,
    ):
        _say(line)
    return 0
