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

**For Stage 24's acceptance the run is sealed, and survives its own tool**
(D-257):
- **resources** — each sample also reads container health, the database's and
  Prometheus's size, free disk, and the host's memory, temperature and
  throttling. Memory that climbs, disk that runs short, or a host that
  throttled fails the run (:mod:`long_run_watch`);
- **alerts as a positive control** — a platform fault that outlasts an alert's
  ``for:`` owes that alert a firing;
- **data loss** — the simulator's own faults are stopped when the run ends, so
  the settle window measures the queue with nothing broken;
- **resume** — ``run.json`` records the start, the settings and what the run ran
  on, so ``--resume`` carries a run on after its tool stopped
  (:mod:`long_run_state`);
- **seal** — the judgement is published as a fault run with this run's record
  inside it as ``long_run.json``, which ``meridian report build --faults``
  reads to say whether the seventy-two hours were run and passed.

    python deploy/tools/long_run.py --hours 72 --seed 4471 --out runs/long-72h \\
        --up --stations 10

Pure where it decides anything, so ``tests/unit/test_long_run.py`` pins the
schedule, the parsing and the false-positive rule without Docker.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.parse
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from chaos import FAULTS, Effects, Planned, append, inject, plan
from compose_db import Compose, ToolError, add_compose_arguments, compose_from
from long_run_preflight import preflight
from long_run_state import (
    PLATFORM_LEDGER,
    REPORT_FILE,
    RunState,
    environment,
    left_open,
    read_state,
    refuse_taken,
    restart_close,
    write_state,
)
from long_run_watch import (
    DISK_FREE_FLOOR_GIB,
    MEMORY_SLOPE_MIB_PER_HOUR,
    Machine,
    Sizes,
    alert_waits,
    grace_for,
    judge_resources,
    missed_alerts,
    parse_bytes,
    parse_kib,
    read_machine,
)

REPORT_FORMAT = "meridian-long-run/2"
RECORD_FILE = "long_run.json"
"""The run's own record, sealed inside its fault run (D-257)."""

RULES = Path(__file__).resolve().parents[1] / "prometheus" / "rules" / "meridian.yml"
"""The alert rules, for each alert's ``for:``."""

HISTORY_CHUNK = timedelta(hours=12)
"""Alert history is read twelve hours at a time, as D-192's checker reads it:
seventy-two hours at a thirty-second step is near Prometheus's point limit."""
RUN_ID = "long-run"
STATION_LEDGER = "/var/lib/meridian-sim/faults.jsonl"
"""Where the simulator writes its ledger, inside its state volume."""

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
    health: str = "none"
    """Its healthcheck's answer, ``none`` for a service without one."""

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
    machine: Machine | None = None
    sizes: Sizes = field(default_factory=Sizes)


@dataclass(frozen=True, slots=True)
class Interval:
    """A stretch of time, both ends as instants; ``end`` ``None`` if still open."""

    name: str
    start: datetime
    end: datetime | None
    series: str = field(default="", compare=False)
    """Which series of ``name`` it is — an alert's labels — so two stations'
    ``StationOffline`` are never joined into one stretch."""


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
    """``docker inspect``: ``service restarts state exit-code [health]`` per line.

    The service is compose's own label on the container, not a piece of its
    name: a project name may contain hyphens, and so may a service's.
    """
    found: list[Container] = []
    for line in lines:
        parts = line.split()
        if len(parts) not in {4, 5} or not parts[1].isdigit():
            continue
        service, restarts, state, code = parts[:4]
        if not code.lstrip("-").isdigit():
            continue
        health = parts[4] if len(parts) == 5 else "none"
        found.append(Container(service, state, int(restarts), int(code), health))
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
        labels = json.dumps(series["metric"], sort_keys=True)
        stamps = [float(at) for at, value in series.get("values", []) if value == "1"]
        start = previous = None
        for stamp in stamps:
            if start is None:
                start = previous = stamp
            elif previous is not None and stamp - previous > step_s * 1.5:
                stretches.append(_interval(name, start, previous, labels))
                start = stamp
            previous = stamp
        if start is not None and previous is not None:
            stretches.append(_interval(name, start, previous, labels))
    return sorted(stretches, key=lambda one: (one.start, one.name))


