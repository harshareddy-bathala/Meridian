"""A decoder that is a separate program, supervised rather than trusted.

Every decoder this project runs — SatDump, a GNU Radio flowgraph, a wrapper
script — is a separate process named in the station's configuration, which is
what keeps GPL code out of this repository by construction (D-001). This module
starts one, polls it on the loop's ticks, stops it if it overruns, and reads the
report it was asked to write (D-124).

Four properties are the point of it:

* **No shell.** The argv is a list, so a recording whose name contains ``;`` is
  a file name and never a command.
* **Its own session.** The decoder and anything it spawns share one process
  group, and stopping the decode stops the group — a flowgraph's helpers do not
  outlive it.
* **Logs go to files, never pipes.** A child that writes more than a pipe's
  buffer while nobody reads blocks forever, and nothing on a single-threaded
  loop would be reading.
* **Time is the injected monotonic clock.** An NTP step cannot cut a decode
  short or let one run forever.

Reference: docs/DECISIONS.md D-001, D-120, D-123, D-124.
"""

from __future__ import annotations

import contextlib
import logging
import math
import os
import shutil
import signal
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from string import Formatter

from meridian_client.reception.decode_report import (
    DecodeFailure,
    DecodeReport,
    DecodeReportError,
    read_decode_report,
)
from meridian_client.reception.protocols import DecodeJob, DecodeRun, StationClocks

__all__ = [
    "PLACEHOLDERS",
    "DecodePaths",
    "DecoderCommand",
    "SubprocessDecodeRun",
    "SubprocessDecoder",
    "decode_paths",
]

_log = logging.getLogger(__name__)

PLACEHOLDERS = frozenset(
    {
        "recording",
        "output_dir",
        "report_path",
        "sample_rate_hz",
        "sample_format",
        "centre_freq_hz",
        "mode",
    }
)
"""D-124's closed set. Anything else in a template is a configuration error."""

DEFAULT_NICENESS = 10
"""How much lower than the station client a decode runs. A capture always takes
precedence over a decode (D-124), and on a Pi they share four cores."""

STOP_GRACE_S = 5.0
"""How long a decoder gets to exit after being asked, before it is killed.

The one wait this module makes on the loop, bounded as D-120 requires."""


def _template_fields(argument: str) -> list[str]:
    """The placeholders in one argument, refusing any outside the closed set."""
    try:
        parsed = list(Formatter().parse(argument))
    except ValueError as exc:
        raise ValueError(
            f"decoder argument {argument!r} is not a template: {exc}"
        ) from exc
    fields = []
    for _literal, name, spec, conversion in parsed:
        if name is None:
            continue
        if name not in PLACEHOLDERS or spec or conversion:
            raise ValueError(
                f"decoder argument {argument!r} uses {{{name}}}; the placeholders are "
                f"{sorted(PLACEHOLDERS)}, with no format spec or conversion"
            )
        fields.append(name)
    return fields


@dataclass(frozen=True, slots=True)
class DecoderCommand:
    """One mode's decoder: an argv template and how long it may run.

    Validated on construction, which is when the station's configuration is
    loaded, so a typo is refused at start-up rather than discovered at the end of
    the first pass (D-124).
    """

    argv: tuple[str, ...]
    """The program, then its arguments. ``{{`` and ``}}`` are literal braces."""

    timeout_s: float

    def __post_init__(self) -> None:
        """Refuse a template that could not be run as written."""
        if not self.argv:
            raise ValueError("a decoder command needs a program to run")
        if not (math.isfinite(self.timeout_s) and self.timeout_s > 0):
            raise ValueError(
                f"timeout_s must be a positive number, not {self.timeout_s}"
            )
        if _template_fields(self.argv[0]):
            raise ValueError(
                f"the program must be named literally, not {self.argv[0]!r}"
            )
        for argument in self.argv[1:]:
            _template_fields(argument)

    def render(self, values: Mapping[str, str]) -> list[str]:
        """The argv for one decode, with every placeholder filled in."""
        return [argument.format_map(values) for argument in self.argv]


