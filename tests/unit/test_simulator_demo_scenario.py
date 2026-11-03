"""``demo`` — the diagnosis faults spread one to a station, over a fifth of the fleet.

No marker: which fault a station carries is a function of its seed, so every
property here is arithmetic over seeds, checked without a platform.

Reference: docs/DECISIONS.md D-075, D-253, D-293, D-330.
"""

from __future__ import annotations

from collections import Counter

import pytest

from meridian_sim.clock_faults import step_for
from meridian_sim.config import seed_for_station
from meridian_sim.fault_choice import FLEET_FAULTS, ONE_FAULT_SHARE, station_faults
from meridian_sim.fault_schedule import schedule_for
from meridian_sim.faults import (
    CLOCK_STEP,
    SATELLITE_SILENT,
    SCENARIOS,
    SIGNAL_DEGRADATION,
    SKY_FAULTS,
)
from meridian_sim.sky_faults import sky_faults_for

MASTER_SEED = 4471
OWN = tuple(kind for kind in SCENARIOS["demo"] if kind not in FLEET_FAULTS)


def own_fault(index: int, master_seed: int = MASTER_SEED) -> str | None:
    """The one station fault station ``index`` carries under ``demo``, if any."""
    kinds = station_faults(seed_for_station(master_seed, index), "demo")
    own = [kind for kind in kinds if kind not in FLEET_FAULTS]
    assert len(own) <= 1, kinds
    return own[0] if own else None


def test_a_station_carries_one_fault_at_most_and_the_fleets_silence() -> None:
    for index in range(1, 201):
        kinds = station_faults(seed_for_station(MASTER_SEED, index), "demo")

        assert SATELLITE_SILENT in kinds
        assert len([one for one in kinds if one not in FLEET_FAULTS]) <= 1


def test_about_a_fifth_of_a_fleet_carries_one_and_every_kind_is_carried() -> None:
    carried = Counter(own_fault(index) for index in range(1, 2001))
    faulty = 2000 - carried.pop(None)

    assert abs(faulty / 2000 - ONE_FAULT_SHARE["demo"]) < 0.03
    assert set(carried) == set(OWN)


def test_the_default_fleet_is_pinned() -> None:
    """Seed 4471's fifty stations, which the demo trial is judged on (D-330).

    Pinned so that a change to the draw shows here, not as a trial that
    quietly flew a different fleet from the one ``SCALE-AND-FAULTS.md`` reports.
    """
    fleet = {index: own_fault(index) for index in range(1, 51)}
    faulty = {index: kind for index, kind in fleet.items() if kind is not None}

    assert faulty == {
        4: "clock_step",
        7: "clock_step",
        24: "obstruction",
        29: "decoder_degraded",
        32: "decoder_degraded",
        34: "clock_step",
        35: "receiver_down",
        40: "receiver_down",
        41: "receiver_down",
        42: "decoder_degraded",
        50: "interference",
    }


def test_a_stations_fault_is_its_seeds_alone() -> None:
    """D-075: the same station, whatever the fleet's size or the call's order."""
    forwards = [own_fault(index) for index in range(1, 51)]
    backwards = [own_fault(index) for index in range(50, 0, -1)]

    assert forwards == backwards[::-1]
    assert [own_fault(i, MASTER_SEED + 1) for i in range(1, 51)] != forwards


@pytest.mark.parametrize("index", range(1, 51))
def test_every_draw_follows_the_stations_one_fault(index: int) -> None:
    seed = seed_for_station(MASTER_SEED, index)
    fault = own_fault(index)
    schedule = schedule_for(seed, "demo")
    drawn = {one.kind for one in schedule.cycles} | {one.kind for one in schedule.sky}

    assert drawn == ({fault} if fault else set())
    assert (schedule.clock_step_s != 0.0) == (fault == CLOCK_STEP)


def test_a_carried_fault_is_the_one_its_own_module_draws() -> None:
    """The pinned modules draw for the scenario; the schedule keeps the station's.

    So a station that carries a fault carries the very shape and onset the
    specification's code drew for it (D-270), not a second draw of its own.
    """
    for index in (4, 24, 50):
        seed = seed_for_station(MASTER_SEED, index)
        schedule = schedule_for(seed, "demo")
        if own_fault(index) == CLOCK_STEP:
            assert schedule.clock_step_s == step_for(seed, "demo")
        else:
            assert set(schedule.sky) <= set(sky_faults_for(seed, "demo"))
            assert len(schedule.sky) == 1


def test_a_clean_station_breaks_on_no_tick() -> None:
    clean = next(i for i in range(1, 51) if own_fault(i) is None)
    schedule = schedule_for(seed_for_station(MASTER_SEED, clean), "demo")

    assert not any(schedule.active_at(tick) for tick in range(2000))


def test_every_other_scenario_draws_every_kind_it_names() -> None:
    seed = seed_for_station(MASTER_SEED, 1)

    for name, kinds in SCENARIOS.items():
        if name not in ONE_FAULT_SHARE:
            assert station_faults(seed, name) == kinds


def test_demo_is_the_diagnosis_set_and_leaves_degradation_out() -> None:
    """A degradation from a cold start is trained into the first baseline (D-293)."""
    assert set(SCENARIOS["demo"]) == set(SCENARIOS["diagnosis"])
    assert SIGNAL_DEGRADATION not in SCENARIOS["demo"]
    assert set(SKY_FAULTS) - set(SCENARIOS["demo"]) == {SIGNAL_DEGRADATION}