def _interval(name: str, start: float, end: float, series: str = "") -> Interval:
    return Interval(
        name,
        datetime.fromtimestamp(start, UTC),
        datetime.fromtimestamp(end, UTC),
        series,
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
    waits: dict[str, timedelta] | None = None,
) -> list[Interval]:
    """Alerts that fired with no fault that could cause them open, nor closed
    within their grace. A host that slept explains any alert; it fails the run
    on its own account.

    The grace is ten minutes, or longer for an alert whose ``for:`` is: an alert
    that waits thirty minutes can first fire nearly that long after its cause.
    """
    unexplained: list[Interval] = []
    for alert in alerts:
        alert_end = alert.end or run_end
        grace = grace_for(alert.name, waits or {})
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
        try:
            result = subprocess.run(
                command, input=stdin, text=True, capture_output=True, check=False
            )
        except FileNotFoundError:
            # As a shell says it: a command this host lacks is an answer, not a
            # crash — `vcgencmd` exists only on a Pi, `timedatectl` only with systemd.
            return 127, f"{command[0]}: command not found"
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


@dataclass(frozen=True, slots=True)
class Probe:
    """What a sample can read on this host besides Docker, found once."""

    disk_root: Path | None = None
    """Docker's data root, whose free space is the stack's disk."""
    vcgencmd: bool = False
    """Whether this is a Raspberry Pi that can say if it throttled."""


def probe_for(host: Host) -> Probe:
    """Find Docker's data root, and whether ``vcgencmd`` is here."""
    status, root = host.run(["docker", "info", "--format", "{{.DockerRootDir}}"], None)
    return Probe(
        disk_root=Path(root.strip()) if status == 0 and root.strip() else None,
        vcgencmd=shutil.which("vcgencmd") is not None,
    )


SIZE_SQL = "select pg_database_size(current_database());\n"


def sample(compose: Compose, host: Host, probe: Probe | None = None) -> Sample:
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
                " {{.RestartCount}} {{.State.Status}} {{.State.ExitCode}}"
                " {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
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
    _measure(compose, host, probe or Probe(), taken)
    return taken


def _measure(compose: Compose, host: Host, probe: Probe, taken: Sample) -> None:
    """The stack's sizes on disk and the machine's state, into ``taken``."""
    status, size = host.run(compose.psql(), SIZE_SQL)
    database = parse_bytes(size) if status == 0 else None
    status, used = host.run(
        compose.command("exec", "-T", "prometheus", "du", "-sk", "/prometheus"), None
    )
    taken.sizes = Sizes(database, parse_kib(used) if status == 0 else None)
    throttled = None
    if probe.vcgencmd:
        status, answer = host.run(["vcgencmd", "get_throttled"], None)
        throttled = answer if status == 0 else None
    taken.machine = read_machine(probe.disk_root, throttled)


def _in_prometheus(compose: Compose, path: str) -> list[str]:
    """A GET against the run's Prometheus, from inside its container."""
    return compose.command(
        "exec", "-T", "prometheus", "wget", "-qO-", f"http://localhost:9090{path}"
    )


def alert_history(
    compose: Compose, host: Host, start: datetime, end: datetime, step_s: float = 30
) -> list[Interval]:
    """Every stretch any alert fired between ``start`` and ``end``.

    Read in twelve-hour pieces and joined again, so a stretch crossing a piece's
    edge is one stretch.
    """
    found: list[Interval] = []
    at = start
    while at < end:
        upto = min(at + HISTORY_CHUNK, end)
        query = urllib.parse.urlencode(
            {
                "query": 'ALERTS{alertstate="firing"}',
                "start": f"{at.timestamp():.0f}",
                "end": f"{upto.timestamp():.0f}",
                "step": f"{step_s:.0f}",
            }
        )
        status, body = host.run(
            _in_prometheus(compose, f"/api/v1/query_range?{query}"), None
        )
        if status != 0:
            raise ToolError(f"the alert history could not be read: {body.strip()}")
        found.extend(parse_alert_history(body, step_s))
        at = upto
    return join_stretches(found, step_s)


