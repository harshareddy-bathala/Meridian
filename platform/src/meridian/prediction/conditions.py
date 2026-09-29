"""The public-conditions feature group: what was published before each pass.

D-131 makes a geomagnetic index and local cloud cover *candidate* features —
a group that enters the ablation and earns its place or does not. D-160 named
the group and left it empty; this fills it, with four inputs:

========================= ====================================================
``kp_index``              the planetary K index for the latest interval that
                          had begun by the pass, as published before it
``kp_known``              1 where one was, 0 otherwise
``cloud_cover_pct``       the cloud cover forecast for the pass's hour at the
                          station's cell, as published before it
``cloud_cover_known``     1 where one was, 0 otherwise
========================= ====================================================

**The pre-pass rule** (D-222), in :func:`value_before`, is the whole of the
temporal discipline: a sample is a candidate only if it was *published* before
the pass began. Among candidates the latest interval wins, and among
revisions of that interval the latest published before the pass — never a
revision published after it, which is a different row with a later
``published_at`` and so never a candidate.

**Missing stays missing** (D-221, D-224). No candidate, a stale one, or a
published gap all give a missing value — never the previous value across a
published gap, and never a zero standing for one. The model still needs a
number, so a missing value is written as ``(0.0, known = 0)``: with its
indicator beside it, a linear model absorbs whatever constant fills the value
into the indicator's coefficient, so the 0 is not a claim about the sky.

**Never a gate.** Nothing here can stop a pass being scheduled: a missing value
is a feature like any other, and the scheduler scores every pass (D-131).

Standard library only, like :mod:`meridian.prediction.score`, and it reads
nothing but a snapshot's values.

Reference: docs/DECISIONS.md D-131, D-160, D-221, D-222, D-224.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from meridian.datasets.environment_rows import EnvironmentSample

__all__ = [
    "CLOUD",
    "CONDITION_FEATURES",
    "KP",
    "Choice",
    "Conditions",
    "Rule",
    "value_before",
]

CONDITION_FEATURES = (
    ("kp_index", "Kp published before the pass; 0 when unknown"),
    ("kp_known", "1 if a Kp was published before the pass, else 0"),
    ("cloud_cover_pct", "cloud forecast for the pass's hour; 0 when unknown"),
    ("cloud_cover_known", "1 if one was published before the pass, else 0"),
)

EARTH_RADIUS_KM = 6371.0


@dataclass(frozen=True, slots=True)
class Rule:
    """How one quantity is chosen for a pass."""

    quantity: str
    covering: bool
    """Whether the chosen interval must contain the pass — a forecast for the
    pass's hour — rather than merely have begun before it."""

    max_age: timedelta
    """How long after its interval ends a value still describes a pass."""

    within_km: float | None
    """How far from the station a value may describe, or None for a value
    that describes the whole globe."""


KP = Rule("kp_index", covering=False, max_age=timedelta(hours=6), within_km=None)
"""The latest three-hour interval begun by the pass. Six hours is two
intervals: an index older than that says nothing about the pass's sky."""

CLOUD = Rule("cloud_cover", covering=True, max_age=timedelta(0), within_km=25.0)
"""The hour containing the pass, at a model cell within 25 km of the station."""


@dataclass(frozen=True, slots=True)
class Choice:
    """The value a pass reads, and why — or why it reads none."""

    value: float | None
    reason: str
    sample: EnvironmentSample | None = None
    """The row chosen, where one was: the value's provenance."""


def value_before(
    samples: Sequence[EnvironmentSample],
    rule: Rule,
    at: datetime,
    place: tuple[float, float] | None,
) -> Choice:
    """The value of ``rule.quantity`` a pass beginning at ``at`` may read.

    Args:
        samples: A snapshot's values, any quantities.
        rule: Which quantity, and how it is chosen.
        at: When the pass begins.
        place: The station's latitude and longitude, for a local quantity.

    Returns:
        The chosen value, or a missing one with the reason.
    """
    candidates = [
        one
        for one in samples
        if one.quantity == rule.quantity
        and one.published_at < at
        and one.observed_from <= at
        and _near(one, rule, place)
    ]
    if not candidates:
        return Choice(None, "nothing published before the pass")
    return _judged(max(candidates, key=lambda one: _preference(one, place)), rule, at)


