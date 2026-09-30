"""Timing error per observation, from a raw snapshot, as the live view reads it.

``EVALUATION.md`` §6.1 measures orbital data quality by when a station first
heard a pass against when the platform predicted it would rise. The two instants
are on different clocks, so the station's is corrected by the clock offset its
nearest heartbeat reported, with the sign fixed once by D-025::

    timing_error = first_detection_at + clock_offset_s − aos

A station whose clock runs fast reports a negative offset, and the correction
moves its detection earlier. Getting the sign wrong raises nothing and inverts
the finding, which is why a test pins it with a known pass.

**The same rules as the live view** (migration 0024, D-177), computed from the
snapshot so a reported figure is regenerable:

* one row per current observation — the latest revision of each assignment's
  report — with a first detection;
* the clock offset is the one the station's latest heartbeat with an offset
  reported, from 30 minutes before the detection to 5 minutes after, and never
  later: an offset reported afterwards describes another clock;
* §6.1's two exclusions are carried, not applied: ``clock_offset_unknown``,
  never assumed zero, and ``within_clock_uncertainty``, an error smaller than
  the clock's own uncertainty.

**One difference, stated** (D-239): a raw snapshot holds heartbeats received
inside some assignment's window, not every heartbeat. A detection is inside its
own assignment's window, so the heartbeats a station sends while it listens are
all there; one sent in the half hour before its window opened is not, and a
detection whose only nearby offset came from then reads as unknown here where
the live view would have found it.

**The stated uncertainty** is the ``timing_uncertainty_s`` the station was
issued with its assignment. An assignment exported without one takes the
published prior at the element set's age (:func:`~meridian.orbit.uncertainty.
timing_uncertainty_at_age`), and each row says which it used.

Reference: docs/DECISIONS.md D-025, D-060, D-177, D-239; ``EVALUATION.md`` §6.1.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from meridian.datasets.row_fields import (
    MalformedSnapshotError,
    field,
    instant,
    integer,
    jsonl_rows,
    optional_instant,
    optional_number,
    text,
)
from meridian.orbit.uncertainty import timing_uncertainty_at_age

__all__ = [
    "CLOCK_OFFSET_UNKNOWN",
    "UNKNOWN_REGIME",
    "WITHIN_CLOCK_UNCERTAINTY",
    "Detection",
    "detections",
]

CLOCK_OFFSET_UNKNOWN = "clock_offset_unknown"
WITHIN_CLOCK_UNCERTAINTY = "within_clock_uncertainty"

_BEFORE = timedelta(minutes=30)
_AFTER = timedelta(minutes=5)
_DAY_S = 86_400.0
UNKNOWN_REGIME = "unknown"

Row = Mapping[str, object]


@dataclass(frozen=True, slots=True)
class Detection:
    """One observation's first detection against its pass's predicted rise."""

    assignment_id: str
    station_id: str
    satellite_id: str
    regime: str
    aos: datetime
    element_set_age_days: float
    uncorrected_s: float
    clock_offset_s: float | None
    clock_uncertainty_s: float | None
    sigma_s: float
    """The stated 1σ timing uncertainty."""

    sigma_source: str
    """``assignment``, or ``prior`` where the assignment carried none."""

    simulated: bool

    @property
    def error_s(self) -> float | None:
        """Corrected by the clock offset (D-025); ``None`` without one."""
        if self.clock_offset_s is None:
            return None
        return self.uncorrected_s + self.clock_offset_s

    @property
    def excluded(self) -> str | None:
        """§6.1's reason to leave it out of the regression, or ``None``."""
        error = self.error_s
        if error is None:
            return CLOCK_OFFSET_UNKNOWN
        if (
            self.clock_uncertainty_s is not None
            and abs(error) < self.clock_uncertainty_s
        ):
            return WITHIN_CLOCK_UNCERTAINTY
        return None