def join_stretches(stretches: Sequence[Interval], step_s: float) -> list[Interval]:
    """One stretch per alert series where two pieces of history meet within a step.

    Joined only within one series: two stations' ``StationOffline`` that
    overlap are two firings, and one joined into the other would hide it.
    """
    joined: list[Interval] = []
    for one in sorted(stretches, key=lambda item: (item.name, item.series, item.start)):
        last = joined[-1] if joined else None
        if (
            last is not None
            and last.name == one.name
            and last.series == one.series
            and last.end is not None
            and (one.start - last.end).total_seconds() <= step_s * 1.5
        ):
            joined[-1] = Interval(
                one.name, last.start, max(last.end, one.end or last.end), one.series
            )
        else:
            joined.append(one)
    return sorted(joined, key=lambda item: (item.start, item.name))


@dataclass(frozen=True, slots=True)
class Judging:
    """Where the judgement seals the run: a datasets root on this host."""

    datasets: Path


def run_long(args: argparse.Namespace, host: Host) -> dict[str, object]:
    """The whole run: faults and samples on their timeline, then the judgement."""
    compose = compose_from(args)
    out: Path = args.out
    state = _resume(compose, host, out) if args.resume else _begin(args, compose, host)
    platform_ledger = out / PLATFORM_LEDGER
    faults = plan(state.seed, state.hours, state.fault_gap_minutes * 60)
    started = datetime.fromisoformat(state.started)
    resumed_at_s = (host.now() - started).total_seconds() if args.resume else 0.0
    samples = _read_samples(out)
    waiter = Waiter(host)
    probe = probe_for(host)
    effects = Effects(
        run=lambda command: host.run(command, None)[0],
        sleep=waiter.sleep,
        now=host.now,
    )
    for at_s, kind, fault in timeline(
        faults, state.hours, state.sample_every_minutes * 60
    ):
        if at_s < resumed_at_s:
            # What fell while the tool was stopped did not happen; it is not
            # done late, which would crowd it against what comes next.
            continue
        waiter.until(started + timedelta(seconds=at_s))
        if kind == "fault" and fault is not None:
            inject(
                compose, fault.kind, platform_ledger, RUN_ID, fault.duration_s, effects
            )
        else:
            samples.append(sample(compose, host, probe))
            _write_samples(out, samples)

    ended = host.now()
    stopped_faults = _stop_station_faults(compose, host, state)
    waiter.sleep(state.settle_minutes * 60)
    settled = sample(compose, host, probe)
    samples.append(settled)
    _write_samples(out, samples)

    # The fleet is stopped before its ledger is read. A fault still open when
    # the ledger was copied would otherwise be judged against heartbeats sent
    # after the copy, and read as a fault that did not hold.
    _step(host, compose.command("stop", "simulator"))
    report = _report(state, started, ended, samples, settled)
    report["fleet_stopped"] = host.now().isoformat()
    report["station_faults_stopped_at_end"] = stopped_faults
    planned = len([one for one in faults if one.at_s < state.hours * 3600])
    report["faults_injected"] = {
        "platform": planned,
        "skipped_while_interrupted": max(0, planned - opened(platform_ledger)),
    }
    # Kept before judging: a judgement that fails can be had again with
    # --judge-only, from this, without running the hours again.
    (out / REPORT_FILE).write_text(json.dumps(report, indent=2) + "\n", "utf-8")
    return judge(compose, host, out, report, waiter.pauses, _judging(args))


def _begin(args: argparse.Namespace, compose: Compose, host: Host) -> RunState:
    """Refuse a run that is not fresh, bring the stack up, and record the start."""
    refused = refuse_taken(args.out) or unwritable(args.datasets)
    if refused:
        raise ToolError(refused)
    if args.up:
        # Before `up`, which starts the simulator writing: a ledger already
        # there is an earlier run's, and would be judged as part of this one.
        status, held = host.run(_ledger_command(compose), None)
        if status == 0 and held.strip():
            raise ToolError(
                "this compose project's simulator volume already holds a fault"
                " ledger: give a new --project-name, or remove its volumes with"
                " `docker compose down -v`"
            )
        _step(host, compose.command(*PROFILES, "up", "-d"))
    status, api = host.run(compose.command("ps", "-q", "api"), None)
    state = RunState(
        started=host.now().isoformat(),
        seed=args.seed,
        hours=args.hours,
        stations=args.stations,
        fault_gap_minutes=args.fault_gap_minutes,
        sample_every_minutes=args.sample_every_minutes,
        settle_minutes=args.settle_minutes,
        memory_slope_mib_per_hour=args.memory_slope_bound,
        disk_free_gib=args.disk_floor_gib,
        up=args.up,
        environment=environment(
            lambda command: host.run(command, None),
            api.strip() or None if status == 0 else None,
        ),
    )
    write_state(args.out, state)
    return state