@dataclass(frozen=True, slots=True)
class DecodePaths:
    """Where one decode's files go, all inside its capture folder (D-123)."""

    output_dir: Path
    report: Path
    stdout_log: Path
    stderr_log: Path


def decode_paths(folder: Path) -> DecodePaths:
    """The decode files for the capture folder at ``folder``."""
    return DecodePaths(
        output_dir=folder / "decoder_output",
        report=folder / "decode_report.json",
        stdout_log=folder / "decoder.stdout.log",
        stderr_log=folder / "decoder.stderr.log",
    )


class SubprocessDecodeRun:
    """One running decoder process, polled on each tick.

    Built by :meth:`SubprocessDecoder.start`; a caller never constructs one.
    """

    def __init__(
        self,
        process: subprocess.Popen[bytes] | None,
        job: DecodeJob,
        timeout_s: float,
        clocks: StationClocks,
        *,
        stop_grace_s: float = STOP_GRACE_S,
    ) -> None:
        """Supervise ``process``; ``None`` is a decode that never started."""
        self._process = process
        self._report = decode_paths(job.folder).report
        self._duration_s = job.recording.sample_count / job.recording.sample_rate_hz
        self._timeout_s = timeout_s
        self._clocks = clocks
        self._deadline = clocks.monotonic() + timeout_s
        self._stop_grace_s = stop_grace_s
        self._outcome: DecodeReport | DecodeFailure | None = None

    @classmethod
    def failed(
        cls, job: DecodeJob, reason: str, clocks: StationClocks
    ) -> SubprocessDecodeRun:
        """A run whose outcome is already ``reason``, for a decode that never began."""
        run = cls(None, job, 1.0, clocks)
        run._outcome = DecodeFailure(reason)
        return run

    def poll(self) -> DecodeReport | DecodeFailure | None:
        """``None`` while it runs; afterwards the outcome, the same every time."""
        if self._outcome is not None or self._process is None:
            return self._outcome
        status = self._process.poll()
        if status is None:
            if self._clocks.monotonic() < self._deadline:
                return None
            self._stop_group()
            self._outcome = DecodeFailure(
                f"the decoder timed out after {self._timeout_s:g} s"
            )
            return self._outcome
        # The leader has exited; anything it left running in its group goes too.
        self._stop_group()
        self._outcome = self._outcome_of(status)
        return self._outcome

    def cancel(self) -> None:
        """Stop the decode, if it is still running."""
        if self._outcome is None and self._process is not None:
            self._stop_group()
            self._outcome = DecodeFailure("the decode was cancelled")

    def _outcome_of(self, status: int) -> DecodeReport | DecodeFailure:
        """The report a cleanly exited decoder wrote, or why there is none."""
        if status < 0:
            return DecodeFailure(f"the decoder was killed by signal {-status}")
        if status != 0:
            return DecodeFailure(f"the decoder exited with status {status}")
        try:
            return read_decode_report(
                self._report, recording_duration_s=self._duration_s
            )
        except DecodeReportError as exc:
            return DecodeFailure(str(exc))

    def _stop_group(self) -> None:
        """Terminate the decoder's process group, then kill what is left of it.

        The group id is the leader's pid, because the decoder was started in its
        own session. Linux does not reuse that id while any member of the group
        survives, so signalling it after the leader has exited reaches the
        stragglers and nothing else.
        """
        if self._process is None:
            return
        group = self._process.pid
        _signal_group(group, signal.SIGTERM)
        with contextlib.suppress(subprocess.TimeoutExpired):
            self._process.wait(timeout=self._stop_grace_s)
        _signal_group(group, signal.SIGKILL)
        try:
            self._process.wait(timeout=self._stop_grace_s)
        except subprocess.TimeoutExpired:
            _log.error("decoder process %d did not exit after SIGKILL", group)


