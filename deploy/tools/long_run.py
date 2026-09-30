"""Run the whole stack under faults for hours, and record what happened. Stdlib only.

Stage 21's long run: the complete compose stack, with simulated stations under
the ``chaos`` scenario and platform faults drawn from a seed, for hours
unattended — seventy-two for the roadmap, two for a rehearsal (D-198). Every
``--sample-every`` minutes it records, from outside the stack, what the roadmap
asks of it:

- **crashes and restarts** — each container's state and its restart count. A
  restart the tool ordered is in the ledger; a restart count above zero is the
  container dying on its own;
- **alerts** — what Prometheus has firing;
- **queue growth** — observations waiting in the simulator's upload queues;
- **resource use** — each container's CPU and memory, from ``docker stats``.

At the end it waits for the stack to settle, then judges the run:

- **false positives** — every alert that fired with no fault in either ledger
  open at the time, or within the grace after one closed;
- **data loss** — observations still queued once the faults have stopped and the
  stations have had time to drain;
- **the verdict** — ``meridian reliability faults`` over both ledgers, run inside
  the API container against the run's own Prometheus, so SC-5's alert latency is
  measured rather than derived.

Everything lands in ``--out``: the merged ledger, every sample, the verdict and
a ``report.json`` a person or a later stage can read.

    python deploy/tools/long_run.py --hours 72 --seed 4471 --out runs/long-72h \\
        --project-name meridian-s21 --up --stations 10

Pure where it decides anything, so ``tests/unit/test_long_run.py`` pins the
schedule, the parsing and the false-positive rule without Docker.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.parse
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from chaos import Effects, Planned, inject, plan
from compose_db import Compose, ToolError, add_compose_arguments, compose_from

REPORT_FORMAT = "meridian-long-run/1"
RUN_ID = "long-run"
STATION_LEDGER = "/var/lib/meridian-sim/faults.jsonl"
"""Where the simulator writes its ledger, inside its state volume."""

ALERT_GRACE = timedelta(minutes=10)
"""How long after a fault closes an alert it caused may still be firing.

Long enough for a `for:` of five minutes plus an evaluation and a scrape; an
alert still firing later than this, with nothing open, is a false positive.
"""

PROFILES = ("--profile", "sim", "--profile", "metrics")

WAIT_STEP_S = 30.0
"""The longest single sleep. Waits are cut into these and re-read the wall clock
after each, so a host that suspends mid-wait is noticed on waking rather than
slept through: a monotonic sleep does not count the time a laptop was asleep."""

PAUSE_THRESHOLD = timedelta(minutes=2)
"""How far the wall clock may run past a sleep before the host counts as having
been asleep. Far above scheduling jitter; far below a real suspend."""


ONE_SHOT = frozenset({"migrate", "sim-seed"})
"""Services that run once and exit; exiting cleanly is their success."""


@dataclass(frozen=True, slots=True)
class Container:
    """One container at one sample."""

    service: str
    state: str
    restarts: int
    exit_code: int = 0

    @property
    def healthy(self) -> bool:
        """Running, or a one-shot service that finished cleanly."""
        if self.service in ONE_SHOT:
            return self.state == "running" or (
                self.state == "exited" and self.exit_code == 0
            )
        return self.state == "running"


@dataclass(frozen=True, slots=True)
class Usage:
    """One container's resource use at one sample."""

    name: str
    cpu_percent: float
    memory_mib: float


@dataclass
class Sample:
    """Everything recorded at one instant of the run."""

    at: str
    containers: list[Container] = field(default_factory=list)
    firing: list[str] = field(default_factory=list)
    queued: int | None = None
    usage: list[Usage] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Interval:
    """A stretch of time, both ends as instants; ``end`` ``None`` if still open."""

    name: str
    start: datetime
    end: datetime | None