def unwritable(datasets: Path) -> str | None:
    """Why the run could not be sealed under ``datasets``, or None if it can.

    Made here, before `up`, and checked: a bind mount whose source is missing
    is created by Docker as root, and the seal, which runs as this user,
    would then be refused at the end of three days. Stage 24's rehearsal
    found it so.
    """
    try:
        datasets.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"the datasets root {datasets} cannot be made: {exc}"
    if not os.access(datasets, os.W_OK):
        return (
            f"the datasets root {datasets} is not writable by this user, so the run"
            " could not be sealed: Docker makes a missing bind mount as root."
            " Make it as yourself before the stack first starts"
        )
    return None


def _resume(compose: Compose, host: Host, out: Path) -> RunState:
    """Carry a run on: mend what its tool left broken, and record the gap."""
    try:
        state = read_state(out)
    except FileNotFoundError as missing:
        raise ToolError(f"{out} holds no run.json, so no run to resume") from missing
    ledger = out / PLATFORM_LEDGER
    text = ledger.read_text("utf-8") if ledger.exists() else ""
    for opened in left_open(text):
        mends = FAULTS[str(opened["kind"])].mends
        if mends:
            # Not _step: the break may never have taken, or a reboot undid it,
            # and `unpause` of a running container fails. Refusing then would
            # leave the run with no way back in.
            status, output = host.run(compose.command(*mends), None)
            if status != 0:
                print(
                    f"long_run: {' '.join(mends)} did not apply on resume:"
                    f" {output.strip()[-200:]}",
                    file=sys.stderr,
                )
        append(ledger, restart_close(opened, host.now()))
    taken = _read_samples(out)
    acted = [taken[-1].at if taken else state.started, *_instants(text)]
    since = max(acted, key=datetime.fromisoformat)
    state.interruptions.append({"from": since, "to": host.now().isoformat()})
    write_state(out, state)
    return state


def _stop_station_faults(compose: Compose, host: Host, state: RunState) -> bool:
    """Restart the fleet under ``clean``, so nothing is broken while it settles.

    A restarted simulator closes the faults it held as ``ended: restart`` and
    opens none (D-189), so the queue measured after settling is what the
    platform failed to take, not what a fault was still holding back. Only a
    stack the tool brought up is restarted: it alone knows how that fleet was
    configured.
    """
    if not state.up:
        return False
    settings = {
        "SIMULATOR_SCENARIO": "clean",
        "SIMULATOR_STATION_COUNT": str(state.stations),
        "SIMULATOR_SEED": str(state.seed),
    }
    command = compose.command("up", "-d", "--no-deps", "--force-recreate", "simulator")
    _step(
        host, ["env", *(f"{key}={value}" for key, value in settings.items()), *command]
    )
    return True


