"""Configurations A to D, and the cold-start path — both decided here, in code.

**The groups are fixed; the configuration chooses between them** (D-160):

========= ================================= =================================
Name      Model inputs                      Objective
========= ================================= =================================
A         ``elevation``                     the probability
B         as A                              the probability × priority
C         ``ours``                          the probability
D         every group                       the probability × priority
========= ================================= =================================

**Priority is never a model input.** It is what an operator values, not a cause
of reception, so B's probabilities are A's; B differs in what the scheduler
maximises (D-066). :func:`objective` is that difference, and it is all of it.
D weights by priority as B does, so D − B isolates the model and C − A our
features (D-168, amending D-160). The scheduler's own objective, with its
other terms, is :mod:`meridian.scheduler.objective`; a test holds the two
tables equal.

**``conditions`` is the public geomagnetic and weather group** of
``EVALUATION.md`` §3, filled by Stage 31 (D-224): what a source published
before the pass. Only D reads it. D-131's leave-one-group-out run, D without
this group, is not built yet: it waits on a model of real data (D-224).

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
"""Every group a feature can belong to. ``conditions`` is Stage 31's (D-224)."""


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
    "D": Configuration("D", GROUPS, True, "the shipped system"),
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

    The probability, except under B and D, where it is weighted by the
    satellite's priority (D-066, D-168).
    """
    return probability * priority if configuration.weighted_by_priority else probability
