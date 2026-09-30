"""Inject platform faults into a running deployment, and write them down. Stdlib only.

The simulator breaks stations; this breaks the platform. The simulator must not
reach into the platform (D-075), so a restart of the API or the database is done
by the operator's tool, from the host, with `docker compose`, as an operator
would do it by hand (D-194). Four faults:

- `platform_restart` — `restart api`. Stations retry and resume; nobody registers
  again.
- `database_restart` — `restart db`. The pool replaces its dead connections without
  failing a request (D-193); a jobs round fails and the next one succeeds.
- `scheduler_down` — `stop jobs`, wait, `start jobs`. `SchedulerUnavailable` fires,
  stations go on executing held work, and the next round catches up.
- `api_paused` — `pause api`, wait, `unpause api`. Requests hang and time out;
  stations resend, and nothing is stored twice.

Each is written to the run's fault ledger as it opens and closes — the same file,
in the same format, that the simulator writes (`meridian_sim/ledger.py` defines it,
and `tests/unit/test_chaos_tool.py` reads this tool's lines with that reader). A
recovery step always runs, in a `finally`: a tool that stopped the scheduler and
then failed must not leave it stopped.

    python deploy/tools/chaos.py inject database_restart --ledger sim-state/faults.jsonl
    python deploy/tools/chaos.py run --seed 4471 --hours 72 --ledger … --plan

`run` draws a seeded schedule of faults and injects them in turn; `--plan` prints
it and does nothing, so what a long run will do can be read before it starts.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from compose_db import Compose, ToolError, add_compose_arguments, compose_from

LEDGER_VERSION = 1
"""The ledger format this tool writes; `meridian_sim.ledger` defines it."""


@dataclass(frozen=True, slots=True)
class PlatformFault:
    """One fault: the service it targets, how to break it and how to mend it."""

    service: str
    breaks: tuple[str, ...]
    mends: tuple[str, ...]
    default_duration_s: float
    """How long it stays broken between the two, zero for an instant."""


FAULTS: dict[str, PlatformFault] = {
    "platform_restart": PlatformFault("api", ("restart", "api"), (), 0.0),
    "database_restart": PlatformFault("db", ("restart", "db"), (), 0.0),
    "scheduler_down": PlatformFault("jobs", ("stop", "jobs"), ("start", "jobs"), 300.0),
    "api_paused": PlatformFault("api", ("pause", "api"), ("unpause", "api"), 20.0),
}
"""Every fault this tool injects, by the name the ledger records.

`scheduler_down` lasts five minutes, long enough for `SchedulerUnavailable`'s
one-minute `for:` and a missed round. `api_paused` lasts twenty seconds, past a
station's request timeout and short of its heartbeat going stale, so what it tests
is the resend, not liveness.
"""

MEAN_GAP_S = 3600.0
"""The mean time between two faults in a `run`, drawn exponentially.

An hour: a seventy-two hour run sees about seventy, each far enough from the
next that the platform has recovered before it is broken again — a platform
that never recovers between faults measures the fault rate, not the recovery.
"""

MIN_GAP_S = 900.0
"""No fault starts sooner than this after the previous one ends."""


@dataclass(frozen=True, slots=True)
class Planned:
    """One fault a `run` will inject, at an offset from the run's start."""

    at_s: float
    kind: str
    duration_s: float


def plan(
    seed: int, hours: float, mean_gap_s: float = MEAN_GAP_S
) -> tuple[Planned, ...]:
    """The faults a `run` of `hours` injects, drawn from `seed`.

    Pure, so the plan can be printed and tested; the same seed, length and gap
    give the same faults at the same offsets. `mean_gap_s` is shortened for a
    rehearsal, which is too short to meet a fault an hour; `MIN_GAP_S` still
    holds between faults.
    """
    stream = random.Random(f"{seed}:platform-faults")
    kinds = sorted(FAULTS)
    end_s = hours * 3600.0
    planned: list[Planned] = []
    at_s = stream.expovariate(1.0 / mean_gap_s)
    while at_s < end_s:
        kind = stream.choice(kinds)
        duration_s = FAULTS[kind].default_duration_s
        planned.append(Planned(round(at_s, 1), kind, duration_s))
        at_s += duration_s + MIN_GAP_S + stream.expovariate(1.0 / mean_gap_s)
    return tuple(planned)