def timeline(
    faults: Sequence[Planned], hours: float, sample_every_s: float
) -> list[tuple[float, str, Planned | None]]:
    """Every fault and every sample of a run, in the order they happen.

    A sample at zero and one at the end, and one every ``sample_every_s``
    between. A fault and a sample at the same instant take the fault first, so
    the sample sees it.
    """
    end_s = hours * 3600.0
    samples = [
        (float(at), "sample", None)
        for at in range(0, int(end_s) + 1, max(1, int(sample_every_s)))
    ]
    if samples[-1][0] < end_s:
        samples.append((end_s, "sample", None))
    events: list[tuple[float, str, Planned | None]] = [
        (one.at_s, "fault", one) for one in faults if one.at_s < end_s
    ]
    return sorted(events + samples, key=lambda one: (one[0], one[1] != "fault"))


def parse_containers(lines: Iterable[str]) -> list[Container]:
    """``docker inspect`` output: ``service restarts state exit-code`` per line.

    The service is compose's own label on the container, not a piece of its
    name: a project name may contain hyphens, and so may a service's.
    """
    found: list[Container] = []
    for line in lines:
        parts = line.split()
        if len(parts) != 4:
            continue
        service, restarts, state, code = parts
        found.append(Container(service, state, int(restarts), int(code)))
    return sorted(found, key=lambda one: one.service)


def parse_usage(lines: Iterable[str]) -> list[Usage]:
    """``docker stats --no-stream --format '{{json .}}'``, one container a line."""
    found: list[Usage] = []
    for line in lines:
        if not line.strip():
            continue
        row = json.loads(line)
        memory = row.get("MemUsage", "0MiB / 0").split("/")[0].strip()
        found.append(
            Usage(
                name=row.get("Name", "?"),
                cpu_percent=float(row.get("CPUPerc", "0%").rstrip("%") or 0),
                memory_mib=_mebibytes(memory),
            )
        )
    return sorted(found, key=lambda one: one.name)


def _mebibytes(text: str) -> float:
    """``12.5MiB``, ``1.2GiB`` or ``980KiB`` as mebibytes."""
    units = {"KiB": 1 / 1024, "MiB": 1.0, "GiB": 1024.0, "B": 1 / (1024 * 1024)}
    for unit, scale in units.items():
        if text.endswith(unit) and text[: -len(unit)].replace(".", "").isdigit():
            return round(float(text[: -len(unit)]) * scale, 1)
    return 0.0


def parse_firing(body: str) -> list[str]:
    """The names of alerts firing, from Prometheus's ``/api/v1/alerts``."""
    alerts = json.loads(body).get("data", {}).get("alerts", [])
    return sorted(
        {one["labels"]["alertname"] for one in alerts if one.get("state") == "firing"}
    )


def parse_alert_history(body: str, step_s: float) -> list[Interval]:
    """Each stretch an alert was firing, from a ``query_range`` over ``ALERTS``.

    Consecutive samples of one series, no more than one step apart, are one
    stretch; a gap starts another.
    """
    stretches: list[Interval] = []
    for series in json.loads(body).get("data", {}).get("result", []):
        name = series["metric"].get("alertname", "?")
        stamps = [float(at) for at, value in series.get("values", []) if value == "1"]
        start = previous = None
        for stamp in stamps:
            if start is None:
                start = previous = stamp
            elif previous is not None and stamp - previous > step_s * 1.5:
                stretches.append(_interval(name, start, previous))
                start = stamp
            previous = stamp
        if start is not None and previous is not None:
            stretches.append(_interval(name, start, previous))
    return sorted(stretches, key=lambda one: (one.start, one.name))


def _interval(name: str, start: float, end: float) -> Interval:
    return Interval(
        name, datetime.fromtimestamp(start, UTC), datetime.fromtimestamp(end, UTC)
    )


def ledger_windows(lines: Iterable[str]) -> list[Interval]:
    """Every fault window in a ledger, named by kind and target."""
    windows: list[Interval] = []
    open_at: dict[tuple[str, str], int] = {}
    for line in lines:
        if not line.strip():
            continue
        row = json.loads(line)
        key = (row["target"], row["kind"])
        at = datetime.fromisoformat(row["at"].replace("Z", "+00:00"))
        if row["event"] == "open":
            open_at[key] = len(windows)
            windows.append(Interval(f"{row['kind']} on {row['target']}", at, None))
        elif row["event"] == "close" and key in open_at:
            index = open_at.pop(key)
            windows[index] = Interval(windows[index].name, windows[index].start, at)
    return windows


