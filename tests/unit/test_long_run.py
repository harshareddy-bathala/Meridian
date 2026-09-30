"""``deploy/tools/long_run.py`` — a long run's schedule and judgement, without Docker.

The tool drives a compose stack for hours; what it decides is pinned here: when
each fault and sample happens, how the Docker and Prometheus answers are read,
and which alerts count as false positives.

Marked as a unit test by living in ``tests/unit``: no network, no Docker.

Reference: docs/DECISIONS.md D-194, D-198.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "deploy/tools"
T0 = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def long_run() -> Iterator[ModuleType]:
    """The tool, imported as running it does: its directory on the path."""
    sys.path.insert(0, str(TOOLS))
    try:
        path = TOOLS / "long_run.py"
        spec = importlib.util.spec_from_file_location("long_run", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["long_run"] = module
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(TOOLS))
        for name in ("long_run", "chaos", "compose_db"):
            sys.modules.pop(name, None)


def test_samples_bracket_the_run_and_faults_come_first(long_run: ModuleType) -> None:
    """A sample at the start and the end, and a fault before a sample at its time."""
    planned = sys.modules["chaos"].Planned
    faults = [planned(600.0, "api_paused", 20.0), planned(9000.0, "db", 0.0)]

    events = long_run.timeline(faults, hours=0.5, sample_every_s=600)

    assert [(at, kind) for at, kind, _ in events] == [
        (0.0, "sample"),
        (600.0, "fault"),
        (600.0, "sample"),
        (1200.0, "sample"),
        (1800.0, "sample"),
    ]


def test_each_container_is_read_by_its_service(long_run: ModuleType) -> None:
    """Compose's label, not the name, whose hyphens belong to project and service."""
    found = long_run.parse_containers(
        ["api 0 running 0", "sim-seed 2 exited 0", "a line docker never writes"]
    )

    assert [(one.service, one.restarts, one.state) for one in found] == [
        ("api", 0, "running"),
        ("sim-seed", 2, "exited"),
    ]


@pytest.mark.parametrize(
    ("line", "healthy"),
    [
        ("api 0 running 0", True),
        ("api 0 exited 0", False),
        ("migrate 0 exited 0", True),
        ("sim-seed 0 exited 1", False),
    ],
)
def test_only_a_one_shot_may_have_exited(
    long_run: ModuleType, line: str, healthy: bool
) -> None:
    """A service that died and stayed down is not calm; a finished seed is."""
    (container,) = long_run.parse_containers([line])

    assert container.healthy is healthy


def test_docker_stats_are_read_as_cpu_and_mebibytes(long_run: ModuleType) -> None:
    """Whatever unit ``docker stats`` chose, memory is compared in MiB."""
    lines = [
        json.dumps({"Name": "a", "CPUPerc": "3.5%", "MemUsage": "1.5GiB / 7GiB"}),
        json.dumps({"Name": "b", "CPUPerc": "0.0%", "MemUsage": "512KiB / 7GiB"}),
    ]

    usage = long_run.parse_usage(lines)

    assert [(one.name, one.cpu_percent, one.memory_mib) for one in usage] == [
        ("a", 3.5, 1536.0),
        ("b", 0.0, 0.5),
    ]


def test_only_firing_alerts_are_named(long_run: ModuleType) -> None:
    """A pending alert has not fired yet."""
    body = json.dumps(
        {
            "data": {
                "alerts": [
                    {"labels": {"alertname": "StationOffline"}, "state": "firing"},
                    {"labels": {"alertname": "StationStale"}, "state": "pending"},
                ]
            }
        }
    )

    assert long_run.parse_firing(body) == ["StationOffline"]


def test_an_alert_history_splits_at_its_gaps(long_run: ModuleType) -> None:
    """Consecutive samples are one stretch; a gap of more than a step is two."""
    start = T0.timestamp()
    stamps = [start, start + 30, start + 60, start + 600, start + 630]
    body = json.dumps(
        {
            "data": {
                "result": [
                    {
                        "metric": {"alertname": "StationOffline"},
                        "values": [[stamp, "1"] for stamp in stamps],
                    }
                ]
            }
        }
    )

    stretches = long_run.parse_alert_history(body, step_s=30)

    assert [(one.start - T0, one.end - T0) for one in stretches] == [
        (timedelta(0), timedelta(seconds=60)),
        (timedelta(seconds=600), timedelta(seconds=630)),
    ]


