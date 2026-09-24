"""Configurations A to D, and the cold-start path — both decided here, in code.

**The groups are fixed; the configuration chooses between them** (D-160):

========= ================================= =================================
Name      Model inputs                      Objective
========= ================================= =================================
A         ``elevation``                     the probability
B         as A                              the probability × priority
C         ``ours``                          the probability
D         every group                       the probability
========= ================================= =================================

**Priority is never a model input.** It is what an operator values, not a cause
of reception, so B's probabilities are A's; B differs in what the scheduler
maximises (D-066). :func:`objective` is that difference, and it is all of it.

**``conditions`` is a group with no features** until Stage 31 ingests the
public geomagnetic and weather series (``EVALUATION.md`` §3). It is named here
so that D-without-conditions is one entry in ``exclude`` away, not a code
change.

**Cold start is a route, not a default** (D-161). A configuration that reads
the station's own record cannot describe a station that has none; one with
fewer than ``min_station_history`` settled, usable outcomes is scored by the
geometry-only model instead, and the route says which and why. A
configuration reading no history (A, B) needs no route: it is already
geometry-only.

Reference: docs/DECISIONS.md D-066, D-160, D-161; docs/EVALUATION.md §3.
"""

from __future__ import annotations

from dataclasses import dataclass

from meridian.prediction.features import FEATURES
from meridian.prediction.score import Route, route_for

__all__ = [
    "CONFIGURATIONS",
    "FALLBACK",
    "GROUPS",
    "Configuration",
    "Route",
    "objective",
    "route",
]

GROUPS = ("elevation", "geometry", "ours", "conditions")
"""Every group a feature can belong to. ``conditions`` is empty until Stage 31."""


@dataclass(frozen=True, slots=True)
class Configuration:
    """One configuration: the groups it reads, and whether priority weights it."""

    name: str
    groups: tuple[str, ...]
    weighted_by_priority: bool
    question: str
    """What it answers, as ``EVALUATION.md`` §3 puts it."""

    @property
    def features(self) -> tuple[str, ...]:
        """Its inputs, in :data:`FEATURES` order."""
        return tuple(one.name for one in FEATURES if one.group in self.groups)

    @property
    def reads_history(self) -> bool:
        """Whether a station with no record can be scored by it as it is."""
        return "ours" in self.groups


CONFIGURATIONS: dict[str, Configuration] = {
    "A": Configuration("A", ("elevation",), False, "naive baseline"),
    "B": Configuration("B", ("elevation",), True, "what existing practice achieves"),
    "C": Configuration(
        "C", ("ours",), False, "do our signals carry independent information?"
    ),
    "D": Configuration("D", GROUPS, False, "the shipped system"),
}

FALLBACK = Configuration(
    "geometry", ("elevation", "geometry"), False, "cold start (D-161)"
)
"""What a station without enough history is scored by: the orbit, nothing else."""


def route(
    configuration: Configuration, station_history: int, min_station_history: int
) -> Route:
    """The model a pass is scored by, given its station's settled record.

    The rule itself lives in :mod:`meridian.prediction.score`, which the
    scheduler imports without this module's feature table (D-155).
    """
    return route_for(
        configuration.name,
        reads_history=configuration.reads_history,
        station_history=station_history,
        min_station_history=min_station_history,
    )


def objective(
    configuration: Configuration, probability: float, priority: float
) -> float:
    """What the scheduler maximises for a pass under this configuration.

    The probability, except under B, where it is weighted by the satellite's
    priority (D-066, D-160).
    """
    return probability * priority if configuration.weighted_by_priority else probability