STATION_SILENCING = frozenset(
    {"network_down", "partition", "heartbeat_delayed", "restart", "token_revoked"}
)
PLATFORM_OUTAGES = frozenset({"platform_restart", "database_restart", "api_paused"})
STATION_FAULTS = STATION_SILENCING | {
    "upload_blocked",
    "slow_api",
    "receiver_down",
    "decoder_degraded",
    "clock_drift",
    "declines",
}

CAUSES: dict[str, frozenset[str]] = {
    "StationStale": STATION_SILENCING | PLATFORM_OUTAGES,
    "StationOffline": STATION_SILENCING | PLATFORM_OUTAGES,
    "HeartbeatIngestionStopped": STATION_SILENCING | PLATFORM_OUTAGES,
    "ApiUnavailable": frozenset({"platform_restart", "api_paused"}),
    "DatabaseUnavailable": frozenset({"database_restart"}),
    "SchedulerUnavailable": frozenset({"scheduler_down"}),
    "ScheduledTaskStalled": frozenset({"scheduler_down", "database_restart"}),
    "ScheduledTaskNeverSucceeded": frozenset({"scheduler_down", "database_restart"}),
    "ObservationsOverdue": STATION_SILENCING | {"upload_blocked", "slow_api"},
    "LossBudgetThresholdReached": STATION_FAULTS | PLATFORM_OUTAGES,
}
"""Which injected fault kinds can raise each alert.

An alert is explained only by a fault that could have caused it: under the
`chaos` scenario some fault is open on some station almost all the time, so
"any fault was open" would explain every alert and find no false positive
ever. An alert not listed here — `SchemaMigrationMismatch`, say — is caused by
no fault the run injects, and so is always a false positive.
"""

HOST_ASLEEP = "host asleep"


def _kind(window: Interval) -> str:
    """The fault kind a ledger window was named for: ``kind on target``."""
    return window.name.split(" on ", 1)[0]


def false_positives(
    alerts: Sequence[Interval],
    faults: Sequence[Interval],
    run_end: datetime,
    grace: timedelta = ALERT_GRACE,
) -> list[Interval]:
    """Alerts that fired with no fault that could cause them open, nor closed
    within ``grace``. A host that slept explains any alert; it fails the run
    on its own account."""
    unexplained: list[Interval] = []
    for alert in alerts:
        alert_end = alert.end or run_end
        causes = CAUSES.get(alert.name, frozenset())
        explained = any(
            (fault.name == HOST_ASLEEP or _kind(fault) in causes)
            and fault.start <= alert_end
            and alert.start <= (fault.end or run_end) + grace
            for fault in faults
        )
        if not explained:
            unexplained.append(alert)
    return unexplained


@dataclass(frozen=True, slots=True)
class Host:
    """Everything the run does to the world, replaceable in a test."""

    run: Callable[[list[str], str | None], tuple[int, str]]
    sleep: Callable[[float], None]
    now: Callable[[], datetime]


def real_host() -> Host:
    """Commands on this machine, real waiting, the wall clock."""

    def run(command: list[str], stdin: str | None) -> tuple[int, str]:
        result = subprocess.run(
            command, input=stdin, text=True, capture_output=True, check=False
        )
        # Standard output alone when it worked: `compose run` narrates on
        # stderr, and its narration must not become part of a ledger. Both
        # when it failed, so the error says why.
        if result.returncode == 0:
            return 0, result.stdout
        return result.returncode, result.stdout + result.stderr

    return Host(run=run, sleep=time.sleep, now=lambda: datetime.now(UTC))