def judge(
    compose: Compose,
    host: Host,
    out: Path,
    report: dict[str, object],
    pauses: Sequence[Interval] = (),
    judging: Judging | None = None,
) -> dict[str, object]:
    """Judge a finished run from its ledgers, its stack and its Prometheus.

    Also what ``--judge-only`` runs, against a stack a run left standing, so a
    judgement lost to a defect in this tool need not cost the run again.

    The run is judged twice over, and sealed once: first its own record — the
    host, the containers, the alerts, the queue, the resources — written as
    ``long_run.json``; then every fault, by ``meridian reliability faults
    --publish``, which seals the ledger, the evidence and the verdicts with
    that record beside them.
    """
    started = datetime.fromisoformat(str(report["started"]))
    ended = datetime.fromisoformat(str(report["ended"]))
    ledger = _merged_ledger(compose, host, out, out / PLATFORM_LEDGER)

    # Only up to the fleet being stopped: the alerts that stopping it raises —
    # heartbeats ceasing — are the judgement's doing, not the run's.
    alerts = alert_history(compose, host, started, _fleet_stopped(report, out, host))
    windows = ledger_windows(ledger.splitlines())
    waits = alert_waits(RULES.read_text("utf-8")) if RULES.exists() else {}
    # An alert raised by the host having slept is the host's, not a false one;
    # the pause itself fails the run.
    unexplained = false_positives(alerts, [*windows, *pauses], ended, waits)
    # A window --resume closed spans the tool's gap, not the fault: it owes no
    # alert, since how long the service stayed broken is not known.
    restarted = restart_closes(ledger.splitlines())
    platform = [
        one
        for one in windows
        if " on platform:" in one.name and (one.name, one.end) not in restarted
    ]
    report["alerts_fired"] = len(alerts)
    report["alerts_by_name"] = _count(one.name for one in alerts)
    report["false_positives"] = [
        {"alert": one.name, "start": one.start.isoformat()} for one in unexplained
    ]
    report["missed_alerts"] = missed_alerts(platform, alerts, waits, CAUSES)
    if pauses or "host_pauses" not in report:
        report["host_pauses"] = [
            {"start": one.start.isoformat(), "end": (one.end or ended).isoformat()}
            for one in pauses
        ]
    injected = dict(report.get("faults_injected") or {})  # type: ignore[call-overload]
    injected["windows_in_ledger"] = len(windows)
    report["faults_injected"] = injected
    report["failures"] = failures(report)
    report["passed"] = not report["failures"]
    _seal(compose, host, out, report, judging or Judging(Path("data/datasets")))
    _keep_logs(compose, host, out)
    (out / REPORT_FILE).write_text(json.dumps(report, indent=2) + "\n", "utf-8")
    return report


def failures(report: dict[str, object]) -> list[str]:
    """Every reason the run's own record fails it, in words; none if it passed."""
    said = {
        "host_pauses": "the host slept {n} time(s)",
        "false_positives": "{n} alert(s) fired with no fault to cause them",
        "missed_alerts": "{n} platform alert(s) owed by a fault never fired",
        "unplanned_restarts": "{n} container(s) restarted on their own",
        "not_running_at_end": "{n} service(s) not running after settling",
        "unhealthy_at_end": "{n} service(s) unhealthy after settling",
    }
    found = [
        text.format(n=len(report[key]))  # type: ignore[arg-type]
        for key, text in said.items()
        if report.get(key)
    ]
    if report.get("queue_after_settling"):
        found.append(
            f"{report['queue_after_settling']} observation(s) still queued after"
            " settling"
        )
    resources = report.get("resources")
    if isinstance(resources, dict):
        found.extend(str(one) for one in resources.get("failures", []))
    return found


def _seal(
    compose: Compose,
    host: Host,
    out: Path,
    report: dict[str, object],
    judging: Judging,
) -> None:
    """Judge every fault and seal the run as a fault run, with its record inside.

    Run in a one-off API container on the stack's network, as the runbook
    exports a snapshot: the API's own container cannot write (D-206).
    """
    # What the seal itself adds is left out: on --judge-only the report holds a
    # previous seal's, which would make the same run seal to a different hash.
    record = {
        key: value
        for key, value in report.items()
        if key not in {"verdict", "fault_run"}
    }
    (out / RECORD_FILE).write_text(json.dumps(record, indent=2) + "\n", "utf-8")
    datasets = judging.datasets.resolve()
    datasets.mkdir(parents=True, exist_ok=True)
    status, verdict = host.run(
        compose.command(
            "run",
            "--rm",
            "--no-deps",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "-v",
            f"{datasets}:/datasets",
            "-v",
            f"{out.resolve()}:/long-run:ro",
            "-e",
            "MERIDIAN_DATASETS_ROOT=/datasets",
            "api",
            "meridian",
            "reliability",
            "faults",
            "--ledger",
            "/long-run/faults.jsonl",
            "--prometheus",
            "http://prometheus:9090",
            "--publish",
            "--run-record",
            f"/long-run/{RECORD_FILE}",
        ),
        None,
    )
    (out / "verdict.txt").write_text(verdict, encoding="utf-8")
    sealed = sealed_run(verdict)
    report["verdict"] = {"exit": status, "summary": _last_line(verdict)}
    report["fault_run"] = (
        None
        if sealed is None
        else {"sha256": sealed[1], "path": str(datasets / "faults" / sealed[0])}
    )