def ledger_line(event: str, run_id: str, kind: str, at: datetime) -> str:
    """One ledger line for a platform fault, as `meridian_sim.ledger` reads it."""
    if at.tzinfo is None:
        raise ValueError("a ledger instant must be timezone-aware")
    line = {
        "ledger": LEDGER_VERSION,
        "event": event,
        "run_id": run_id,
        "kind": kind,
        "target": f"platform:{FAULTS[kind].service}",
        "at": at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
    }
    return json.dumps(line, sort_keys=True) + "\n"


@dataclass(frozen=True, slots=True)
class Effects:
    """Everything `inject` does to the world, so a test can replace all of it."""

    run: Callable[[list[str]], int]
    sleep: Callable[[float], None]
    now: Callable[[], datetime]


def real_effects() -> Effects:
    """Commands on this host, the wall clock, and real waiting."""
    return Effects(
        run=lambda command: subprocess.run(command, check=False).returncode,
        sleep=time.sleep,
        now=lambda: datetime.now(UTC),
    )


def append(path: Path, text: str) -> None:
    """Append one line to the ledger, and get it to disk before going on."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def inject(
    compose: Compose,
    kind: str,
    ledger: Path,
    run_id: str,
    duration_s: float | None = None,
    effects: Effects | None = None,
) -> None:
    """Break one service, wait, mend it, and write the window to the ledger.

    The window opens before the break and closes after the mend, so it covers
    every moment the service was broken. A mend always runs; if the break
    itself failed, the window is closed anyway, since nothing was broken for
    longer than the attempt.

    Raises:
        ToolError: A `docker compose` step failed.
    """
    fault = FAULTS[kind]
    act = effects or real_effects()
    wait_s = fault.default_duration_s if duration_s is None else duration_s

    append(ledger, ledger_line("open", run_id, kind, act.now()))
    try:
        _step(act, compose.command(*fault.breaks))
        if fault.mends:
            act.sleep(wait_s)
    finally:
        try:
            if fault.mends:
                _step(act, compose.command(*fault.mends))
        finally:
            append(ledger, ledger_line("close", run_id, kind, act.now()))


def _step(act: Effects, command: list[str]) -> None:
    """Run one command, refusing to go on if it failed."""
    print("->", " ".join(command), flush=True)
    status = act.run(command)
    if status != 0:
        raise ToolError(f"{' '.join(command)} exited with status {status}")


def run(
    compose: Compose,
    planned: Sequence[Planned],
    ledger: Path,
    run_id: str,
    effects: Effects | None = None,
    started: float | None = None,
) -> int:
    """Inject every planned fault at its offset. Returns how many ran.

    A failed step stops the run: a deployment the tool could not mend is one a
    person must look at before anything else is done to it.
    """
    act = effects or real_effects()
    origin = act.now().timestamp() if started is None else started
    for one in planned:
        act.sleep(max(0.0, origin + one.at_s - act.now().timestamp()))
        inject(compose, one.kind, ledger, run_id, one.duration_s, act)
    return len(planned)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    one = commands.add_parser("inject", help="inject one fault now")
    one.add_argument("kind", choices=sorted(FAULTS))
    one.add_argument(
        "--duration", type=float, default=None, help="seconds broken, if not default"
    )

    many = commands.add_parser("run", help="inject a seeded schedule of faults")
    many.add_argument("--seed", type=int, required=True)
    many.add_argument("--hours", type=float, required=True)
    many.add_argument(
        "--plan", action="store_true", help="print the schedule and do nothing"
    )

    for sub in (one, many):
        sub.add_argument("--ledger", type=Path, required=True, help="the fault ledger")
        sub.add_argument(
            "--run-id", default="platform-faults", help="the run to stamp lines with"
        )
        add_compose_arguments(sub)
    args = parser.parse_args(argv)

    try:
        if args.command == "inject":
            inject(
                compose_from(args), args.kind, args.ledger, args.run_id, args.duration
            )
            return 0
        planned = plan(args.seed, args.hours)
        if args.plan:
            for one in planned:
                print(f"{one.at_s:>10.1f}s  {one.kind:<18}  {one.duration_s:.0f}s")
            return 0
        run(compose_from(args), planned, args.ledger, args.run_id)
    except ToolError as refused:
        print(f"chaos: {refused}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