def _judged(chosen: EnvironmentSample, rule: Rule, at: datetime) -> Choice:
    """The chosen row's value, unless it does not describe the pass."""
    if rule.covering and at > chosen.observed_to:
        reason = "nothing published covers the pass"
    elif at - chosen.observed_to > rule.max_age:
        reason = "the latest value is too old"
    elif chosen.value is None:
        reason = f"published as missing: {chosen.missing_reason}"
    else:
        return Choice(chosen.value, "published before the pass", chosen)
    return Choice(None, reason, chosen)


class Conditions:
    """A snapshot's values, answering for any pass.

    Answers exactly as :func:`value_before` does, without scanning every value
    for every pass. Values are grouped by place and sorted by the preference
    that orders them within one place; for a pass, each place near enough is
    searched from the latest interval begun by the pass, back to the first
    published before it, and the nearest place's answer wins. A snapshot holds
    every hourly republish of a forecast, and a model is fitted on thousands
    of passes, so a scan per pass is the difference between seconds and hours.

    Args:
        samples: Every value the snapshot holds.
        places: Each station's latitude and longitude.
    """

    def __init__(
        self,
        samples: Sequence[EnvironmentSample] = (),
        places: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        """Keep only the two quantities the group reads, indexed by place."""
        groups: dict[tuple[str, float | None, float | None], list[EnvironmentSample]]
        groups = {}
        for one in samples:
            if one.quantity in (KP.quantity, CLOUD.quantity):
                key = (one.quantity, one.lat_deg, one.lon_deg)
                groups.setdefault(key, []).append(one)
        self._groups = {
            key: _Place(sorted(members, key=_within_place))
            for key, members in groups.items()
        }
        self._places = places or {}

    def choices(self, station_id: str, at: datetime) -> tuple[Choice, Choice]:
        """The Kp and cloud cover one pass reads."""
        place = self._places.get(station_id)
        return self._choose(KP, at, None), self._choose(CLOUD, at, place)

    def _choose(
        self, rule: Rule, at: datetime, place: tuple[float, float] | None
    ) -> Choice:
        """:func:`value_before`, one binary search per place."""
        best = []
        for (quantity, _, _), held in self._groups.items():
            if quantity == rule.quantity and _near(held.members[0], rule, place):
                found = held.latest_before(at)
                if found is not None:
                    best.append(found)
        if not best:
            return Choice(None, "nothing published before the pass")
        return _judged(max(best, key=lambda one: _preference(one, place)), rule, at)

    def values(self, station_id: str, at: datetime) -> tuple[float, ...]:
        """The group's four features, in :data:`CONDITION_FEATURES` order."""
        return tuple(
            number
            for choice in self.choices(station_id, at)
            for number in _encoded(choice)
        )


class _Place:
    """One place's values, in :func:`_within_place` order."""

    def __init__(self, members: list[EnvironmentSample]) -> None:
        self.members = members
        self._begins = [one.observed_from for one in members]

    def latest_before(self, at: datetime) -> EnvironmentSample | None:
        """The preferred value begun by ``at`` and published before it.

        The first published before ``at``, searching back from the latest
        interval begun by then.
        """
        for index in range(bisect_right(self._begins, at) - 1, -1, -1):
            if self.members[index].published_at < at:
                return self.members[index]
        return None


def _within_place(one: EnvironmentSample) -> tuple[datetime, datetime, int]:
    """:func:`_preference` at one place, where the distance is the same."""
    return one.observed_from, one.published_at, one.sample_id


def _encoded(choice: Choice) -> tuple[float, float]:
    """A value and its indicator; a missing value is ``(0.0, 0.0)`` (D-224)."""
    if choice.value is None:
        return 0.0, 0.0
    return choice.value, 1.0


def _preference(
    one: EnvironmentSample, place: tuple[float, float] | None
) -> tuple[float, datetime, datetime, int]:
    """Nearest first, then the latest interval, then the latest revision.

    The ``sample_id`` last only so a tie has one answer; two rows agreeing on
    every other term describe the same value.
    """
    return (
        -_distance_km(one, place),
        one.observed_from,
        one.published_at,
        one.sample_id,
    )


def _near(
    one: EnvironmentSample, rule: Rule, place: tuple[float, float] | None
) -> bool:
    if rule.within_km is None:
        return True
    return place is not None and _distance_km(one, place) <= rule.within_km


def _distance_km(one: EnvironmentSample, place: tuple[float, float] | None) -> float:
    """Great-circle distance from the station, 0 for a global value."""
    if place is None or one.lat_deg is None or one.lon_deg is None:
        return 0.0
    lat1, lon1 = math.radians(place[0]), math.radians(place[1])
    lat2, lon2 = math.radians(one.lat_deg), math.radians(one.lon_deg)
    half = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(half)))
