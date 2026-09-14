"""A decoder stand-in for tests: a real program, whose behaviour its argv chooses.

Run by ``SubprocessDecoder`` exactly as a SatDump wrapper would be — a separate
process, no shell, logs to files — so what the tests exercise is the supervision
and not a rehearsal of it. Standard library only.

    fake_decoder.py report  <report_path> <fixture>    copy fixture to the report
    fake_decoder.py chatty  <report_path> <fixture>    2 MiB to each log, then report
    fake_decoder.py exit    <status>                   exit with that status
    fake_decoder.py silent                             exit 0 and write nothing
    fake_decoder.py hang    <output_dir>               start a child, then sleep
    fake_decoder.py stubborn <output_dir>              as hang, ignoring SIGTERM
    fake_decoder.py argv    <output_dir> <args...>     record argv, then exit 0
    fake_decoder.py nice    <output_dir>               record its niceness, exit 0

Lives in ``reception_fakes`` rather than a directory named for data or products,
both of which the repository ignores.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

LOG_BYTES = 2 * 1024 * 1024


def hang(output_dir: Path, *, ignore_term: bool) -> int:
    """Start a child in the same group, say who it is, and never finish."""
    if ignore_term:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    (output_dir / "child.pid").write_text(str(child.pid), encoding="utf-8")
    time.sleep(600)
    return 0


def report(rest: list[str]) -> int:
    shutil.copyfile(rest[1], rest[0])
    return 0


def chatty(rest: list[str]) -> int:
    sys.stdout.buffer.write(b"o" * LOG_BYTES)
    sys.stderr.buffer.write(b"e" * LOG_BYTES)
    return report(rest)


def record_argv(rest: list[str]) -> int:
    (Path(rest[0]) / "argv.json").write_text(json.dumps(rest[1:]), encoding="utf-8")
    return 0


def record_niceness(rest: list[str]) -> int:
    time.sleep(0.5)
    niceness = os.getpriority(os.PRIO_PROCESS, 0)
    (Path(rest[0]) / "nice.txt").write_text(str(niceness), encoding="utf-8")
    return 0


BEHAVIOURS = {
    "report": report,
    "chatty": chatty,
    "exit": lambda rest: int(rest[0]),
    "silent": lambda _rest: 0,
    "hang": lambda rest: hang(Path(rest[0]), ignore_term=False),
    "stubborn": lambda rest: hang(Path(rest[0]), ignore_term=True),
    "argv": record_argv,
    "nice": record_niceness,
}


if __name__ == "__main__":
    sys.exit(BEHAVIOURS[sys.argv[1]](sys.argv[2:]))