def test_ledger_windows_pair_opens_with_closes(long_run: ModuleType) -> None:
    """Station and platform faults alike, open-ended when never closed."""

    def line(event: str, kind: str, target: str, seconds: int) -> str:
        at = (T0 + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")
        return json.dumps(
            {"ledger": 1, "event": event, "kind": kind, "target": target, "at": at}
        )

    windows = long_run.ledger_windows(
        [
            line("open", "network_down", "station:1", 0),
            line("open", "database_restart", "platform:db", 30),
            line("close", "network_down", "station:1", 120),
        ]
    )

    assert [(one.name, one.end) for one in windows] == [
        ("network_down on station:1", T0 + timedelta(seconds=120)),
        ("database_restart on platform:db", None),
    ]


def test_an_alert_with_no_fault_near_it_is_a_false_positive(
    long_run: ModuleType,
) -> None:
    """Explained while a fault is open, and within the grace after it closes."""
    interval = long_run.Interval
    faults = [interval("network_down", T0, T0 + timedelta(minutes=3))]
    during = interval("StationOffline", T0 + timedelta(minutes=2), None)
    just_after = interval(
        "StationOffline",
        T0 + timedelta(minutes=9),
        T0 + timedelta(minutes=10),
    )
    long_after = interval(
        "DatabaseUnavailable",
        T0 + timedelta(hours=2),
        T0 + timedelta(hours=2, minutes=1),
    )

    unexplained = long_run.false_positives(
        [during, just_after, long_after], faults, run_end=T0 + timedelta(hours=3)
    )

    assert unexplained == [long_after]


class _Laptop:
    """A host whose clock moves with each sleep, and once jumps: a suspend."""

    def __init__(self, suspend_after: int, suspended: timedelta) -> None:
        self.clock = T0
        self.sleeps = 0
        self.suspend_after = suspend_after
        self.suspended = suspended

    def sleep(self, seconds: float) -> None:
        self.sleeps += 1
        self.clock += timedelta(seconds=seconds)
        if self.sleeps == self.suspend_after:
            self.clock += self.suspended

    def now(self) -> datetime:
        return self.clock


def waiter_for(long_run: ModuleType, laptop: _Laptop) -> object:
    host = long_run.Host(run=lambda _c, _s: (0, ""), sleep=laptop.sleep, now=laptop.now)
    return long_run.Waiter(host)


def test_a_wait_reaches_its_instant_in_short_steps(long_run: ModuleType) -> None:
    """Five minutes is ten sleeps of thirty seconds, not one of three hundred."""
    laptop = _Laptop(suspend_after=0, suspended=timedelta(0))
    waiter = waiter_for(long_run, laptop)

    waiter.until(T0 + timedelta(minutes=5))

    assert laptop.clock == T0 + timedelta(minutes=5)
    assert laptop.sleeps == 10
    assert waiter.pauses == []


def test_a_suspended_host_is_recorded_and_the_wait_ends_on_waking(
    long_run: ModuleType,
) -> None:
    """The run on a laptop that slept is not unattended: D-198 fails it."""
    laptop = _Laptop(suspend_after=2, suspended=timedelta(hours=1))
    waiter = waiter_for(long_run, laptop)

    waiter.until(T0 + timedelta(minutes=5))

    (pause,) = waiter.pauses
    assert pause.start == T0 + timedelta(minutes=1)
    assert pause.end == T0 + timedelta(hours=1, minutes=1)
    assert laptop.clock >= T0 + timedelta(minutes=5)
    assert laptop.sleeps == 2, "a wait past its instant after waking ends at once"


def test_an_unreadable_station_ledger_stops_the_judgement(
    long_run: ModuleType, tmp_path: Path
) -> None:
    """Judged on the platform's faults alone, every station alert would be false."""
    host = long_run.Host(
        run=lambda _command, _stdin: (1, "no such container"),
        sleep=lambda _s: None,
        now=lambda: T0,
    )
    compose = sys.modules["compose_db"].Compose("deploy/docker-compose.yml")

    with pytest.raises(sys.modules["compose_db"].ToolError, match="ledger"):
        long_run._merged_ledger(compose, host, tmp_path, tmp_path / "none.jsonl")


def test_the_judgement_reads_alerts_only_until_the_fleet_stopped(
    long_run: ModuleType, tmp_path: Path
) -> None:
    """Recorded when there is one, else the last sample, which comes just before."""
    host = long_run.Host(
        run=lambda _c, _s: (0, ""), sleep=lambda _s: None, now=lambda: T0
    )
    (tmp_path / "samples.json").write_text(
        json.dumps([{"at": (T0 - timedelta(hours=1)).isoformat()}]), "utf-8"
    )

    recorded = {"fleet_stopped": (T0 - timedelta(minutes=5)).isoformat()}

    assert long_run._fleet_stopped(recorded, tmp_path, host) == T0 - timedelta(
        minutes=5
    )
    assert long_run._fleet_stopped({}, tmp_path, host) == T0 - timedelta(hours=1)