@dataclass
class Waiter:
    """Waits against the wall clock, and remembers when the host was asleep.

    A long run on a laptop that suspends is not an unattended run: the stack
    was frozen, and what it recorded afterwards is of a different experiment.
    So every wait is cut into short sleeps, and a sleep the wall clock says
    lasted far longer than asked is recorded as a pause (D-198).
    """

    host: Host
    pauses: list[Interval] = field(default_factory=list)

    def until(self, due: datetime) -> None:
        """Return at ``due``, however the host spent the time before it."""
        while True:
            before = self.host.now()
            if before >= due:
                return
            step = min(WAIT_STEP_S, (due - before).total_seconds())
            self.host.sleep(step)
            after = self.host.now()
            asleep = after - before - timedelta(seconds=step)
            if asleep > PAUSE_THRESHOLD:
                self.pauses.append(
                    Interval(HOST_ASLEEP, before + timedelta(seconds=step), after)
                )

    def sleep(self, seconds: float) -> None:
        """Wait ``seconds`` of wall time; a stand-in for ``time.sleep``."""
        self.until(self.host.now() + timedelta(seconds=seconds))


def sample(compose: Compose, host: Host) -> Sample:
    """One look at the stack, from outside it."""
    taken = Sample(at=host.now().isoformat())
    # Every container, stopped ones too: a service that died and stayed down
    # would vanish from `ps -q`, and a sample must not read its absence as calm.
    status, ids = host.run(compose.command("ps", "-a", "-q"), None)
    if status == 0 and ids.strip():
        status, inspected = host.run(
            [
                "docker",
                "inspect",
                "--format",
                '{{index .Config.Labels "com.docker.compose.service"}}'
                " {{.RestartCount}} {{.State.Status}} {{.State.ExitCode}}",
                *ids.split(),
            ],
            None,
        )
        taken.containers = parse_containers(inspected.splitlines())
        status, stats = host.run(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}", *ids.split()],
            None,
        )
        if status == 0:
            taken.usage = parse_usage(stats.splitlines())
    status, body = host.run(_in_prometheus(compose, "/api/v1/alerts"), None)
    if status == 0:
        taken.firing = parse_firing(body)
    status, count = host.run(
        compose.command(
            "exec",
            "-T",
            "simulator",
            "sh",
            "-c",
            "find /var/lib/meridian-sim -path '*/outbox/*' -type f | wc -l",
        ),
        None,
    )
    if status == 0 and count.strip().isdigit():
        taken.queued = int(count.strip())
    return taken


def _in_prometheus(compose: Compose, path: str) -> list[str]:
    """A GET against the run's Prometheus, from inside its container."""
    return compose.command(
        "exec", "-T", "prometheus", "wget", "-qO-", f"http://localhost:9090{path}"
    )


def alert_history(
    compose: Compose, host: Host, start: datetime, end: datetime, step_s: float = 30
) -> list[Interval]:
    """Every stretch any alert fired between ``start`` and ``end``."""
    query = urllib.parse.urlencode(
        {
            "query": 'ALERTS{alertstate="firing"}',
            "start": f"{start.timestamp():.0f}",
            "end": f"{end.timestamp():.0f}",
            "step": f"{step_s:.0f}",
        }
    )
    status, body = host.run(
        _in_prometheus(compose, f"/api/v1/query_range?{query}"), None
    )
    if status != 0:
        raise ToolError(f"the alert history could not be read: {body.strip()}")
    return parse_alert_history(body, step_s)


