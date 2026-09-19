"""The subprocess decoder, supervising a real program.

Each decode here starts ``reception_fakes/fake_decoder.py`` as a separate
process, exactly as a station starts a SatDump wrapper, so what is under test is
the launch, the timeout, the process-group stop and the log files rather than a
rehearsal of them. The timeout runs on a fake monotonic clock; waiting for a
fast program to finish uses real time, bounded.

Marked as a unit test by living in ``tests/unit``: a child process is not
infrastructure.

Reference: docs/DECISIONS.md D-001, D-120, D-123, D-124.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from meridian_client.reception import subprocess_decoder
from meridian_client.reception.decode_report import DecodeFailure, DecodeReport
from meridian_client.reception.protocols import (
    DecodeJob,
    DecodeRun,
    Recording,
    StationClocks,
)
from meridian_client.reception.subprocess_decoder import (
    DecoderCommand,
    SubprocessDecoder,
    decode_paths,
)

FAKE = str(Path(__file__).parent / "reception_fakes" / "fake_decoder.py")
PYTHON = sys.executable
REPORT = {
    "format": 1,
    "decoder": "fake",
    "frames_decoded": 3,
    "first_frame_offset_s": 1.5,
}


class FakeMonotonic:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def monotonic() -> FakeMonotonic:
    return FakeMonotonic()


def job(
    tmp_path: Path, mode: str = "lrpt", recording_name: str = "recording.u8"
) -> DecodeJob:
    folder = tmp_path / "captures" / "as_44b2"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / recording_name
    path.write_bytes(b"\x7f" * 20_000)
    at = datetime(2026, 8, 14, 9, 41, 18, tzinfo=UTC)
    recording = Recording(path, 1_000, "u8", 137_900_000, 10_000, at, at, None, False)
    return DecodeJob(recording=recording, mode=mode, folder=folder)


STARTED: list[DecodeRun] = []
"""Every run a test started, so teardown can stop any a failing test left."""


class TrackedDecoder(SubprocessDecoder):
    """The real decoder, remembering each run it starts."""

    def start(self, job: DecodeJob) -> DecodeRun:
        run = super().start(job)
        STARTED.append(run)
        return run


@pytest.fixture(autouse=True)
def stop_every_decode() -> Iterator[None]:
    """A test that fails before its decode finishes must not leave it running.

    A hanging fake sleeps for ten minutes and starts a child that does too;
    without this, every failed run of this file leaves both behind.
    """
    yield
    for run in STARTED:
        run.cancel()
    STARTED.clear()


def decoder(
    monotonic: FakeMonotonic, *argv: str, timeout_s: float = 60.0, **options: Any
) -> SubprocessDecoder:
    command = DecoderCommand((PYTHON, FAKE, *argv), timeout_s=timeout_s)
    return TrackedDecoder(
        {"lrpt": command},
        StationClocks(monotonic=monotonic),
        stop_grace_s=0.3,
        **options,
    )


def fixture_report(tmp_path: Path, report: object = REPORT) -> str:
    path = tmp_path / "fixture_report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return str(path)


def finish(run: DecodeRun, within_s: float = 20.0) -> DecodeReport | DecodeFailure:
    """Poll until the decode has an outcome, in real time, bounded."""
    deadline = time.monotonic() + within_s
    while time.monotonic() < deadline:
        outcome = run.poll()
        if outcome is not None:
            return outcome
        time.sleep(0.02)
    raise AssertionError("the decode did not finish")


def wait_for_file(path: Path, within_s: float = 20.0) -> None:
    deadline = time.monotonic() + within_s
    while not path.exists():
        if time.monotonic() > deadline:
            raise AssertionError(f"{path} never appeared")
        time.sleep(0.02)


def gone(pid: int, within_s: float = 10.0) -> bool:
    """Whether ``pid`` has exited — absent, or a zombie waiting for init."""
    deadline = time.monotonic() + within_s
    while time.monotonic() < deadline:
        try:
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except (FileNotFoundError, ProcessLookupError):
            return True
        if state == "Z":
            return True
        time.sleep(0.05)
    return False


# --- the command template -----------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        ((), "needs a program"),
        (("decode", "{recording_path}"), "uses {recording_path}"),
        (("decode", "{}"), "uses {}"),
        (("decode", "{recording!r}"), "no format spec or conversion"),
        (("decode", "{sample_rate_hz:08d}"), "no format spec or conversion"),
        (("decode", "{recording.name}"), "uses {recording.name}"),
        (("{mode}-decoder", "{recording}"), "named literally"),
        (("decode", "{unclosed"), "not a template"),
    ],
)
def test_a_template_that_could_not_run_as_written_is_refused(
    argv: tuple[str, ...], message: str
) -> None:
    """Refused when configuration loads, not at the end of the first pass (D-124)."""
    with pytest.raises(ValueError, match=message):
        DecoderCommand(argv, timeout_s=60.0)


@pytest.mark.parametrize("timeout_s", [0.0, -1.0, float("inf"), float("nan")])
def test_a_decoder_needs_a_finite_positive_timeout(timeout_s: float) -> None:
    with pytest.raises(ValueError, match="timeout_s"):
        DecoderCommand(("decode",), timeout_s=timeout_s)


def test_every_placeholder_renders_and_doubled_braces_are_literal() -> None:
    names = sorted(subprocess_decoder.PLACEHOLDERS)
    command = DecoderCommand(
        ("decode", *[f"{{{name}}}" for name in names], "{{x}}"), 60.0
    )

    rendered = command.render({name: name.upper() for name in names})

    assert rendered == ["decode", *[name.upper() for name in names], "{x}"]


# --- running it ---------------------------------------------------------------


def test_a_decoder_is_configured_per_mode(monotonic: FakeMonotonic) -> None:
    configured = decoder(monotonic, "silent")

    assert configured.supports("lrpt")
    assert not configured.supports("apt")


def test_a_mode_with_no_decoder_fails_without_starting_anything(
    tmp_path: Path, monotonic: FakeMonotonic
) -> None:
    outcome = decoder(monotonic, "silent").start(job(tmp_path, mode="apt")).poll()

    assert outcome == DecodeFailure("no decoder is configured for apt")


def test_a_decoder_that_writes_its_report_succeeds(
    tmp_path: Path, monotonic: FakeMonotonic
) -> None:
    run = decoder(monotonic, "report", "{report_path}", fixture_report(tmp_path)).start(
        job(tmp_path)
    )

    outcome = finish(run)

    assert isinstance(outcome, DecodeReport)
    assert outcome.frames_decoded == 3
    assert run.poll() is outcome


def test_logs_land_in_the_capture_folder_and_a_loud_decoder_cannot_deadlock(
    tmp_path: Path, monotonic: FakeMonotonic
) -> None:
    """Files, never pipes: two megabytes on each stream with nobody reading."""
    the_job = job(tmp_path)
    run = decoder(monotonic, "chatty", "{report_path}", fixture_report(tmp_path)).start(
        the_job
    )

    outcome = finish(run)

    paths = decode_paths(the_job.folder)
    assert isinstance(outcome, DecodeReport)
    assert paths.stdout_log.stat().st_size == 2 * 1024 * 1024
    assert paths.stderr_log.stat().st_size == 2 * 1024 * 1024


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (("exit", "3"), "exited with status 3"),
        (("silent",), "wrote no report"),
    ],
)
def test_a_decoder_that_fails_or_writes_nothing_is_a_failed_decode(
    tmp_path: Path, monotonic: FakeMonotonic, argv: tuple[str, ...], message: str
) -> None:
    outcome = finish(decoder(monotonic, *argv).start(job(tmp_path)))

    assert isinstance(outcome, DecodeFailure)
    assert message in outcome.reason


@pytest.mark.parametrize(
    ("report", "message"),
    [
        ({"format": 1, "decoder": "fake", "frame_count": 3}, "unknown keys"),
        ({"format": 1, "decoder": "fake", "first_frame_offset_s": 11.0}, "outside"),
    ],
)
def test_a_report_that_breaks_the_contract_is_a_failed_decode(
    tmp_path: Path, monotonic: FakeMonotonic, report: object, message: str
) -> None:
    """The recording is ten seconds, so an offset of eleven is outside it (D-122)."""
    run = decoder(
        monotonic, "report", "{report_path}", fixture_report(tmp_path, report)
    )

    outcome = finish(run.start(job(tmp_path)))

    assert isinstance(outcome, DecodeFailure)
    assert message in outcome.reason


def test_a_program_that_does_not_exist_is_a_failed_decode(
    tmp_path: Path, monotonic: FakeMonotonic
) -> None:
    command = DecoderCommand(("/nonexistent/decoder", "{recording}"), timeout_s=60.0)
    configured = SubprocessDecoder(
        {"lrpt": command}, StationClocks(monotonic=monotonic)
    )

    outcome = configured.start(job(tmp_path)).poll()

    assert isinstance(outcome, DecodeFailure)
    assert "could not start" in outcome.reason


def test_a_restarted_decode_never_reads_its_predecessors_report(
    tmp_path: Path, monotonic: FakeMonotonic
) -> None:
    """D-123: output is cleared first, so a stale report cannot pass as fresh."""
    the_job = job(tmp_path)
    paths = decode_paths(the_job.folder)
    paths.output_dir.mkdir()
    (paths.output_dir / "frames.bin").write_bytes(b"stale")
    paths.report.write_text(json.dumps(REPORT), encoding="utf-8")

    outcome = finish(decoder(monotonic, "silent").start(the_job))

    assert isinstance(outcome, DecodeFailure)
    assert list(paths.output_dir.iterdir()) == []


# --- no shell -----------------------------------------------------------------


def test_a_hostile_file_name_is_an_argument_and_never_a_command(
    tmp_path: Path, monotonic: FakeMonotonic
) -> None:
    name = "pass; touch pwned $(touch pwned2).u8"
    the_job = job(tmp_path, recording_name=name)
    run = decoder(monotonic, "argv", "{output_dir}", "{recording}").start(the_job)

    finish(run)

    received = json.loads(
        (decode_paths(the_job.folder).output_dir / "argv.json").read_text()
    )
    assert received == [str(the_job.folder / name)]
    assert not list(tmp_path.rglob("pwned*"))


def test_the_decoder_is_launched_as_a_list_in_its_own_session(
    tmp_path: Path, monotonic: FakeMonotonic, monkeypatch: pytest.MonkeyPatch
) -> None:
    launched: dict[str, Any] = {}
    real_popen = subprocess.Popen

    def spy(args: Any, **kwargs: Any) -> Any:
        launched.update(kwargs, args=args)
        return real_popen(args, **kwargs)

    monkeypatch.setattr(subprocess_decoder.subprocess, "Popen", spy)

    finish(decoder(monotonic, "silent").start(job(tmp_path)))

    assert isinstance(launched["args"], list)
    assert launched["shell"] is False
    assert launched["start_new_session"] is True
    assert launched["stdin"] is subprocess.DEVNULL


def test_a_decode_runs_at_a_lower_priority(
    tmp_path: Path, monotonic: FakeMonotonic
) -> None:
    """A capture takes precedence over a decode (D-124)."""
    the_job = job(tmp_path)
    before = __import__("os").getpriority(__import__("os").PRIO_PROCESS, 0)

    finish(decoder(monotonic, "nice", "{output_dir}", niceness=7).start(the_job))

    recorded = (decode_paths(the_job.folder).output_dir / "nice.txt").read_text()
    assert int(recorded) == max(before, 7)


# --- stopping it --------------------------------------------------------------


@pytest.mark.parametrize("behaviour", ["hang", "stubborn"])
def test_an_overrunning_decoder_and_everything_it_started_are_stopped(
    tmp_path: Path, monotonic: FakeMonotonic, behaviour: str
) -> None:
    """Terminate the group, wait a bounded time, then kill it (D-124).

    ``stubborn`` ignores SIGTERM, so only the kill that follows can stop it.
    """
    the_job = job(tmp_path)
    configured = decoder(monotonic, behaviour, "{output_dir}", timeout_s=30.0)
    run = configured.start(the_job)
    child_pid_file = decode_paths(the_job.folder).output_dir / "child.pid"
    wait_for_file(child_pid_file)
    child_pid = int(child_pid_file.read_text())

    assert run.poll() is None
    monotonic.now += 31.0
    outcome = run.poll()

    assert outcome == DecodeFailure("the decoder timed out after 30 s")
    assert run._process is not None  # type: ignore[attr-defined]
    assert run._process.returncode is not None  # type: ignore[attr-defined]
    assert gone(child_pid)


def test_a_cancelled_decode_is_stopped_and_says_so(
    tmp_path: Path, monotonic: FakeMonotonic
) -> None:
    the_job = job(tmp_path)
    run = decoder(monotonic, "hang", "{output_dir}").start(the_job)
    child_pid_file = decode_paths(the_job.folder).output_dir / "child.pid"
    wait_for_file(child_pid_file)

    run.cancel()

    assert run.poll() == DecodeFailure("the decode was cancelled")
    assert gone(int(child_pid_file.read_text()))
