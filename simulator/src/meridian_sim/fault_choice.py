"""Which of a scenario's faults one station carries, drawn from its seed alone.

Most scenarios give every station every fault they name. A few are spread thin,
for a fleet people watch (D-330): there, each station carries at most one of its
own faults, and the fleet's faults (a partition, a silent satellite) belong to
no station in particular. :mod:`~meridian_sim.faults` names the faults and the
scenarios; *when* each happens is :mod:`~meridian_sim.fault_schedule`'s.

Reference: docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md Stage 35;
docs/DECISIONS.md D-075, D-188, D-253, D-330.
"""

from __future__ import annotations

import random

from meridian_sim.faults import PARTITION, SATELLITE_SILENT, SCENARIOS

__all__ = ["FLEET_FAULTS", "ONE_FAULT_SHARE", "station_faults"]


FLEET_FAULTS = frozenset({PARTITION, SATELLITE_SILENT})
"""The faults drawn once for the fleet rather than for a station.

A partition is a share of the fleet cut off at once (D-188), and a silent
satellite is silent at every station (D-253). Neither is a station's to carry or
to be spared.
"""

ONE_FAULT_SHARE: dict[str, float] = {"demo": 0.2}
"""Scenarios in which a station carries at most one of its station faults.

The value is the share of stations that carry one at all. A fleet where every
station breaks every way at once is a fault injector's test; one where a fifth
of the stations each break one way is what a network looks like, and lets a
reader see a loss beside the healthy stations it stands out from.
"""


def station_faults(station_seed: int, scenario: str) -> tuple[str, ...]:
    """The fault kinds one station's own schedule is drawn for.

    Args:
        station_seed: The station's seed, from
            :func:`~meridian_sim.config.seed_for_station`.
        scenario: A key of :data:`SCENARIOS`.

    Returns:
        Every kind the scenario names, unless it is in :data:`ONE_FAULT_SHARE`.
        Then the fleet's faults and at most one station fault, chosen from the
        station's seed alone: a station's fault never depends on how many
        stations the fleet has (D-075), and a restart keeps the same one.

    Raises:
        KeyError: No such scenario, for the reason
            :func:`~meridian_sim.fault_schedule.schedule_for` gives.
    """
    kinds = SCENARIOS[scenario]
    share = ONE_FAULT_SHARE.get(scenario)
    if share is None:
        return kinds
    fleet = tuple(kind for kind in kinds if kind in FLEET_FAULTS)
    own = tuple(kind for kind in kinds if kind not in FLEET_FAULTS)
    stream = random.Random(f"{station_seed}:{scenario}:pick")
    if stream.random() >= share:
        return fleet
    return (stream.choice(own), *fleet)
