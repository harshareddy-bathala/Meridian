"""A long run's own record: where it started, what it ran on, and how to resume it.

A seventy-two hour run outlives a terminal, an SSH session and sometimes the
tool itself. So the run writes ``run.json`` into ``--out`` before its first
fault (D-257), holding:

- **its settings** — seed, hours, stations and gaps;
- **when it started**, by the wall clock;
- **what it ran on** — the commit, the platform image and its digest, the
  machine's architecture and the Docker and Compose versions. These are what a
  reader needs to say *which* software survived.

**A run starts fresh or not at all.** An ``--out`` already holding a run's files
is refused, because a second run appended to a first's platform ledger would be
judged as one. ``--resume`` is the way back into a run whose tool stopped:
- it reads ``run.json``;
- it mends and closes any platform fault the ledger left open, marked ``ended:
  restart`` as the simulator marks its own (D-189);
- it carries on from the wall clock, skipping what fell in the gap and
  recording the gap.

Pure where it decides anything, so ``tests/unit/test_long_run_watch.py`` pins
it without Docker.
"""

from __future__ import annotations

import json
import platform
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

RUN_FILE = "run.json"
PLATFORM_LEDGER = "platform-faults.jsonl"
REPORT_FILE = "report.json"
"""Files whose presence means ``--out`` already holds a run."""

TAKEN = (RUN_FILE, PLATFORM_LEDGER, REPORT_FILE)


@dataclass
class RunState:
    """Everything needed to judge a run, or to carry on with it."""

    started: str
    seed: int
    hours: float
    stations: int
    fault_gap_minutes: float
    sample_every_minutes: float
    settle_minutes: float
    memory_slope_mib_per_hour: float
    disk_free_gib: float
    up: bool = False
    """Whether the tool brought the stack up, and so may restart its simulator."""
    environment: dict[str, object] = field(default_factory=dict)
    interruptions: list[dict[str, str]] = field(default_factory=list)


def refuse_taken(out: Path) -> str | None:
    """Why ``out`` cannot hold a new run, or None if it can."""
    held = [name for name in TAKEN if (out / name).exists()]
    if held:
        return (
            f"{out} already holds a run ({', '.join(held)}): give a new --out,"
            " or --resume to carry this one on, or --judge-only to judge it"
        )
    return None


def write_state(out: Path, state: RunState) -> None:
    """Write ``run.json``, whole, before anything that reads it."""
    out.mkdir(parents=True, exist_ok=True)
    scratch = out / f".{RUN_FILE}.partial"
    scratch.write_text(json.dumps(asdict(state), indent=2) + "\n", "utf-8")
    scratch.replace(out / RUN_FILE)


def read_state(out: Path) -> RunState:
    """Read ``run.json`` back.

    Raises:
        FileNotFoundError: There is no run here to resume or judge.
    """
    stored = json.loads((out / RUN_FILE).read_text("utf-8"))
    return RunState(**stored)


def left_open(ledger: str) -> list[dict[str, object]]:
    """Every platform fault the ledger opened and never closed, oldest first."""
    open_now: dict[tuple[str, str], dict[str, object]] = {}
    for line in ledger.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        key = (str(row["target"]), str(row["kind"]))
        if row["event"] == "open":
            open_now[key] = row
        elif row["event"] == "close":
            open_now.pop(key, None)
    return sorted(open_now.values(), key=lambda one: str(one["at"]))


def restart_close(opened: dict[str, object], at: datetime) -> str:
    """The ledger line that closes a fault a stopped tool left open."""
    line = {key: opened[key] for key in ("ledger", "run_id", "kind", "target")} | {
        "event": "close",
        "at": at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "detail": {"ended": "restart"},
    }
    return json.dumps(line, sort_keys=True) + "\n"


def environment(
    run: Callable[[list[str]], tuple[int, str]],
    api_container: str | None,
) -> dict[str, object]:
    """What the run ran on, each part None where it could not be read.

    Args:
        run: Runs a command on this host and gives its status and output.
        api_container: The API's container: its image is what actually ran,
            whatever ``MERIDIAN_IMAGE`` was meant to name.
    """

    def read(command: list[str]) -> str | None:
        status, output = run(command)
        return (output.strip() or None) if status == 0 else None

    commit = read(["git", "rev-parse", "HEAD"])
    changed = read(["git", "status", "--porcelain", "--untracked-files=no"])
    image = image_id = repo_digest = None
    if api_container:
        image = read(
            ["docker", "inspect", "--format", "{{.Config.Image}}", api_container]
        )
        image_id = read(["docker", "inspect", "--format", "{{.Image}}", api_container])
    if image:
        repo_digest = read(
            ["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", image]
        )
    return {
        "commit": commit,
        "dirty": None if commit is None else changed is not None,
        "image": image,
        "image_id": image_id,
        "image_digests": json.loads(repo_digest) if repo_digest else [],
        "arch": platform.machine(),
        "docker": read(["docker", "version", "--format", "{{.Server.Version}}"]),
        "compose": read(["docker", "compose", "version", "--short"]),
    }
