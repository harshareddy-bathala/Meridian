"""What a long run watches, how it judges what it saw, and how it resumes.

``deploy/tools/long_run.py`` and its two modules, ``long_run_watch`` and
``long_run_state``, without Docker: every rule that fails a run, each with the
case that passes it, and the resume that mends what a stopped tool left broken.

Marked as a unit test by living in ``tests/unit``: no network, no Docker.

Reference: docs/DECISIONS.md D-198, D-257.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "deploy/tools"
T0 = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)
MODULES = ("long_run", "long_run_watch", "long_run_state", "chaos", "compose_db")


@pytest.fixture(scope="module")
def tools() -> Iterator[SimpleNamespace]:
    """The tool and its modules, imported as running it does."""
    sys.path.insert(0, str(TOOLS))
    try:
        spec = importlib.util.spec_from_file_location("long_run", TOOLS / "long_run.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["long_run"] = module
        spec.loader.exec_module(module)
        yield SimpleNamespace(
            run=module,
            watch=sys.modules["long_run_watch"],
            state=sys.modules["long_run_state"],
            compose=sys.modules["compose_db"],
        )
    finally:
        sys.path.remove(str(TOOLS))
        for name in MODULES:
            sys.modules.pop(name, None)


# --- reading the host ------------------------------------------------------------


def test_the_host_is_read_in_mebibytes_and_degrees(tools: SimpleNamespace) -> None:
    meminfo = (
        "MemTotal: 8000000 kB\nMemAvailable: 2097152 kB\n"
        "SwapTotal: 1048576 kB\nSwapFree: 524288 kB\n"
    )

    assert tools.watch.parse_meminfo(meminfo) == (2048.0, 512.0)
    assert tools.watch.parse_temperature("61234\n") == 61.2
    assert tools.watch.parse_throttled("throttled=0x50005\n") == 0x50005
    assert tools.watch.parse_kib("20480\t/prometheus\n") == 20.0
    assert tools.watch.parse_bytes(" 10485760\n") == 10.0


def test_what_cannot_be_read_is_none_not_zero(tools: SimpleNamespace) -> None:
    assert tools.watch.parse_meminfo("") == (None, None)
    assert tools.watch.parse_temperature("") is None
    assert tools.watch.parse_throttled("vcgencmd: not found") is None
    assert tools.watch.parse_bytes("ERROR") is None


def test_each_alert_waits_as_its_rule_says(tools: SimpleNamespace) -> None:
    waits = tools.watch.alert_waits(tools.run.RULES.read_text("utf-8"))

    assert waits["ApiUnavailable"] == timedelta(minutes=1)
    assert waits["ObservationsOverdue"] == timedelta(minutes=30)
    assert waits["LossBudgetThresholdReached"] == HOUR
    assert tools.watch.grace_for("ObservationsOverdue", waits) == timedelta(minutes=35)
    assert tools.watch.grace_for("StationOffline", waits) == timedelta(minutes=10)


# --- judging resources -------------------------------------------------------------


def series(per_hour: float, samples: int) -> list[tuple[datetime, float]]:
    return [(T0 + one * HOUR, 200.0 + per_hour * one) for one in range(samples)]


def test_memory_that_climbs_in_the_second_half_fails_the_run(
    tools: SimpleNamespace,
) -> None:
    flat = series(0.0, 20)
    climbing = [
        *flat[:10],
        *((at, value + 10.0 * i) for i, (at, value) in enumerate(flat[10:])),
    ]

    found = tools.watch.judge_resources({"api": flat, "jobs": climbing}, [], [])

    assert found.figures["memory_slope_mib_per_hour"] == {"api": 0.0, "jobs": 10.0}
    assert found.failures == ["memory of jobs grew 10.0 MiB/h, over 4.0"]


def test_a_rehearsal_too_short_to_fit_is_not_judged_on_memory(
    tools: SimpleNamespace,
) -> None:
    found = tools.watch.judge_resources({"api": series(50.0, 9)}, [], [])

    assert found.figures["memory_slope_mib_per_hour"] == {"api": None}
    assert found.failures == []


def test_short_disk_and_a_throttled_host_fail_the_run(tools: SimpleNamespace) -> None:
    machine = tools.watch.Machine
    calm = [machine(disk_free_gib=40.0, throttled=0x0), machine(disk_free_gib=30.0)]
    strained = [*calm, machine(disk_free_gib=3.5, throttled=0x4)]
    sizes = [tools.watch.Sizes(120.0, 10.0), tools.watch.Sizes(340.0, 60.0)]

    calm_found = tools.watch.judge_resources({}, calm, sizes)
    strained_found = tools.watch.judge_resources({}, strained, sizes)

    assert calm_found.failures == []
    assert calm_found.figures["database_mib"] == {"first": 120.0, "last": 340.0}
    assert strained_found.failures == [
        "disk free fell to 3.5 GiB, under 5.0",
        "the host throttled its clock during the run",
    ]


def test_throttling_since_boot_alone_is_not_this_run(tools: SimpleNamespace) -> None:
    """Bits 16 to 19 say it happened once since boot; only the low bits are now."""
    machine = tools.watch.Machine(throttled=0x50000)

    assert tools.watch.judge_resources({}, [machine], []).failures == []


# --- judging alerts ----------------------------------------------------------------


def interval(
    tools: SimpleNamespace, name: str, start: timedelta, end: timedelta
) -> object:
    return tools.run.Interval(name, T0 + start, T0 + end)


def test_a_platform_fault_that_outlasts_its_alert_owes_it(
    tools: SimpleNamespace,
) -> None:
    waits = tools.watch.alert_waits(tools.run.RULES.read_text("utf-8"))
    minutes = timedelta(minutes=1)
    stopped = interval(
        tools, "scheduler_down on platform:jobs", 0 * minutes, 5 * minutes
    )
    paused = interval(
        tools,
        "api_paused on platform:api",
        30 * minutes,
        30 * minutes + timedelta(seconds=20),
    )
    fired = interval(tools, "SchedulerUnavailable", 2 * minutes, 6 * minutes)

    silent = tools.watch.missed_alerts([stopped, paused], [], waits, tools.run.CAUSES)
    heard = tools.watch.missed_alerts(
        [stopped, paused], [fired], waits, tools.run.CAUSES
    )

    assert silent == [
        f"SchedulerUnavailable for scheduler_down on platform:jobs at {T0.isoformat()}"
    ]
    assert heard == [], "and a twenty-second pause owes ApiUnavailable nothing"


def test_a_slow_alert_is_explained_for_as_long_as_it_waits(
    tools: SimpleNamespace,
) -> None:
    """ObservationsOverdue waits thirty minutes; StationOffline does not."""
    waits = tools.watch.alert_waits(tools.run.RULES.read_text("utf-8"))
    blocked = [
        interval(
            tools, "upload_blocked on station:1", timedelta(0), timedelta(minutes=5)
        )
    ]
    late = timedelta(minutes=30)
    overdue = interval(tools, "ObservationsOverdue", late, late + timedelta(minutes=5))
    offline = interval(tools, "StationOffline", late, late + timedelta(minutes=5))

    unexplained = tools.run.false_positives(
        [overdue, offline], blocked, T0 + 2 * HOUR, waits
    )

    assert unexplained == [offline]


def test_history_read_in_pieces_is_joined_where_it_meets(
    tools: SimpleNamespace,
) -> None:
    minutes = timedelta(minutes=1)
    pieces = [
        interval(tools, "StationOffline", 0 * minutes, 10 * minutes),
        interval(
            tools, "StationOffline", 10 * minutes + timedelta(seconds=30), 20 * minutes
        ),
        interval(tools, "StationOffline", 40 * minutes, 41 * minutes),
    ]

    joined = tools.run.join_stretches(pieces, step_s=30)

    assert [(one.start - T0, one.end - T0) for one in joined] == [
        (timedelta(0), 20 * minutes),
        (40 * minutes, 41 * minutes),
    ]


# --- the record and the seal -------------------------------------------------------


def test_each_failure_is_said_in_words(tools: SimpleNamespace) -> None:
    report = {
        "host_pauses": [{"start": "x", "end": "y"}],
        "false_positives": [],
        "missed_alerts": ["SchedulerUnavailable for scheduler_down"],
        "unplanned_restarts": {},
        "not_running_at_end": [],
        "unhealthy_at_end": ["api"],
        "queue_after_settling": 2,
        "resources": {"failures": ["the host throttled its clock during the run"]},
    }

    assert tools.run.failures(report) == [
        "the host slept 1 time(s)",
        "1 platform alert(s) owed by a fault never fired",
        "1 service(s) unhealthy after settling",
        "2 observation(s) still queued after settling",
        "the host throttled its clock during the run",
    ]
    assert tools.run.failures({}) == []


def test_the_sealed_run_is_read_from_what_publish_printed(
    tools: SimpleNamespace,
) -> None:
    printed = (
        "414 faults judged, 0 failed\n"
        "fault run: /datasets/faults/0123456789ab (written)\n"
        f"  hash               {'cd' * 32}\n"
    )

    assert tools.run.sealed_run(printed) == ("0123456789ab", "cd" * 32)
    assert tools.run.sealed_run("414 faults judged, 0 failed\n") is None


def test_a_container_s_health_is_read_beside_its_state(tools: SimpleNamespace) -> None:
    found = tools.run.parse_containers(
        ["api 0 running 0 unhealthy", "jobs 0 running 0"]
    )

    assert [(one.service, one.health) for one in found] == [
        ("api", "unhealthy"),
        ("jobs", "none"),
    ]


def test_samples_read_back_as_they_were_written(
    tools: SimpleNamespace, tmp_path: Path
) -> None:
    taken = tools.run.Sample(
        at=T0.isoformat(),
        containers=[tools.run.Container("api", "running", 0, 0, "healthy")],
        usage=[tools.run.Usage("api", 1.5, 233.0)],
        machine=tools.watch.Machine(memory_available_mib=2048.0, throttled=0),
        sizes=tools.watch.Sizes(120.0, None),
    )

    tools.run._write_samples(tmp_path, [taken])

    assert tools.run._read_samples(tmp_path) == [taken]


# --- starting fresh, and resuming --------------------------------------------


def a_state(tools: SimpleNamespace) -> object:
    return tools.state.RunState(
        started=T0.isoformat(),
        seed=4471,
        hours=72.0,
        stations=10,
        fault_gap_minutes=60.0,
        sample_every_minutes=15.0,
        settle_minutes=10.0,
        memory_slope_mib_per_hour=4.0,
        disk_free_gib=5.0,
        up=True,
    )


def test_an_out_that_holds_a_run_is_refused(
    tools: SimpleNamespace, tmp_path: Path
) -> None:
    assert tools.state.refuse_taken(tmp_path) is None

    tools.state.write_state(tmp_path, a_state(tools))

    assert "already holds a run (run.json)" in tools.state.refuse_taken(tmp_path)
    assert tools.state.read_state(tmp_path) == a_state(tools)


def test_resuming_mends_and_closes_what_the_stopped_tool_left_open(
    tools: SimpleNamespace, tmp_path: Path
) -> None:
    chaos = sys.modules["chaos"]
    tools.state.write_state(tmp_path, a_state(tools))
    ledger = tmp_path / "platform-faults.jsonl"
    ledger.write_text(
        chaos.ledger_line("open", "long-run", "database_restart", T0)
        + chaos.ledger_line(
            "close", "long-run", "database_restart", T0 + timedelta(seconds=9)
        )
        + chaos.ledger_line("open", "long-run", "api_paused", T0 + HOUR),
        "utf-8",
    )
    commands: list[list[str]] = []
    host = tools.run.Host(
        run=lambda command, _stdin: (commands.append(command), (0, ""))[1],
        sleep=lambda _s: None,
        now=lambda: T0 + 2 * HOUR,
    )

    state = tools.run._resume(
        tools.compose.Compose("deploy/docker-compose.yml"), host, tmp_path
    )

    assert commands == [
        ["docker", "compose", "-f", "deploy/docker-compose.yml", "unpause", "api"]
    ]
    assert tools.state.left_open(ledger.read_text("utf-8")) == []
    last = json.loads(ledger.read_text("utf-8").splitlines()[-1])
    assert (last["event"], last["kind"], last["detail"]) == (
        "close",
        "api_paused",
        {"ended": "restart"},
    )
    assert state.interruptions == [
        {"from": T0.isoformat(), "to": (T0 + 2 * HOUR).isoformat()}
    ]


def test_there_is_nothing_to_resume_without_a_run(
    tools: SimpleNamespace, tmp_path: Path
) -> None:
    host = tools.run.Host(
        run=lambda _c, _s: (0, ""), sleep=lambda _s: None, now=lambda: T0
    )

    with pytest.raises(tools.compose.ToolError, match=r"no run\.json"):
        tools.run._resume(tools.compose.Compose("x.yml"), host, tmp_path)


# --- a whole run, on a stack that is not there --------------------------------


PUBLISHED = (
    "1 faults judged, 0 failed\n"
    "fault run: /datasets/faults/0123456789ab (written)\n"
    f"  hash               {'ef' * 32}\n"
)
"""What ``meridian reliability faults --publish`` prints."""


class _Stack:
    """Answers every command a run gives as a calm compose stack would."""

    LEDGER = (
        json.dumps(
            {
                "ledger": 1,
                "event": "open",
                "run_id": "sim-4471",
                "kind": "network_down",
                "target": "station:1",
                "at": "2026-09-29T12:00:30Z",
            }
        )
        + "\n"
    )

    def __init__(self) -> None:
        self.clock = T0
        self.commands: list[list[str]] = []
        self.up = False

    def sleep(self, seconds: float) -> None:
        self.clock += timedelta(seconds=seconds)

    def now(self) -> datetime:
        return self.clock

    def run(self, command: list[str], _stdin: str | None) -> tuple[int, str]:
        self.commands.append(command)
        said = " ".join(command)
        answers = [
            ("--entrypoint cat simulator", (0, self.LEDGER if self.up else "")),
            (" up -d", (0, "")),
            ("ps -q api", (0, "apicontainer\n")),
            ("ps -a -q", (0, "c1\n")),
            ("docker inspect --format {{index", (0, "api 0 running 0 healthy\n")),
            (
                "docker stats",
                (0, json.dumps({"Name": "api", "MemUsage": "200MiB / 7GiB"})),
            ),
            ("/api/v1/alerts", (0, '{"data": {"alerts": []}}')),
            ("query_range", (0, '{"data": {"result": []}}')),
            ("outbox", (0, "0\n")),
            ("psql", (0, "104857600\n")),
            ("du -sk", (0, "1024\t/prometheus\n")),
            (
                "reliability faults",
                (0, PUBLISHED),
            ),
            ("logs --no-color", (0, "api | started\n")),
        ]
        if said.endswith(" up -d") or "--profile" in said:
            self.up = True
        return next((answer for key, answer in answers if key in said), (0, ""))


def test_a_short_run_brings_itself_up_settles_clean_and_seals_itself(
    tools: SimpleNamespace, tmp_path: Path
) -> None:
    stack = _Stack()
    host = tools.run.Host(run=stack.run, sleep=stack.sleep, now=stack.now)
    out = tmp_path / "run"
    args = tools.run._arguments(
        [
            "--hours",
            "0.25",
            "--out",
            str(out),
            "--up",
            "--settle-minutes",
            "1",
            "--fault-gap-minutes",
            "600",
            "--datasets",
            str(tmp_path / "datasets"),
        ]
    )

    report = tools.run.run_long(args, host)

    said = [" ".join(one) for one in stack.commands]
    restarted = next(one for one in said if "--force-recreate simulator" in one)
    assert "SIMULATOR_SCENARIO=clean" in restarted
    assert said.index(restarted) < next(
        i for i, one in enumerate(said) if "stop simulator" in one
    )
    assert report["passed"] is True
    assert report["fault_run"]["sha256"] == "ef" * 32
    sealed = json.loads((out / "long_run.json").read_text("utf-8"))
    assert sealed["passed"] is True and "verdict" not in sealed
    assert sealed["environment"]["arch"]
    assert (out / "logs" / "compose.log").read_text("utf-8") == "api | started\n"
    assert tools.state.refuse_taken(out) is not None, "a second run here is refused"


def test_a_project_holding_an_earlier_ledger_is_refused_before_anything_starts(
    tools: SimpleNamespace, tmp_path: Path
) -> None:
    stack = _Stack()
    stack.up = True
    host = tools.run.Host(run=stack.run, sleep=stack.sleep, now=stack.now)
    args = tools.run._arguments(
        ["--hours", "1", "--out", str(tmp_path / "run"), "--up"]
    )

    with pytest.raises(tools.compose.ToolError, match="already holds a fault ledger"):
        tools.run.run_long(args, host)

    assert not any("up" in one for one in stack.commands)
