"""Pydantic models for MSP §4.3's ``assignment`` message and its envelope.

The platform-to-station direction: what a heartbeat response carries. The §4.2
request that provokes it lives in ``meridian.api.models.heartbeat``.

Translates ``meridian.store.assignments.DueAssignment`` — one flat row per
assignment — into the nested shape §4.3 puts on the wire. That reshaping belongs
here, on the boundary, and nowhere past it: the store layer mirrors its query's
columns and the wire nests ``element_set``, and neither should learn the other's
shape.

Holds no business logic and touches no database. **It does not decide which
assignments are due** — that is the route's call, against
``store.assignments.find_due_assignments``.

Reference: docs/MSP-SPEC.md §4.2, §4.3, §8; docs/DECISIONS.md D-007, D-035.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel

from meridian.store.assignments import DueAssignment

__all__ = [
    "MAX_ASSIGNMENTS_PER_RESPONSE",
    "AssignmentMessage",
    "ElementSetMessage",
    "HeartbeatResponseBody",
]

MAX_ASSIGNMENTS_PER_RESPONSE = 8
"""MSP §4.2's hard cap on ``assignments``.

D-007 set it so a constrained client can size its buffer at compile time.

**It is not a page size.** Because held assignments are redelivered on every
heartbeat, returning the earliest 8 of 9 returns the *same* 8 every time — the
ninth waits behind them rather than arriving in turn, and may never be delivered
at all. D-035 therefore forbids the situation instead of paginating it: at most 8
assignments may be eligible for one station at any instant, which is an invariant
on whoever creates them. The platform logs when that is violated, so it is
visible rather than silent.
"""


def _wire_time(instant: datetime) -> str:
    """Render an instant in MSP §8's form: milliseconds and a literal ``Z``.

    The same rendering :func:`meridian.api.platform_clock.format_server_time` applies to
    ``server_time``. Python's ``isoformat`` writes ``+00:00``, which is the same
    instant and a different string — and a microcontroller client comparing
    suffixes, or one whose parser accepts only ``Z``, sees a different thing.
    """
    return (
        instant.astimezone(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class ElementSetMessage(BaseModel):
    """The element set §4.3 carries **inline**, inside an assignment.

    Inline rather than by reference for two reasons the specification gives: a
    microcontroller station cannot be expected to fetch and cache orbital data
    independently, and carrying it guarantees platform and station propagate from
    identical inputs — without which measured timing error means nothing, since
    the difference could be the element set rather than the prediction.
    """

    epoch: str
    line1: str
    line2: str


class AssignmentMessage(BaseModel):
    """One MSP §4.3 assignment, exactly the fields the specification lists."""

    assignment_id: str
    satellite_id: str
    start_at: str
    end_at: str
    centre_freq_hz: int
    mode: str
    expected_max_elevation_deg: float
    predicted_yield: float | None
    element_set: ElementSetMessage
    timing_uncertainty_s: float
    priority: float

    @classmethod
    def from_due_assignment(cls, due: DueAssignment) -> AssignmentMessage:
        """Reshape one store row into the wire message.

        Args:
            due: A row from ``store.assignments.find_due_assignments``.

        Returns:
            The §4.3 message. ``start_at`` and ``end_at`` are the *assignment's*
            window, already widened from the pass by ``timing_uncertainty_s``
            when the row was created — this method does not widen them again,
            and a station widening its recording further is acting on
            ``timing_uncertainty_s`` as §4.3 tells it to.

        Note:
            ``simulated`` is deliberately **not** on the wire. MSP §4.3 does not
            define the field, and adding one here would be this repository
            inventing protocol. The flag is carried on the row and reaches a
            station's own records through §5's registration-time declaration
            instead, which is what CLAUDE.md's fifth rule asks of this layer.
        """
        return cls(
            assignment_id=due.assignment_id,
            satellite_id=due.satellite_id,
            start_at=_wire_time(due.start_at),
            end_at=_wire_time(due.end_at),
            centre_freq_hz=due.centre_freq_hz,
            mode=due.mode,
            expected_max_elevation_deg=due.expected_max_elevation_deg,
            predicted_yield=due.predicted_yield,
            element_set=ElementSetMessage(
                epoch=_wire_time(due.element_set_epoch),
                line1=due.element_set_line1,
                line2=due.element_set_line2,
            ),
            timing_uncertainty_s=due.timing_uncertainty_s,
            priority=due.priority,
        )


class HeartbeatResponseBody(BaseModel):
    """MSP §4.2's response — two fields, and no others.

    ``server_time`` is here so a station can estimate its clock offset without
    NTP, from the heartbeat it was already sending. It is the same value §8's
    ``/time`` endpoint publishes, which is why a station with a working
    heartbeat never needs to call that endpoint at all.
    """

    assignments: list[AssignmentMessage]
    server_time: str
