"""Stage 27's stepped clock: drawn from the seed, moving nothing that came before.

The timing fault Stage 27's diagnosis is scored against (D-277), specified in
``docs/SCALE-AND-FAULTS.md`` § A stepped clock. These tests hold:
- the shape: a cycle and a signed step, both from the seed;
- the effect: a heard pass is recorded moved by the step, its reported window
  untouched, and the outcome follows from what is left;
- the ground truth: every pass it moves is named in the ledger as it begins,
  and nothing about it reaches an MSP body;
- the history: every earlier scenario draws exactly what it drew before.

No marker: the executor, the schedule and the ledger need nothing else.

Reference: docs/DECISIONS.md D-077, D-105, D-188, D-270, D-277.
"""

from __future__ import annotations

import hashlib
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from meridian.reliability.fault_ledger import read_fault_ledger
from meridian_client.assignment_message import Assignment, ElementSet
from meridian_client.observation_message import (
    ObservationResult,
    build_observation_body,
)
from meridian_sim.clock_effects import samples_moved
from meridian_sim.clock_faults import STEP_S, step_for
from meridian_sim.config import seed_for_station
from meridian_sim.evidence import NOISE_ONLY_SNR_DB, SNR_SAMPLE_COUNT
from meridian_sim.executor import SimulatedExecutor
from meridian_sim.fault_notes import FaultNotes
from meridian_sim.fault_schedule import schedule_for
from meridian_sim.faults import CLOCK_STEP, SCENARIOS, FaultState
from meridian_sim.ledger import FaultLedger

MASTER_SEED = 4471
STATION_SEED = seed_for_station(MASTER_SEED, 1)
TICKS = 600
START = datetime(2026, 8, 12, 3, 0, tzinfo=UTC)
END = START + timedelta(minutes=12)
SATELLITE = "norad:57166"
LINE1 = "1 57166U 23091A   26223.50000000  .00000100  00000-0  50000-4 0  9990"
LINE2 = "2 57166  98.7041 210.4322 0002726  80.4113 279.7297 14.22000000160126"

EARLIER = tuple(name for name in SCENARIOS if name != "clock")

EARLIER_DIGEST = "c2a1bf737dce463612f3d3b7b32b2a8f8d8071896b0126790ca668a12fcc3487"
"""Every earlier scenario's schedule and clock error, stations 1 to 3, 600 ticks.

Computed from the simulator as Stage 26 left it, before the stepped clock
existed. A seed has to go on meaning the run it always meant (D-188).
"""


def assignment(assignment_id: str, max_elevation_deg: float = 70.0) -> Assignment:
    return Assignment(
        assignment_id=assignment_id,
        satellite_id=SATELLITE,
        start_at=START,
        end_at=END,
        centre_freq_hz=137_100_000,
        mode="lrpt",
        expected_max_elevation_deg=max_elevation_deg,
        predicted_yield=None,
        element_set=ElementSet(epoch=START, line1=LINE1, line2=LINE2),
        timing_uncertainty_s=4.2,
        priority=1.0,
    )


def run(
    step_s: float, ids: list[str]
) -> tuple[dict[str, ObservationResult], tuple[tuple[str, str], ...]]:
    """Each assignment begun and ended under a clock ``step_s`` off."""
    active = frozenset({CLOCK_STEP}) if step_s else frozenset()
    executor = SimulatedExecutor(
        STATION_SEED, FaultState(active=active, clock_step_s=step_s)
    )
    faulted: list[tuple[str, str]] = []
    for one in ids:
        executor.begin(assignment(one, ELEVATION[one]))
        faulted.extend(executor.take_faulted())
        executor.end(assignment(one, ELEVATION[one]))
        faulted.extend(executor.take_faulted())
    results = {one.assignment_id: one for one in executor.take_completed()}
    return results, tuple(faulted)


def snr(result: ObservationResult) -> tuple[float, ...]:
    assert result.signal is not None
    assert result.signal.snr_samples is not None
    return tuple(one.snr_db for one in result.signal.snr_samples)


