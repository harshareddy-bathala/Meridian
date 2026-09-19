"""``python -m meridian_client.replay`` — one recording through the real pipeline.

Runs a single assignment offline, against a recording someone already made: the
real executor, the real capture folder, the real decoder program, the real
outcome rules. It prints the MSP 0.3 observation body that a station would have
queued, and **never submits it** — nothing here builds a transport, so there is
no path from this program to the platform (D-125).

What it is for: validating a decoder wrapper and a threshold against a pass you
already have, before a station is trusted to report from them. The body it
writes names the recording it replayed, so a body that reaches the platform
another way still says what it came from.

It works on a real station's configuration too, since the run cannot reach the
platform. The station's own state directory is left alone: the capture folder is
a temporary one unless ``--work-dir`` names another.

Reference: docs/DECISIONS.md D-122 to D-128; docs/OPERATIONS.md § Station
reception.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from meridian_client.assignment_message import (
    Assignment,
    MalformedAssignmentError,
    parse_assignment,
)
from meridian_client.credentials import load_credentials
from meridian_client.observation_message import (
    ObservationResult,
    build_observation_body,
)
from meridian_client.reception.capture_folder import CaptureFolders
from meridian_client.reception.protocols import StationClocks
from meridian_client.reception.reception_executor import ReceptionExecutor
from meridian_client.reception.synthetic_receivers import (
    FileReplayReceiver,
    RecordingSource,
)
from meridian_client.station_config import (
    ConfigError,
    StationConfig,
    load_station_config,
)
from meridian_client.station_wiring import build_setup

__all__ = ["main"]

EXIT_FAILED = 1
"""A bad argument, an unreadable file, or a decode that never finished. Matches
``meridian.cli``'s code for the same meaning."""

POLL_INTERVAL_S = 0.05
SLACK_S = 30.0
"""How long past the decoder's own timeout the runner waits before giving up."""


class _SteppingClock:
    """A wall clock the runner moves, so a replay is not paced by real time."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def build_parser() -> argparse.ArgumentParser:
    """The runner's arguments."""
    parser = argparse.ArgumentParser(
        prog="python -m meridian_client.replay",
        description="Replay one recording through a station's reception pipeline.",
    )
    parser.add_argument("--config", type=Path, required=True, help="station TOML file")
    parser.add_argument(
        "--assignment",
        type=Path,
        required=True,
        help="one MSP §4.3 assignment, as JSON",
    )
    parser.add_argument("--recording", type=Path, required=True, help="the recording")
    parser.add_argument("--sample-rate-hz", type=int, required=True)
    parser.add_argument("--sample-format", default="cf32")
    parser.add_argument(
        "--centre-freq-hz", type=int, help="defaults to the assignment's frequency"
    )
    parser.add_argument("--gain-db", type=float)
    parser.add_argument(
        "--station-id", help="defaults to the station registered in the state directory"
    )
    parser.add_argument("--work-dir", type=Path, help="where the capture folder goes")
    parser.add_argument(
        "--out", type=Path, help="where to write the body; default stdout"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Replay one pass and write the observation it would have produced.

    Returns:
        ``0`` with a body on stdout, or ``EXIT_FAILED`` with a sentence on
        stderr. A pass the rules report as ``not_attempted`` or ``aborted`` is
        still a body and still exits ``0``: that is what the station would have
        sent.
    """
    args = build_parser().parse_args(argv)
    try:
        config = load_station_config(args.config)
        assignment = _assignment(args.assignment)
        station_id = _station_id(args.station_id, config)
        source = _source(args, assignment)
    except (ConfigError, MalformedAssignmentError, ValueError) as exc:
        sys.stderr.write(f"{exc}\n")
        return EXIT_FAILED

    with tempfile.TemporaryDirectory(prefix="meridian-replay-") as scratch:
        work_dir = args.work_dir if args.work_dir is not None else Path(scratch)
        try:
            result = _replay(config, assignment, source, work_dir)
        except (OSError, ValueError, TimeoutError) as exc:
            sys.stderr.write(f"{exc}\n")
            return EXIT_FAILED
        body = build_observation_body(result, station_id)

    rendered = json.dumps(body, indent=2) + "\n"
    if args.out is not None:
        args.out.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


def _replay(
    config: StationConfig,
    assignment: Assignment,
    source: RecordingSource,
    work_dir: Path,
) -> ObservationResult:
    """Drive one assignment through the executor, and wait for its result.

    The clock is moved rather than waited on, so the capture covers the pass;
    the decoder is a real program, so the wait for it is real and bounded.
    """
    clock = _SteppingClock(assignment.start_at)
    clocks = StationClocks(wall=clock)
    setup = replace(
        build_setup(config, clocks),
        receiver=FileReplayReceiver({assignment.assignment_id: source}, clocks),
        folders=CaptureFolders(work_dir / "captures"),
    )
    # Simulated: this run cannot reach the platform, and D-125's guard is about
    # what a station reports. The body says it was a replay either way.
    executor = ReceptionExecutor(setup, clocks, simulated_station=True)
    window = executor.capture_window(assignment)
    clock.now = window.opens_at
    executor.begin(assignment)
    clock.now = window.closes_at + timedelta(seconds=1)
    executor.end(assignment)
    return _first_result(executor, config)


def _first_result(
    executor: ReceptionExecutor, config: StationConfig
) -> ObservationResult:
    """Poll until the decode finishes, for as long as its own timeout allows."""
    longest = max((one.timeout_s for one in config.decoders.values()), default=0.0)
    deadline = time.monotonic() + longest + SLACK_S
    while time.monotonic() < deadline:
        for result in executor.take_completed():
            return result
        time.sleep(POLL_INTERVAL_S)
    raise TimeoutError("the decoder did not finish; no observation was produced")


def _assignment(path: Path) -> Assignment:
    """One assignment, as the platform delivered it in a heartbeat response."""
    try:
        return parse_assignment(json.loads(path.read_text(encoding="utf-8")))
    except OSError as exc:
        raise ValueError(f"{path} could not be read: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not JSON: {exc}") from exc


def _station_id(named: str | None, config: StationConfig) -> str:
    """The station the body is reported for, from the flag or the credentials."""
    if named is not None:
        return named
    credentials = load_credentials(config.paths.credentials)
    if credentials is None:
        raise ValueError(
            "no station is registered in this configuration's state directory; "
            "pass --station-id to say which station this body is for"
        )
    return credentials.station_id


def _source(args: argparse.Namespace, assignment: Assignment) -> RecordingSource:
    """The recording being replayed, described as the receiver needs it."""
    return RecordingSource(
        path=args.recording,
        sample_rate_hz=args.sample_rate_hz,
        sample_format=args.sample_format,
        centre_freq_hz=(
            assignment.centre_freq_hz
            if args.centre_freq_hz is None
            else args.centre_freq_hz
        ),
        gain_db=args.gain_db,
    )


if __name__ == "__main__":
    sys.exit(main())
