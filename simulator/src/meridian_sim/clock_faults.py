"""When a station's clock steps, and by how much — drawn from the seed.

Stage 27's timing fault (D-277). :mod:`~meridian_sim.fault_schedule` draws its
window as it draws every cycled fault's, from these ranges; this module holds
the ranges and draws the step. Both come from streams named for the scenario and
the kind, so no earlier scenario's seed moves (D-188).

Specified in ``docs/SCALE-AND-FAULTS.md`` § A stepped clock, review pending
(D-270).

Reference: docs/DECISIONS.md D-188, D-270, D-277.
"""

from __future__ import annotations

import random

from meridian_sim.faults import CLOCK_STEP, SCENARIOS

__all__ = ["CLOCK_STEP_CYCLE", "STEP_S", "step_for"]

CLOCK_STEP_CYCLE = ((180, 480), (40, 120))
"""Its period and duration ranges, in ticks.

At a thirty-second cadence a step arrives every ninety minutes to four hours and
holds for twenty minutes to an hour: long enough to cover a pass, rare enough
that a station keeps most of its passes, so a run holds both.
"""

STEP_S = (900.0, 1500.0)
"""The range of the step's size, in seconds, ahead or behind.

Fifteen to twenty-five minutes, longer than most passes. A decoded pass stays
decoded while one frame survives, so a smaller step moves a pass and seldom
loses it; one this long takes the recording off the pass, which is the timing
fault that loses passes (``docs/SCALE-AND-FAULTS.md`` § A stepped clock).
"""


def step_for(station_seed: int, scenario: str) -> float:
    """The signed step this station's clock takes while :data:`CLOCK_STEP` holds.

    Args:
        station_seed: The station's seed.
        scenario: A key of :data:`~meridian_sim.faults.SCENARIOS`.

    Returns:
        The step in seconds to a millisecond, positive for a clock ahead; zero
        for a scenario without the fault.

    Raises:
        KeyError: No such scenario.
    """
    if CLOCK_STEP not in SCENARIOS[scenario]:
        return 0.0
    stream = random.Random(f"{station_seed}:{scenario}:{CLOCK_STEP}:size")
    size = stream.uniform(*STEP_S)
    sign = 1.0 if stream.random() < 0.5 else -1.0  # noqa: PLR2004 — a fair coin
    return round(sign * size, 3)