class SubprocessDecoder:
    """Runs the configured program for each mode as a supervised subprocess.

    Args:
        commands: The decoder for each mode, e.g. ``{"lrpt": DecoderCommand(...)}``.
            A mode with no entry is refused at ``begin`` (D-124).
        clocks: The monotonic clock the timeout is measured on.
        niceness: How much to lower the decoder's CPU priority.
        stop_grace_s: How long a decoder has to exit when asked to stop.
    """

    def __init__(
        self,
        commands: Mapping[str, DecoderCommand],
        clocks: StationClocks,
        *,
        niceness: int = DEFAULT_NICENESS,
        stop_grace_s: float = STOP_GRACE_S,
    ) -> None:
        """Hold the commands. Nothing runs until :meth:`start`."""
        self._commands = dict(commands)
        self._clocks = clocks
        self._niceness = niceness
        self._stop_grace_s = stop_grace_s

    def supports(self, mode: str) -> bool:
        """Whether a decoder is configured for ``mode``."""
        return mode in self._commands

    def start(self, job: DecodeJob) -> DecodeRun:
        """Clear any earlier output and launch the decoder for ``job``.

        Earlier output is removed first — a decode restarted after a power cut
        (D-123) must not read the report its interrupted predecessor left.
        """
        command = self._commands.get(job.mode)
        if command is None:
            return SubprocessDecodeRun.failed(
                job, f"no decoder is configured for {job.mode}", self._clocks
            )
        paths = _cleared(decode_paths(job.folder))
        argv = command.render(_placeholder_values(job, paths))
        try:
            process = _launch(argv, job.folder, paths)
        except OSError as exc:
            return SubprocessDecodeRun.failed(
                job, f"the decoder could not start: {exc}", self._clocks
            )
        _lower_priority(process.pid, self._niceness)
        return SubprocessDecodeRun(
            process,
            job,
            command.timeout_s,
            self._clocks,
            stop_grace_s=self._stop_grace_s,
        )


def _placeholder_values(job: DecodeJob, paths: DecodePaths) -> dict[str, str]:
    recording = job.recording
    return {
        "recording": str(recording.path),
        "output_dir": str(paths.output_dir),
        "report_path": str(paths.report),
        "sample_rate_hz": str(recording.sample_rate_hz),
        "sample_format": recording.sample_format,
        "centre_freq_hz": str(recording.centre_freq_hz),
        "mode": job.mode,
    }


def _cleared(paths: DecodePaths) -> DecodePaths:
    """Remove a previous decode's output and report, leaving an empty output dir."""
    shutil.rmtree(paths.output_dir, ignore_errors=True)
    paths.output_dir.mkdir(parents=True)
    paths.report.unlink(missing_ok=True)
    return paths


def _launch(
    argv: Sequence[str], folder: Path, paths: DecodePaths
) -> subprocess.Popen[bytes]:
    """Start the decoder with no shell, in its own session, logging to files.

    The parent's handles to the log files close as soon as the child has its
    own copies; nothing in this process ever reads them.
    """
    with paths.stdout_log.open("wb") as stdout, paths.stderr_log.open("wb") as stderr:
        return subprocess.Popen(
            list(argv),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            cwd=folder,
            start_new_session=True,
            close_fds=True,
        )


def _lower_priority(pid: int, niceness: int) -> None:
    """Lower the decoder's CPU priority, if the platform allows it.

    Set just after launch, so anything the decoder spawns in its first instant
    keeps the client's priority. A decoder that forks before it has read its
    arguments is not one this project runs.
    """
    try:
        os.setpriority(os.PRIO_PROCESS, pid, niceness)
    except OSError as exc:
        _log.warning("could not lower decoder %d's priority: %s", pid, exc)


def _signal_group(group: int, signum: signal.Signals) -> None:
    """Send ``signum`` to a process group that may already have gone."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(group, signum)
