"""``deploy/tools/chaos.py`` — platform faults, decided without Docker.

The tool drives ``docker compose`` against a running deployment, so what it does
to one is exercised by the drill in ``OPERATIONS.md``. What it decides can be
pinned here: the schedule a seed draws, the commands a fault runs and in what
order, that a mend always runs, and that every line it writes to the ledger is
one the simulator's own reader accepts — the two write one format from two
packages that share no code (D-189, D-194).

Marked as a unit test by living in ``tests/unit``: no network, no Docker.

Reference: docs/DECISIONS.md D-189, D-193, D-194.
"""

from __future__ import annotations

import importlib.util
import itertools
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

from meridian_sim.ledger import read_ledger

TOOLS = Path(__file__).resolve().parents[2] / "deploy/tools"
START = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def chaos() -> Iterator[ModuleType]:
    """The tool, imported the way running it does: its directory on the path."""
    sys.path.insert(0, str(TOOLS))
    try:
        spec = importlib.util.spec_from_file_location("chaos", TOOLS / "chaos.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        # Registered first: a dataclass looks its module up while it is built.
        sys.modules["chaos"] = module
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(TOOLS))
        sys.modules.pop("chaos", None)
        sys.modules.pop("compose_db", None)


class World:
    """Stands in for the host: records commands, waits and the clock."""

    def __init__(self, failing: frozenset[str] = frozenset()) -> None:
        self.commands: list[list[str]] = []
        self.slept: list[float] = []
        self.clock = START
        self.failing = failing

    def run(self, command: list[str]) -> int:
        self.commands.append(command)
        return 1 if " ".join(command[-2:]) in self.failing else 0

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.clock += timedelta(seconds=seconds)

    def now(self) -> datetime:
        return self.clock


def effects(chaos: ModuleType, world: World) -> object:
    return chaos.Effects(run=world.run, sleep=world.sleep, now=world.now)


def compose(_chaos: ModuleType) -> object:
    """The compose project, from the copy of compose_db the tool imported."""
    return sys.modules["compose_db"].Compose("deploy/docker-compose.yml")


def test_a_plan_is_the_same_for_the_same_seed(chaos: ModuleType) -> None:
    """What a long run will do can be printed, and repeated, before it runs."""
    assert chaos.plan(4471, 72) == chaos.plan(4471, 72)
    assert chaos.plan(4471, 72) != chaos.plan(4472, 72)


def test_a_plan_leaves_time_to_recover_between_faults(chaos: ModuleType) -> None:
    """A platform never allowed to recover measures the fault rate instead."""
    planned = chaos.plan(4471, 72)

    for earlier, later in itertools.pairwise(planned):
        assert later.at_s >= earlier.at_s + earlier.duration_s + chaos.MIN_GAP_S
    assert all(one.at_s < 72 * 3600 for one in planned)


def test_a_long_run_meets_every_fault(chaos: ModuleType) -> None:
    """Seventy-two hours at the mean gap is dozens of faults, of every kind."""
    planned = chaos.plan(4471, 72)

    assert {one.kind for one in planned} == set(chaos.FAULTS)
    assert 20 < len(planned) < 120


def test_a_restart_is_one_command_and_a_window_around_it(
    chaos: ModuleType, tmp_path: Path
) -> None:
    """Opened before the break, closed after it, so it covers the whole outage."""
    world = World()
    ledger = tmp_path / "faults.jsonl"

    chaos.inject(
        compose(chaos),
        "database_restart",
        ledger,
        "run-1",
        effects=effects(chaos, world),
    )

    assert [one[-2:] for one in world.commands] == [["restart", "db"]]
    (record,) = read_ledger(ledger)
    assert (record.kind, record.target) == ("database_restart", "platform:db")
    assert record.closed_at is not None


def test_a_stopped_scheduler_is_started_again_after_its_duration(
    chaos: ModuleType, tmp_path: Path
) -> None:
    """Broken, held broken for the stated time, mended — and all of it recorded."""
    world = World()
    ledger = tmp_path / "faults.jsonl"

    chaos.inject(
        compose(chaos),
        "scheduler_down",
        ledger,
        "run-1",
        duration_s=120,
        effects=effects(chaos, world),
    )

    assert [one[-2:] for one in world.commands] == [["stop", "jobs"], ["start", "jobs"]]
    assert world.slept == [120]
    (record,) = read_ledger(ledger)
    assert record.closed_at - record.opened_at == timedelta(seconds=120)


def test_a_failed_break_still_mends_and_closes_the_window(
    chaos: ModuleType, tmp_path: Path
) -> None:
    """A tool that paused the API and then failed must not leave it paused."""
    world = World(failing=frozenset({"pause api"}))
    ledger = tmp_path / "faults.jsonl"

    with pytest.raises(sys.modules["compose_db"].ToolError, match="pause api"):
        chaos.inject(
            compose(chaos), "api_paused", ledger, "run-1", effects=effects(chaos, world)
        )

    assert [one[-2:] for one in world.commands] == [
        ["pause", "api"],
        ["unpause", "api"],
    ]
    (record,) = read_ledger(ledger)
    assert record.closed_at is not None


def test_a_failed_mend_is_reported_and_the_window_still_closes(
    chaos: ModuleType, tmp_path: Path
) -> None:
    """The operator is told; the ledger is not left with a window open forever."""
    world = World(failing=frozenset({"start jobs"}))
    ledger = tmp_path / "faults.jsonl"

    with pytest.raises(sys.modules["compose_db"].ToolError, match="start jobs"):
        chaos.inject(
            compose(chaos),
            "scheduler_down",
            ledger,
            "run-1",
            duration_s=1,
            effects=effects(chaos, world),
        )

    (record,) = read_ledger(ledger)
    assert record.closed_at is not None


def test_a_run_injects_each_fault_at_its_offset(
    chaos: ModuleType, tmp_path: Path
) -> None:
    """In order, waiting between them on the clock the tool was given."""
    world = World()
    ledger = tmp_path / "faults.jsonl"
    planned = (
        chaos.Planned(at_s=100.0, kind="platform_restart", duration_s=0.0),
        chaos.Planned(at_s=1300.0, kind="api_paused", duration_s=20.0),
    )

    ran = chaos.run(compose(chaos), planned, ledger, "run-1", effects(chaos, world))

    assert ran == 2
    records = read_ledger(ledger)
    assert [one.kind for one in records] == ["platform_restart", "api_paused"]
    assert [one.opened_at - START for one in records] == [
        timedelta(seconds=100),
        timedelta(seconds=1300),
    ]


def test_every_line_is_one_the_simulator_reads(
    chaos: ModuleType, tmp_path: Path
) -> None:
    """One format, written from two packages that share no code (D-189)."""
    ledger = tmp_path / "faults.jsonl"
    for kind in sorted(chaos.FAULTS):
        ledger.open("a").write(chaos.ledger_line("open", "run-1", kind, START))
        ledger.open("a").write(chaos.ledger_line("close", "run-1", kind, START))

    records = read_ledger(ledger)

    assert {one.kind for one in records} == set(chaos.FAULTS)
    assert all(one.target.startswith("platform:") for one in records)


def test_a_naive_instant_is_refused(chaos: ModuleType) -> None:
    """Set against the platform's UTC clock, a zoneless time means nothing."""
    with pytest.raises(ValueError, match="timezone"):
        chaos.ledger_line("open", "run-1", "api_paused", datetime(2026, 9, 29))  # noqa: DTZ001