def run_long(args: argparse.Namespace, host: Host) -> dict[str, object]:
    """The whole run: faults and samples on their timeline, then the judgement."""
    compose = compose_from(args)
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    platform_ledger = out / "platform-faults.jsonl"
    if args.up:
        _step(host, compose.command(*PROFILES, "up", "-d"))

    faults = plan(args.seed, args.hours, args.fault_gap_minutes * 60)
    started = host.now()
    samples: list[Sample] = []
    waiter = Waiter(host)
    effects = Effects(
        run=lambda command: host.run(command, None)[0],
        sleep=waiter.sleep,
        now=host.now,
    )
    for at_s, kind, fault in timeline(
        faults, args.hours, args.sample_every_minutes * 60
    ):
        waiter.until(started + timedelta(seconds=at_s))
        if kind == "fault" and fault is not None:
            inject(
                compose, fault.kind, platform_ledger, RUN_ID, fault.duration_s, effects
            )
        else:
            samples.append(sample(compose, host))
            _write_samples(out, samples)

    ended = host.now()
    waiter.sleep(args.settle_minutes * 60)
    settled = sample(compose, host)
    samples.append(settled)
    _write_samples(out, samples)

    # The fleet is stopped before its ledger is read. A fault still open when
    # the ledger was copied would otherwise be judged against heartbeats sent
    # after the copy, and read as a fault that did not hold.
    _step(host, compose.command("stop", "simulator"))
    report = _report(args, started, ended, samples, settled, [], [])
    report["fleet_stopped"] = host.now().isoformat()
    report["faults_injected"] = {
        "platform": len([one for one in faults if one.at_s < args.hours * 3600])
    }
    return judge(compose, host, out, report, waiter.pauses)


def judge(
    compose: Compose,
    host: Host,
    out: Path,
    report: dict[str, object],
    pauses: Sequence[Interval] = (),
) -> dict[str, object]:
    """Judge a finished run from its ledgers, its stack and its Prometheus.

    Also what ``--judge-only`` runs, against a stack a run left standing, so a
    judgement lost to a defect in this tool need not cost the run again.
    """
    started = datetime.fromisoformat(str(report["started"]))
    ended = datetime.fromisoformat(str(report["ended"]))
    ledger = _merged_ledger(compose, host, out, out / "platform-faults.jsonl")
    verdict_status, verdict = host.run(
        compose.command(
            "exec",
            "-T",
            "api",
            "meridian",
            "reliability",
            "faults",
            "--ledger",
            "-",
            "--prometheus",
            "http://prometheus:9090",
        ),
        ledger,
    )
    (out / "verdict.txt").write_text(verdict, encoding="utf-8")

    # Only up to the fleet being stopped: the alerts that stopping it raises —
    # heartbeats ceasing — are the judgement's doing, not the run's.
    alerts = alert_history(compose, host, started, _fleet_stopped(report, out, host))
    windows = ledger_windows(ledger.splitlines())
    # An alert raised by the host having slept is the host's, not a false one;
    # the pause itself fails the run.
    unexplained = false_positives(alerts, [*windows, *pauses], ended)
    report["alerts_fired"] = len(alerts)
    report["alerts_by_name"] = _count(one.name for one in alerts)
    report["false_positives"] = [
        {"alert": one.name, "start": one.start.isoformat()} for one in unexplained
    ]
    if pauses or "host_pauses" not in report:
        report["host_pauses"] = [
            {"start": one.start.isoformat(), "end": (one.end or ended).isoformat()}
            for one in pauses
        ]
    report["verdict"] = {"exit": verdict_status, "summary": _last_line(verdict)}
    injected = dict(report.get("faults_injected") or {})  # type: ignore[call-overload]
    injected["windows_in_ledger"] = len(windows)
    report["faults_injected"] = injected
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n", "utf-8")
    return report


def _report(
    args: argparse.Namespace,
    started: datetime,
    ended: datetime,
    samples: Sequence[Sample],
    settled: Sample,
    alerts: Sequence[Interval],
    unexplained: Sequence[Interval],
) -> dict[str, object]:
    restarts = {
        one.service: one.restarts for one in (samples[-1].containers if samples else [])
    }
    peak_memory: dict[str, float] = {}
    for taken in samples:
        for use in taken.usage:
            peak_memory[use.name] = max(peak_memory.get(use.name, 0.0), use.memory_mib)
    queues = [one.queued for one in samples if one.queued is not None]
    return {
        "format": REPORT_FORMAT,
        "simulated": True,
        "seed": args.seed,
        "hours": args.hours,
        "started": started.isoformat(),
        "ended": ended.isoformat(),
        "samples": len(samples),
        "unplanned_restarts": {name: n for name, n in restarts.items() if n},
        "not_running_at_end": [
            one.service for one in settled.containers if not one.healthy
        ],
        "alerts_fired": len(alerts),
        "alerts_by_name": _count(one.name for one in alerts),
        "false_positives": [
            {"alert": one.name, "start": one.start.isoformat()} for one in unexplained
        ],
        "queue_max": max(queues, default=None),
        "queue_after_settling": settled.queued,
        "peak_memory_mib": peak_memory,
    }