def sealed_run(verdict: str) -> tuple[str, str] | None:
    """The fault run's directory name and hash, as ``--publish`` printed them."""
    path = re.search(r"^fault run: \S*/([0-9a-f]{12}) ", verdict, re.M)
    digest = re.search(r"^\s+hash\s+([0-9a-f]{64})\s*$", verdict, re.M)
    return (path.group(1), digest.group(1)) if path and digest else None


def _keep_logs(compose: Compose, host: Host, out: Path) -> None:
    """Every service's log, kept beside the record: compose rotates them away."""
    status, logs = host.run(compose.command("logs", "--no-color", "--timestamps"), None)
    (out / "logs").mkdir(exist_ok=True)
    (out / "logs" / "compose.log").write_text(
        logs if status == 0 else f"compose logs failed: {logs[-400:]}\n", "utf-8"
    )


def _report(
    state: RunState,
    started: datetime,
    ended: datetime,
    samples: Sequence[Sample],
    settled: Sample,
) -> dict[str, object]:
    restarts = {
        one.service: one.restarts for one in (samples[-1].containers if samples else [])
    }
    queues = [one.queued for one in samples if one.queued is not None]
    memory: dict[str, list[tuple[datetime, float]]] = {}
    for taken in samples:
        at = datetime.fromisoformat(taken.at)
        for use in taken.usage:
            memory.setdefault(use.name, []).append((at, use.memory_mib))
    resources = judge_resources(
        memory,
        [one.machine for one in samples if one.machine is not None],
        [one.sizes for one in samples],
        (state.memory_slope_mib_per_hour, state.disk_free_gib),
    )
    return {
        "format": REPORT_FORMAT,
        "simulated": True,
        "seed": state.seed,
        "hours": state.hours,
        "stations": state.stations,
        "fault_gap_minutes": state.fault_gap_minutes,
        "sample_every_minutes": state.sample_every_minutes,
        "settle_minutes": state.settle_minutes,
        "environment": state.environment,
        "interruptions": state.interruptions,
        "started": started.isoformat(),
        "ended": ended.isoformat(),
        "samples": len(samples),
        "unplanned_restarts": {name: n for name, n in restarts.items() if n},
        "not_running_at_end": [
            one.service for one in settled.containers if not one.healthy
        ],
        "unhealthy_at_end": [
            one.service for one in settled.containers if one.health == "unhealthy"
        ],
        "queue_max": max(queues, default=None),
        "queue_after_settling": settled.queued,
        "resources": {"figures": resources.figures, "failures": resources.failures},
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
    status, stations = host.run(_ledger_command(compose), None)
    if status != 0:
        raise ToolError(f"the simulator's ledger could not be read: {stations[-400:]}")
    platform = platform_ledger.read_text("utf-8") if platform_ledger.exists() else ""
    merged = stations + platform
    (out / "faults.jsonl").write_text(merged, encoding="utf-8")
    return merged


def opened(ledger: Path) -> int:
    """How many platform faults the run opened, across every resume."""
    if not ledger.exists():
        return 0
    rows = [json.loads(one) for one in ledger.read_text("utf-8").splitlines() if one]
    return sum(one["event"] == "open" for one in rows)


def restart_closes(lines: Iterable[str]) -> set[tuple[str, datetime]]:
    """Each window closed as ``ended: restart``, by its name and closing instant."""
    found: set[tuple[str, datetime]] = set()
    for line in lines:
        if not line.strip():
            continue
        row = json.loads(line)
        detail = row.get("detail") or {}
        if row["event"] == "close" and detail.get("ended") == "restart":
            at = datetime.fromisoformat(row["at"].replace("Z", "+00:00"))
            found.add((f"{row['kind']} on {row['target']}", at))
    return found


def _instants(ledger: str) -> list[str]:
    """Every instant a ledger records, as ISO text a datetime reads."""
    return [
        str(json.loads(one)["at"]).replace("Z", "+00:00")
        for one in ledger.splitlines()
        if one.strip()
    ]


def _ledger_command(compose: Compose) -> list[str]:
    """Print the simulator's ledger from its volume, whether or not it runs."""
    return compose.command(
        "run", "--rm", "--no-deps", "--entrypoint", "cat", "simulator", STATION_LEDGER
    )


def _read_samples(out: Path) -> list[Sample]:
    """The samples a run already took, from ``samples.json``; none if it has none."""
    path = out / "samples.json"
    if not path.exists():
        return []
    return [
        Sample(
            at=one["at"],
            containers=[Container(**held) for held in one.get("containers", [])],
            firing=list(one.get("firing", [])),
            queued=one.get("queued"),
            usage=[Usage(**held) for held in one.get("usage", [])],
            machine=Machine(**one["machine"]) if one.get("machine") else None,
            sizes=Sizes(**one.get("sizes", {})),
        )
        for one in json.loads(path.read_text("utf-8"))
    ]


def _judging(args: argparse.Namespace) -> Judging:
    return Judging(datasets=args.datasets)


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


def _arguments(argv: list[str] | None) -> argparse.Namespace:
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
    parser.add_argument("--sample-every-minutes", type=float, default=15.0)
    parser.add_argument(
        "--fault-gap-minutes",
        type=float,
        default=60.0,
        help="mean time between platform faults; shorter for a rehearsal",
    )
    parser.add_argument("--settle-minutes", type=float, default=10.0)
    parser.add_argument(
        "--memory-slope-bound",
        type=float,
        default=MEMORY_SLOPE_MIB_PER_HOUR,
        metavar="MIB_PER_HOUR",
        help="fail a container whose memory grows faster over the second half",
    )
    parser.add_argument(
        "--disk-floor-gib",
        type=float,
        default=DISK_FREE_FLOOR_GIB,
        help="fail the run if Docker's disk ever has less free",
    )
    parser.add_argument(
        "--datasets",
        type=Path,
        default=Path("data/datasets"),
        help="the datasets root the run is sealed under, on this host",
    )
    again = parser.add_mutually_exclusive_group()
    again.add_argument(
        "--resume",
        action="store_true",
        help="carry on a run in --out whose tool stopped, from its run.json",
    )
    again.add_argument(
        "--preflight",
        action="store_true",
        help="ask whether this host is ready for the run, and start nothing",
    )
    again.add_argument(
        "--judge-only",
        action="store_true",
        help="judge a finished run again from --out and the stack it left up",
    )
    add_compose_arguments(parser)
    return parser.parse_args(argv)


def _judge_again(args: argparse.Namespace) -> dict[str, object]:
    """``--judge-only``: the judgement again, from the report the run kept."""
    path = args.out / REPORT_FILE
    if not path.exists():
        raise ToolError(f"{path} does not exist: the run never reached its end")
    report = json.loads(path.read_text("utf-8"))
    return judge(
        compose_from(args), real_host(), args.out, report, judging=_judging(args)
    )


def _preflight(args: argparse.Namespace, host: Host) -> int:
    """``--preflight``: each question, its answer, and 1 if any failed."""
    compose = compose_from(args)
    status, config = host.run(compose.command("config", "--format", "json"), None)
    image_ref = None
    if status == 0:
        services = json.loads(config).get("services", {})
        image_ref = services.get("api", {}).get("image")
    checks = preflight(
        lambda command: host.run(command, None),
        args.out,
        image_ref,
        args.disk_floor_gib,
        Path(args.compose_file).parent / "prometheus" / "metrics_token",
    )
    for one in checks:
        print(f"{one.status:<5} {one.name:<14} {one.detail}")
    return 1 if any(one.status == "fail" for one in checks) else 0


def _stopped(signum: int, _frame: object) -> None:
    """Leave by an exception, so a fault being injected is mended on the way."""
    raise SystemExit(128 + signum)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    if args.preflight:
        return _preflight(args, real_host())
    if args.up:
        os.environ.setdefault("SIMULATOR_SCENARIO", "chaos")
        os.environ.setdefault("SIMULATOR_STATION_COUNT", str(args.stations))
        os.environ.setdefault("SIMULATOR_SEED", str(args.seed))
    # A lost SSH session sends SIGHUP and `systemctl stop` sends SIGTERM; both
    # would otherwise end the tool with a paused API or a stopped scheduler.
    for one in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(one, _stopped)

    try:
        report = _judge_again(args) if args.judge_only else run_long(args, real_host())
    except ToolError as refused:
        print(f"long_run: {refused}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    passed = report["passed"] and report["verdict"]["exit"] == 0  # type: ignore[index]
    return 0 if passed and report.get("fault_run") else 1


if __name__ == "__main__":
    sys.exit(main())