IDS = [f"as_{index:04d}" for index in range(60)]
ELEVATION = {one: 5.0 + (index * 13) % 80 for index, one in enumerate(IDS)}
"""From 5° to 84°, so the outcome model gives heard and unheard passes both."""
HEARD = {"decoded", "signal_no_decode"}
RANK = {"decoded": 0, "signal_no_decode": 1, "no_signal": 2}


def test_earlier_scenarios_draw_exactly_what_they_drew_before() -> None:
    digest = hashlib.sha256()
    for index in (1, 2, 3):
        seed = seed_for_station(MASTER_SEED, index)
        for scenario in EARLIER:
            schedule = schedule_for(seed, scenario)
            for tick in range(TICKS):
                kinds = ",".join(sorted(schedule.active_at(tick)))
                error = schedule.clock_error_s(tick)
                digest.update(f"{index}:{scenario}:{tick}:{kinds}:{error!r};".encode())

    assert digest.hexdigest() == EARLIER_DIGEST


def test_the_step_is_drawn_from_the_seed_signed_and_in_range() -> None:
    steps = [step_for(seed_for_station(MASTER_SEED, i), "clock") for i in range(1, 41)]

    assert steps == [
        step_for(seed_for_station(MASTER_SEED, i), "clock") for i in range(1, 41)
    ]
    assert all(STEP_S[0] <= abs(one) <= STEP_S[1] for one in steps)
    assert any(one > 0 for one in steps)
    assert any(one < 0 for one in steps)
    assert all(step_for(STATION_SEED, name) == 0.0 for name in EARLIER)


def test_the_clock_is_off_by_the_step_while_it_holds_and_right_otherwise() -> None:
    schedule = schedule_for(STATION_SEED, "clock")
    held = [tick for tick in range(TICKS) if CLOCK_STEP in schedule.active_at(tick)]

    assert held
    assert len(held) < TICKS // 2
    for tick in range(TICKS):
        expected = schedule.clock_step_s if tick in held else 0.0
        assert schedule.clock_error_s(tick) == expected
        assert schedule.step_at(tick) == expected


def test_a_heard_pass_is_recorded_moved_by_the_step() -> None:
    clean, _ = run(0.0, IDS)
    ahead, _ = run(300.0, IDS)
    window_s = (END - START).total_seconds()
    moved = samples_moved(300.0, window_s, SNR_SAMPLE_COUNT)

    heard = [one for one in IDS if clean[one].outcome in HEARD]
    assert heard
    for one in heard:
        before, after = snr(clean[one]), snr(ahead[one])
        # A clock ahead begins early: its first samples precede the pass, and
        # the rest are the clean ones, later than it believes.
        assert after[moved:] == before[: len(before) - moved]
        assert all(abs(value) <= NOISE_ONLY_SNR_DB for value in after[:moved])
        assert ahead[one].started_at == clean[one].started_at == START
        assert ahead[one].ended_at == clean[one].ended_at == END
        assert clean[one].signal is not None
        assert ahead[one].signal is not None
        assert ahead[one].signal.noise_floor_dbfs == clean[one].signal.noise_floor_dbfs


def test_a_clock_behind_loses_the_beginning_instead() -> None:
    clean, _ = run(0.0, IDS)
    behind, _ = run(-300.0, IDS)
    window_s = (END - START).total_seconds()
    moved = -samples_moved(-300.0, window_s, SNR_SAMPLE_COUNT)

    for one in (one for one in IDS if clean[one].outcome in HEARD):
        before, after = snr(clean[one]), snr(behind[one])
        assert after[: len(after) - moved] == before[moved:]


def test_a_step_in_range_takes_the_recording_off_the_pass() -> None:
    """The shortest step is longer than this pass: every heard pass is lost."""
    clean, _ = run(0.0, IDS)
    for step in (STEP_S[0], -STEP_S[0]):
        stepped, _ = run(step, IDS)
        for one in (one for one in IDS if clean[one].outcome in HEARD):
            assert stepped[one].outcome == "no_signal"


