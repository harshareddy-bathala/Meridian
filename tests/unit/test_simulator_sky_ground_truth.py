"""Stage 25's ground truth goes to the run record, and never onto the wire.

D-105: a diagnoser able to read the injected cause would score perfectly and
prove nothing, so the cause is written beside the seed, in the ledger, and
nowhere a platform could read it. These tests hold both halves: the ledger names
every fault with its parameters and the passes it acted on, and no body a
virtual station sends mentions any of it.

No marker: the executor and the ledger need a filesystem and nothing else.

Reference: docs/DECISIONS.md D-105, D-189, D-253.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from meridian.orbit.skyfield_service import SkyfieldOrbitService
from meridian.orbit.types import ElementSet as OrbitElementSet
from meridian.orbit.types import GroundSite, PassSearch
from meridian_client.assignment_message import Assignment, ElementSet
from meridian_client.observation_message import (
    ObservationResult,
    build_observation_body,
)
from meridian_sim.config import profile_for_station, seed_for_pass, seed_for_station
from meridian_sim.executor import SimulatedExecutor
from meridian_sim.fault_notes import FaultNotes
from meridian_sim.fault_schedule import schedule_for
from meridian_sim.faults import (
    INTERFERENCE,
    OBSTRUCTION,
    SATELLITE_SILENT,
    SIGNAL_DEGRADATION,
    SKY_FAULTS,
    FaultState,
)
from meridian_sim.fleet_faults import FleetFaults
from meridian_sim.ledger import FaultLedger, read_ledger
from meridian_sim.outcomes import decide_outcome
from meridian_sim.sky_faults import (
    ActiveSkyFault,
    Degradation,
    Interference,
    Obstruction,
    Silence,
    SkyFault,
    silence_for,
)
from meridian_sim.sky_track import Site

STATION_SEED = seed_for_station(4471, 1)
PROFILE = profile_for_station(1, STATION_SEED, "sim-4471")
SITE = Site(PROFILE.lat_deg, PROFILE.lon_deg, PROFILE.alt_m)
EPOCH = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
LINE1 = "1 57166U 23091A   26223.50000000  .00000100  00000-0  50000-4 0  9990"
LINE2 = "2 57166  98.7041 210.4322 0002726  80.4113 279.7297 14.22000000160126"
SATELLITE = "norad:57166"


def _first_pass() -> tuple[datetime, datetime]:
    """A real pass over this station's site, found the way the platform finds it.

    A sector fault acts only where the satellite is above the horizon, so the
    window has to be one the satellite actually crosses at this site.
    """
    (first, *_) = SkyfieldOrbitService().pass_windows(
        PassSearch(
            element_set=OrbitElementSet(SATELLITE, EPOCH, LINE1, LINE2),
            site=GroundSite(SITE.lat_deg, SITE.lon_deg, SITE.alt_m),
            start=EPOCH,
            end=EPOCH + timedelta(days=2),
            min_elevation_deg=30.0,
        )
    )
    return first.aos, first.los


START, END = _first_pass()

LABELS = (
    *SKY_FAULTS,
    "degradation",
    "obstruction",
    "interference",
    "silent",
    "rate_db_per_day",
    "azimuth_from_deg",
    "below_elevation_deg",
    "rise_db",
    "fleet_wide",
)
"""Every name a fault or its parameters is written under."""


def assignment(assignment_id: str) -> Assignment:
    return Assignment(
        assignment_id=assignment_id,
        satellite_id=SATELLITE,
        start_at=START,
        end_at=END,
        centre_freq_hz=137_100_000,
        mode="lrpt",
        expected_max_elevation_deg=70.0,
        predicted_yield=None,
        element_set=ElementSet(epoch=START, line1=LINE1, line2=LINE2),
        timing_uncertainty_s=4.2,
        priority=1.0,
    )


def every_fault() -> tuple[ActiveSkyFault, ...]:
    """All four at once, with a sector that covers the whole sky."""
    onset = START - timedelta(days=1)
    return tuple(
        ActiveSkyFault(SkyFault(kind, shape, 0), onset)
        for kind, shape in (
            (SIGNAL_DEGRADATION, Degradation(2.0)),
            (OBSTRUCTION, Obstruction(0.0, 360.0, 90.0)),
            (INTERFERENCE, Interference(0.0, 360.0, 0, 24, 8.0)),
            (SATELLITE_SILENT, Silence(SATELLITE)),
        )
    )


def run_passes(
    faults: tuple[ActiveSkyFault, ...], count: int = 30
) -> tuple[tuple[ObservationResult, ...], tuple[tuple[str, str], ...]]:
    state = FaultState(active=frozenset(one.fault.kind for one in faults), sky=faults)
    executor = SimulatedExecutor(STATION_SEED, state, SITE)
    for index in range(count):
        work = assignment(f"as_{index:04d}")
        executor.begin(work)
        executor.end(work)
    return executor.take_completed(), executor.take_faulted()


def test_no_fault_and_none_of_its_parameters_reach_an_msp_body() -> None:
    """D-105: the label never travels with the observation."""
    results, acted = run_passes(every_fault())

    assert acted
    for result in results:
        body = json.dumps(build_observation_body(result, "st_one")).lower()
        for label in LABELS:
            assert label not in body, f"{label!r} reached an MSP body"


def test_each_fault_that_changed_a_pass_is_named_against_it() -> None:
    """The ground truth Stage 27 joins to its diagnoses afterwards."""
    faults = every_fault()[:3]

    _, acted = run_passes(faults, count=5)

    assert {kind for kind, _ in acted} == {
        SIGNAL_DEGRADATION,
        OBSTRUCTION,
        INTERFERENCE,
    }
    assert {one for _, one in acted} == {f"as_{i:04d}" for i in range(5)}


def test_a_silence_is_named_when_the_pass_begins() -> None:
    """Its window may close before the pass ends, and the ledger refuses an
    act on a window that has closed."""
    silence = every_fault()[3]
    executor = SimulatedExecutor(
        STATION_SEED, FaultState(frozenset({SATELLITE_SILENT}), (silence,)), SITE
    )

    executor.begin(assignment("as_quiet"))

    assert executor.take_faulted() == ((SATELLITE_SILENT, "as_quiet"),)
    executor.end(assignment("as_quiet"))
    assert executor.take_faulted() == ()
    (result,) = executor.take_completed()
    assert result.outcome == "no_signal"


def test_a_pass_that_aborts_on_its_own_is_never_named_against_a_silence() -> None:
    """Found by CI on Stage 24: a silence named at the start of a pass that then
    aborted. The abort, not the silence, decided that pass (D-253), and the
    draw is keyed on the platform's assignment id, so it showed only sometimes.
    """
    silence = every_fault()[3]
    executor = SimulatedExecutor(
        STATION_SEED, FaultState(frozenset({SATELLITE_SILENT}), (silence,)), SITE
    )
    aborting = next(
        one
        for one in (f"as_{i:05d}" for i in range(20_000))
        if decide_outcome(
            seed_for_pass(STATION_SEED, one),
            assignment(one).expected_max_elevation_deg,
        ).outcome
        == "aborted"
    )

    executor.begin(assignment(aborting))
    executor.end(assignment(aborting))

    assert executor.take_faulted() == ()
    (result,) = executor.take_completed()
    assert result.outcome == "aborted"


def test_a_fault_in_force_only_after_a_pass_began_does_not_touch_it() -> None:
    """What was in force when the pass began decides, as for a dead receiver."""
    state = FaultState()
    executor = SimulatedExecutor(STATION_SEED, state, SITE)
    work = assignment("as_before")
    executor.begin(work)
    state.sky = every_fault()
    executor.end(work)

    assert executor.take_faulted() == ()


def test_a_fault_in_the_sky_needs_to_know_where_the_station_is() -> None:
    obstruction = every_fault()[1]
    executor = SimulatedExecutor(
        STATION_SEED, FaultState(frozenset({OBSTRUCTION}), (obstruction,))
    )
    executor.begin(assignment("as_nowhere"))

    with pytest.raises(ValueError, match="site"):
        executor.end(assignment("as_nowhere"))


def test_the_ledger_records_every_fault_with_the_parameters_it_was_drawn_with(
    tmp_path: Path,
) -> None:
    """Beside the seed, as D-105 asks: kind, target, onset and shape."""
    ledger = FaultLedger(tmp_path / "faults.jsonl", "run-sky")
    schedule = schedule_for(STATION_SEED, "sky")
    silence = silence_for(4471, "sky", SATELLITE)
    notes = FaultNotes(ledger, FleetFaults(silence=silence))
    opened = frozenset({*(one.kind for one in schedule.sky), SATELLITE_SILENT})

    notes.transitions(
        index=1,
        station=("st_one", STATION_SEED),
        schedule=schedule,
        was=frozenset(),
        now_active=opened,
        tick=60,
        now=START,
    )

    records = {one.kind: one for one in read_ledger(ledger.path)}
    assert set(records) == set(SKY_FAULTS)
    for one in schedule.sky:
        assert records[one.kind].detail == one.detail()
        assert records[one.kind].seed == STATION_SEED
        assert records[one.kind].opened_at == START
    assert silence is not None
    assert records[SATELLITE_SILENT].detail == {
        "satellite_id": SATELLITE,
        "fleet_wide": True,
    }
