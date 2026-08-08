"""Pydantic models for MSP §4.2's ``heartbeat`` request.

Parses and validates the wire message and translates it into
``meridian.store.heartbeats.NewHeartbeat``. The §4.3 assignment message the
response carries lives in ``meridian.api.models.assignment``; this module is the
station-to-platform direction only.

Holds no business logic and touches no database, matching ``meridian.api.msp``'s
own rule. In particular it does **not** decide ``simulated``: MSP §4.2 puts no
such field on the wire, and if a later revision does, a station's claim is not
evidence — the value comes from the registration record via
``store.stations.find_station_provenance`` (D-048, CLAUDE.md rule 5).

Reference: docs/MSP-SPEC.md §4.2, §6; docs/DECISIONS.md D-025, D-028.
"""

from __future__ import annotations

import json
from typing import Any, Literal, Self

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from meridian.store.heartbeats import ListeningReport, NewHeartbeat

__all__ = [
    "HEALTH_LIMIT_BYTES",
    "HeartbeatRequestBody",
    "ListeningBlock",
]

HEALTH_LIMIT_BYTES = 4 * 1024
"""MSP §6's cap on the serialised ``health`` object.

Taken from the specification's table. D-028 records why it exists at all:
``health`` is opaque JSON stored verbatim and written every thirty seconds by
every station, so unbounded it is storage exhaustion with no attacker required —
one station with a verbose error array and a loop.

Enforced here rather than by the request-size middleware, which sees only the
whole body. A 60 KiB body carrying a 60 KiB ``health`` passes the 64 KiB cap and
still must be refused.
"""


class ListeningBlock(BaseModel):
    """MSP §4.2's ``listening`` block — **the most important field in MSP**.

    Without it an absence of observations is ambiguous. With it the platform can
    assert that a station was tuned to a specific frequency for a specific target
    at a specific time and heard nothing, which is a real measurement rather than
    a gap.

    All four fields are required, so a partial block is ``malformed`` rather than
    a half-assertion — the specification calls the block all-or-nothing, and the
    ``heartbeat_listening_complete`` CHECK one layer down says the same thing in
    SQL. ``mode`` is required with the rest (D-028): a station tuned to the right
    frequency running the wrong demodulator did not observe the pass, and
    ``Registry.was_listening()`` cannot tell that from a miss without it.
    """

    assignment_id: str
    satellite_id: str
    centre_freq_hz: int = Field(gt=0)
    mode: str

    def to_listening_report(self) -> ListeningReport:
        """The store layer's shape for this block."""
        return ListeningReport(
            assignment_id=self.assignment_id,
            satellite_id=self.satellite_id,
            centre_freq_hz=self.centre_freq_hz,
            mode=self.mode,
        )


class HeartbeatRequestBody(BaseModel):
    """MSP §4.2's full ``heartbeat`` payload."""

    station_id: str

    # AwareDatetime, not datetime: a naive `sent_at` would be stored as though
    # it were UTC and would misreport a station in another timezone by whole
    # hours. CLAUDE.local.md §6 makes that a bug rather than a tolerance, and
    # pydantic rejecting it here turns it into a `400 malformed` a station can
    # act on instead of a wrong row nobody notices.
    sent_at: AwareDatetime

    # MSP §4.2's six values, exactly. A Literal rather than a str with a check,
    # so an unknown state is rejected by the parser with the others.
    state: Literal[
        "idle", "slewing", "listening", "processing", "degraded", "maintenance"
    ]

    # Required, and an empty list is meaningful: §4.2 says it must be sent as
    # `[]` rather than omitted, because "I hold nothing" is a statement and
    # absence is not. There is no decline message in MSP — a decline *is*
    # absence from this list (D-003) — so a missing field would be
    # indistinguishable from declining everything.
    held_assignments: list[str]
    listening: ListeningBlock | None = None

    # Optional and nullable, and `null` is **not** `0.0`. A station claiming a
    # perfect clock and one that cannot measure its own are opposite cases, and
    # treating them alike corrupts every timing figure derived from them — which
    # is why the default is None rather than 0.0 (MSP §4.2, D-025).
    clock_offset_s: float | None = None
    clock_uncertainty_s: float | None = None

    health: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _health_is_within_its_cap(self) -> Self:
        """MSP §6: a ``health`` object over 4 KiB serialised is ``malformed``."""
        if len(json.dumps(self.health).encode("utf-8")) > HEALTH_LIMIT_BYTES:
            raise ValueError(f"health exceeds {HEALTH_LIMIT_BYTES} bytes serialised")
        return self

    @model_validator(mode="after")
    def _clock_uncertainty_is_a_magnitude(self) -> Self:
        """§4.2: uncertainty is an unsigned 1σ magnitude, not an interval.

        A negative value is not a wider estimate, it is a sign error — and
        ``EVALUATION.md`` §6.1 discards measured timing error smaller than this
        number, so a negative one would discard nothing and silently promote
        noise into the timing results.
        """
        if self.clock_uncertainty_s is not None and self.clock_uncertainty_s < 0:
            raise ValueError(
                "clock_uncertainty_s is a magnitude and cannot be negative"
            )
        return self

    def to_new_heartbeat(self, *, simulated: bool) -> NewHeartbeat:
        """The store layer's insertable shape.

        Args:
            simulated: Read from the station's registration record by the
                caller, never from this payload. It is a parameter rather than
                a field precisely so that no wire value can reach the column
                (D-048).

        Returns:
            The row to insert. ``received_at`` is not set here — the platform's
            own clock fills it via the column default, because a station's
            ``sent_at`` is not trusted to partition a hypertable (D-013).
        """
        return NewHeartbeat(
            station_id=self.station_id,
            sent_at=self.sent_at,
            state=self.state,
            held_assignments=tuple(self.held_assignments),
            listening=self.listening.to_listening_report() if self.listening else None,
            health_json=json.dumps(self.health),
            simulated=simulated,
            clock_offset_s=self.clock_offset_s,
            clock_uncertainty_s=self.clock_uncertainty_s,
        )