def detections(files: Mapping[str, bytes]) -> list[Detection]:
    """Every current observation with a first detection, in ``aos`` order.

    Args:
        files: The raw snapshot's files.

    Raises:
        MalformedSnapshotError: A table is missing or a row lacks a field.
    """
    tables = {name: _table(files, name) for name in _NEEDED}
    passes = {integer(one, "id"): one for one in tables["passes"]}
    assignments = {text(one, "assignment_id"): one for one in tables["assignments"]}
    epochs = {
        integer(one, "id"): instant(one, "epoch") for one in tables["element_sets"]
    }
    regimes = {
        text(one, "satellite_id"): str(one.get("orbital_regime") or UNKNOWN_REGIME)
        for one in tables["satellites"]
    }
    clocks = _clocks(tables["heartbeats"])
    found = []
    for observation in _current(tables["observations"]):
        detected = optional_instant(observation, "first_detection_at")
        assignment = assignments.get(text(observation, "assignment_id"))
        if detected is None or assignment is None:
            continue
        predicted = passes[integer(assignment, "pass_id")]
        found.append(
            _detection(
                (observation, assignment, predicted),
                detected,
                epochs[integer(predicted, "element_set_id")],
                regimes,
                clocks,
            )
        )
    return sorted(found, key=lambda one: (one.aos, one.assignment_id))


_NEEDED = (
    "passes",
    "assignments",
    "observations",
    "heartbeats",
    "element_sets",
    "satellites",
)


def _table(files: Mapping[str, bytes], name: str) -> list[Row]:
    try:
        return jsonl_rows(files[f"{name}.jsonl"], f"{name}.jsonl")
    except KeyError as exc:
        message = f"the raw snapshot has no {name}.jsonl"
        raise MalformedSnapshotError(message) from exc


def _current(observations: Sequence[Row]) -> list[Row]:
    """The latest revision of each assignment's report."""
    latest: dict[str, Row] = {}
    for one in observations:
        key = text(one, "assignment_id")
        if key not in latest or integer(one, "revision") > integer(
            latest[key], "revision"
        ):
            latest[key] = one
    return [latest[key] for key in sorted(latest)]


Clock = tuple[datetime, float, float | None]
"""When a heartbeat arrived, its offset, and its uncertainty."""


def _clocks(heartbeats: Sequence[Row]) -> dict[str, list[Clock]]:
    """Each station's heartbeats that carried an offset, oldest first."""
    held: dict[str, list[Clock]] = defaultdict(list)
    for one in heartbeats:
        offset = optional_number(one, "clock_offset_s")
        if offset is not None:
            held[text(one, "station_id")].append(
                (
                    instant(one, "received_at"),
                    offset,
                    optional_number(one, "clock_uncertainty_s"),
                )
            )
    # Keyed so that two heartbeats at one instant with one offset, one of them
    # without an uncertainty, never compare None with a float.
    return {station: sorted(found, key=_clock_order) for station, found in held.items()}


def _clock_order(one: Clock) -> tuple[datetime, float, bool, float]:
    at, offset, uncertainty = one
    return at, offset, uncertainty is not None, uncertainty or 0.0


def _nearest(clocks: Sequence[Clock], detected: datetime) -> Clock | None:
    """The latest heartbeat from 30 minutes before to 5 minutes after."""
    index = bisect_right(clocks, detected + _AFTER, key=lambda one: one[0])
    if index == 0:
        return None
    latest = clocks[index - 1]
    return latest if latest[0] >= detected - _BEFORE else None


def _detection(
    rows: tuple[Row, Row, Row],
    detected: datetime,
    epoch: datetime,
    regimes: Mapping[str, str],
    clocks: Mapping[str, list[Clock]],
) -> Detection:
    observation, assignment, predicted = rows
    station = text(predicted, "station_id")
    satellite = text(predicted, "satellite_id")
    aos = instant(predicted, "aos")
    clock = _nearest(clocks.get(station, []), detected)
    stated = optional_number(assignment, "timing_uncertainty_s")
    age_s = (aos - epoch).total_seconds()
    return Detection(
        assignment_id=text(assignment, "assignment_id"),
        station_id=station,
        satellite_id=satellite,
        regime=regimes.get(satellite, UNKNOWN_REGIME),
        aos=aos,
        element_set_age_days=age_s / _DAY_S,
        uncorrected_s=(detected - aos).total_seconds(),
        clock_offset_s=None if clock is None else clock[1],
        clock_uncertainty_s=None if clock is None else clock[2],
        sigma_s=stated
        if stated is not None
        else timing_uncertainty_at_age(age_s).sigma_s,
        sigma_source="assignment" if stated is not None else "prior",
        simulated=bool(field(observation, "simulated")),
    )