def test_a_shorter_step_moves_a_pass_without_losing_it() -> None:
    """Why the range starts where it does: one frame left keeps a decode."""
    clean, _ = run(0.0, IDS)
    stepped, _ = run(300.0, IDS)

    for one in IDS:
        assert stepped[one].outcome == clean[one].outcome


def test_a_pass_that_heard_nothing_keeps_its_evidence() -> None:
    """Noise moved is noise."""
    clean, _ = run(0.0, IDS)
    stepped, _ = run(STEP_S[1], IDS)

    quiet = [one for one in IDS if clean[one].outcome not in HEARD]
    assert quiet
    for one in quiet:
        assert stepped[one] == clean[one]


def test_every_pass_begun_under_it_is_named_as_it_begins() -> None:
    """Heard or not: the station recorded the wrong stretch of time either way."""
    clean, _ = run(0.0, IDS)
    executor = SimulatedExecutor(
        STATION_SEED,
        FaultState(active=frozenset({CLOCK_STEP}), clock_step_s=STEP_S[0]),
    )
    named = []
    for one in IDS:
        executor.begin(assignment(one, ELEVATION[one]))
        named.extend(executor.take_faulted())
        executor.end(assignment(one, ELEVATION[one]))
        assert executor.take_faulted() == ()

    assert named == [
        (CLOCK_STEP, one) for one in IDS if clean[one].outcome != "aborted"
    ]
    assert any(clean[one].outcome not in HEARD for _, one in named)


def test_the_clock_in_force_when_the_pass_began_decides() -> None:
    state = FaultState()
    executor = SimulatedExecutor(STATION_SEED, state)
    clean, _ = run(0.0, IDS)
    one = next(one for one in IDS if clean[one].outcome in HEARD)

    executor.begin(assignment(one, ELEVATION[one]))
    state.active, state.clock_step_s = frozenset({CLOCK_STEP}), STEP_S[1]
    executor.end(assignment(one, ELEVATION[one]))

    assert executor.take_faulted() == ()
    assert executor.take_completed() == (clean[one],)


def test_nothing_about_the_clock_reaches_an_msp_body() -> None:
    clean, _ = run(0.0, IDS)
    stepped, _ = run(STEP_S[1], IDS)

    for one in IDS:
        body = build_observation_body(stepped[one], "st_one")
        assert set(body) == set(build_observation_body(clean[one], "st_one"))
        text = json.dumps(body).lower()
        for label in ("clock", "step_s", "step"):
            assert label not in text


def test_the_platform_reads_a_ledger_with_a_stepped_clock(tmp_path: Path) -> None:
    path = tmp_path / "faults.jsonl"
    schedule = schedule_for(STATION_SEED, "clock")
    notes = FaultNotes(FaultLedger(path, "run_clock"))
    first = next(t for t in range(TICKS) if CLOCK_STEP in schedule.active_at(t))
    opened, closed = START, START + timedelta(minutes=30)

    notes.transitions(
        index=1,
        station=("st_one", STATION_SEED),
        schedule=schedule,
        was=frozenset(),
        now_active=frozenset({CLOCK_STEP}),
        tick=first,
        now=opened,
    )
    notes.acts(1, [(CLOCK_STEP, "as_0001")], first, opened)
    notes.transitions(
        index=1,
        station=("st_one", STATION_SEED),
        schedule=schedule,
        was=frozenset({CLOCK_STEP}),
        now_active=frozenset(),
        tick=first + 1,
        now=closed,
    )

    (fault,) = read_fault_ledger(io.StringIO(path.read_text()))
    assert fault.kind == CLOCK_STEP
    assert fault.target == "station:1"
    assert fault.detail == {"step_s": schedule.clock_step_s}
    assert fault.assignment_ids == ("as_0001",)


@pytest.mark.parametrize("step_s", [*STEP_S, *(-one for one in STEP_S)])
def test_a_step_always_moves_a_pass_by_at_least_one_sample(step_s: float) -> None:
    """Even the shortest pass: a pass named in the ledger was moved."""
    for minutes in (8, 12, 16):
        assert samples_moved(step_s, minutes * 60.0, SNR_SAMPLE_COUNT) != 0