def _count(names: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    return counts


def _fleet_stopped(report: dict[str, object], out: Path, host: Host) -> datetime:
    """When the run stopped its fleet: recorded, else its last sample, else now."""
    if "fleet_stopped" in report:
        return datetime.fromisoformat(str(report["fleet_stopped"]))
    samples_file = out / "samples.json"
    if samples_file.exists():
        taken = json.loads(samples_file.read_text("utf-8"))
        if taken:
            return datetime.fromisoformat(taken[-1]["at"])
    return host.now()


def _merged_ledger(
    compose: Compose, host: Host, out: Path, platform_ledger: Path
) -> str:
    """The simulator's ledger and the platform's, as one text, kept in ``out``.

    Read through a one-off container on the simulator's volume, not ``exec``:
    the fleet is stopped by then, and ``exec`` needs it running. A ledger that
    cannot be read stops the judgement — judged against the platform's faults
    alone, every station fault's alert would read as a false positive.
    """
    status, stations = host.run(
        compose.command(
            "run",
            "--rm",
            "--no-deps",
            "--entrypoint",
            "cat",
            "simulator",
            STATION_LEDGER,
        ),
        None,
    )
    if status != 0:
        raise ToolError(f"the simulator's ledger could not be read: {stations[-400:]}")
    platform = platform_ledger.read_text("utf-8") if platform_ledger.exists() else ""
    merged = stations + platform
    (out / "faults.jsonl").write_text(merged, encoding="utf-8")
    return merged


def _write_samples(out: Path, samples: Sequence[Sample]) -> None:
    (out / "samples.json").write_text(
        json.dumps([asdict(one) for one in samples], indent=2) + "\n", "utf-8"
    )


def _step(host: Host, command: list[str]) -> None:
    status, output = host.run(command, None)
    if status != 0:
        raise ToolError(f"{' '.join(command)} failed: {output.strip()[-400:]}")


def _last_line(text: str) -> str:
    lines = [one for one in text.splitlines() if one.strip()]
    return lines[-1] if lines else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--hours", type=float, required=True)
    parser.add_argument("--seed", type=int, default=4471)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--up", action="store_true", help="bring the stack up with sim and metrics"
    )
    parser.add_argument(
        "--stations",
        type=int,
        default=10,
        help="with --up: SIMULATOR_STATION_COUNT, under the chaos scenario",
    )
    parser.add_argument("--sample-every-minutes", type=float, default=60.0)
    parser.add_argument(
        "--fault-gap-minutes",
        type=float,
        default=60.0,
        help="mean time between platform faults; shorter for a rehearsal",
    )
    parser.add_argument("--settle-minutes", type=float, default=10.0)
    parser.add_argument(
        "--judge-only",
        action="store_true",
        help="judge a finished run again from --out and the stack it left up",
    )
    add_compose_arguments(parser)
    args = parser.parse_args(argv)
    if args.up:
        os.environ.setdefault("SIMULATOR_SCENARIO", "chaos")
        os.environ.setdefault("SIMULATOR_STATION_COUNT", str(args.stations))
        os.environ.setdefault("SIMULATOR_SEED", str(args.seed))

    try:
        if args.judge_only:
            existing = json.loads((args.out / "report.json").read_text("utf-8"))
            report = judge(compose_from(args), real_host(), args.out, existing)
        else:
            report = run_long(args, real_host())
    except ToolError as refused:
        print(f"long_run: {refused}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    failed = (
        report["host_pauses"]
        or report["false_positives"]
        or report["unplanned_restarts"]
        or report["not_running_at_end"]
        or report["queue_after_settling"]
        or report["verdict"]["exit"]  # type: ignore[index]
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
