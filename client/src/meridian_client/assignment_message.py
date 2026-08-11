"""Reading MSP §4.3's ``assignment`` message into something a station can act on.

A heartbeat response carries a list of these. This module turns each one into a
frozen :class:`Assignment` with real ``datetime`` objects, and refuses anything
that does not carry every field the specification lists.

Pure: no I/O, no clock, no filesystem. Where an assignment is *kept* is
:mod:`meridian_client.held_assignments`; what a station *does* with one is the
execution loop's problem.

**Everything is validated, nothing is defaulted.** A field the platform omitted
is a protocol violation, not a zero — a station that quietly read a missing
``timing_uncertainty_s`` as ``0.0`` would record a pass on exactly its predicted
boundaries and lose the opening of it, which is the part that decides whether the
decode locks.

Reference: docs/MSP-SPEC.md §4.2, §4.3, §8.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from meridian_client.clock import parse_wire_time

__all__ = [
    "Assignment",
    "ElementSet",
    "MalformedAssignmentError",
    "parse_assignment",
]


class MalformedAssignmentError(ValueError):
    """An assignment the platform sent that this station will not act on.

    Raised rather than skipped by the parser, but a station holding several
    should drop the one and keep the rest — one bad message is not a reason to
    ignore a pass it can still receive.
    """


@dataclass(frozen=True, slots=True)
class ElementSet:
    """The orbital elements §4.3 carries **inline**, inside each assignment.

    Inline rather than by reference so a microcontroller station never has to
    fetch orbital data of its own, and so platform and station propagate from
    byte-identical inputs — without which a measured timing error could be the
    element set rather than the prediction, and means nothing.
    """

    epoch: datetime
    line1: str
    line2: str


@dataclass(frozen=True, slots=True)
class Assignment:
    """One pass this station has been asked to receive.

    Frequencies are integers in Hz, elevations are degrees above the local
    horizon, and every instant is timezone-aware UTC.
    """

    assignment_id: str
    satellite_id: str

    start_at: datetime
    end_at: datetime
    """The window to record, **already widened** from the pass by the platform.

    Not the acquisition and loss instants: the platform opened them out by its
    own stated uncertainty before issuing this (D-021). A station should widen
    further by :attr:`timing_uncertainty_s` if it can afford the recording time,
    as §4.3 asks, rather than treating these as exact.
    """

    centre_freq_hz: int
    mode: str

    expected_max_elevation_deg: float
    predicted_yield: float | None
    """The platform's probability that this pass returns data, or ``None`` when
    it has no model yet. Advisory — §4.3 says so, and a station declines by
    omitting the assignment from its next ``held_assignments``, never by
    reasoning about this number."""

    element_set: ElementSet
    timing_uncertainty_s: float
    priority: float


def _required(message: Mapping[str, object], field: str) -> object:
    """One field, or a :class:`MalformedAssignmentError` naming which was absent."""
    if field not in message:
        raise MalformedAssignmentError(f"assignment is missing {field!r}")
    return message[field]


def _element_set(message: Mapping[str, object]) -> ElementSet:
    """§4.3's nested ``element_set`` object."""
    nested = _required(message, "element_set")
    if not isinstance(nested, Mapping):
        raise MalformedAssignmentError("element_set is not an object")
    return ElementSet(
        epoch=parse_wire_time(str(_required(nested, "epoch")), "element_set.epoch"),
        line1=str(_required(nested, "line1")),
        line2=str(_required(nested, "line2")),
    )


def parse_assignment(message: Mapping[str, object]) -> Assignment:
    """Read one §4.3 assignment off the wire.

    Args:
        message: One entry of a heartbeat response's ``assignments`` array,
            already decoded from JSON.

    Returns:
        The assignment, with instants parsed and units intact.

    Raises:
        MalformedAssignmentError: A field is missing, or ``element_set`` is not
            an object.
        ValueError: A timestamp is not ISO-8601 or carries no UTC offset.

    Note:
        ``predicted_yield`` is the one field allowed to be ``null``, because §4.3
        defines it as advisory and Phase 1 has no model to fill it (D-066). Every
        other field must be present: this is the message a station schedules
        hardware against, and a default would be this client inventing a value
        the platform never sent.
    """
    yield_value = _required(message, "predicted_yield")

    return Assignment(
        assignment_id=str(_required(message, "assignment_id")),
        satellite_id=str(_required(message, "satellite_id")),
        start_at=parse_wire_time(str(_required(message, "start_at")), "start_at"),
        end_at=parse_wire_time(str(_required(message, "end_at")), "end_at"),
        centre_freq_hz=int(str(_required(message, "centre_freq_hz"))),
        mode=str(_required(message, "mode")),
        expected_max_elevation_deg=float(
            str(_required(message, "expected_max_elevation_deg"))
        ),
        predicted_yield=None if yield_value is None else float(str(yield_value)),
        element_set=_element_set(message),
        timing_uncertainty_s=float(str(_required(message, "timing_uncertainty_s"))),
        priority=float(str(_required(message, "priority"))),
    )
