"""What goes wrong, and when — drawn from the seed like everything else.

Stage 10 injects five faults: the network drops, the process restarts, the token
stops being accepted, observation uploads stall while heartbeats keep working,
and the receiver dies. Stage 21 adds six more — heartbeats that do not arrive, a
partition that cuts off part of the fleet at once, responses lost after the
platform committed, a drifting clock, a decoder that hears and cannot decode,
and a station that lets go of work it held (D-188). Each is scheduled from a
seed, so a run with faults is exactly as reproducible as a clean one — which is
the whole point of injecting them here rather than by unplugging something.

**Three of the four are injected beneath the client, at the HTTP layer.** A
:class:`FaultInjectingTransport` wraps whatever the station would really have
talked to, so the real :class:`~meridian_client.transport.MspTransport` sees a
real connection failure and runs its real retry policy. Faking the failure any
higher — a stubbed ``heartbeat()``, a patched method — would test the loop
against a rehearsal of an outage instead of one.

The fourth, a restart, cannot be injected from below: it is the supervisor
discarding a station and rebuilding it from what is on disk. This module says
*when*; :mod:`~meridian_sim.supervisor` does it.

This module is the vocabulary — each fault's name, what it means and which
scenario injects it — and the transport that breaks requests. *When* each
fault happens is :mod:`~meridian_sim.fault_schedule`'s: arithmetic over a tick
number, so what a run will do can be printed before it does any of it.

What was injected is written to the run's fault ledger
(:mod:`~meridian_sim.ledger`) and nowhere else: never on MSP, never in the
platform's database (D-189).

Reference: docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md Stages 10 and 21;
docs/DECISIONS.md D-024, D-074, D-075, D-188, D-189.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from meridian_sim.sky_faults import ActiveSkyFault

__all__ = [
    "CLOCK_DRIFT",
    "DECLINES",
    "DECODER_DEGRADED",
    "HEARTBEAT_DELAYED",
    "INTERFERENCE",
    "NETWORK_DOWN",
    "OBSTRUCTION",
    "PARTITION",
    "RECEIVER_DOWN",
    "RESTART",
    "SATELLITE_SILENT",
    "SCENARIOS",
    "SIGNAL_DEGRADATION",
    "SKY_FAULTS",
    "SLOW_API",
    "TOKEN_REVOKED",
    "UPLOAD_BLOCKED",
    "FaultInjectingTransport",
    "FaultState",
]

NETWORK_DOWN = "network_down"
"""Every request fails as if the platform were unreachable."""

UPLOAD_BLOCKED = "upload_blocked"
"""Only ``POST /observations`` fails; heartbeats still get through.

The nastiest of the four and the reason it is here. A station in this state
looks perfectly healthy — it reports, it holds work, it says it is listening —
while its finished observations pile up on disk. It is the shape of the failure
D-074 exists to bound, and it is the one a dashboard would not show.
"""

TOKEN_REVOKED = "token_revoked"
"""The station's credential stops being accepted, and stays that way.

Injected by presenting a token the platform will reject, so the ``401`` the loop
receives is a real one from the real endpoint. It exercises the *station's*
response to revocation (D-024: stop, do not retry), not the platform's
revocation path — an operator running ``meridian station revoke`` is what does
that, and the simulator must not reach into the database to do it itself.
"""

RESTART = "restart"
"""The process dies and comes back, keeping only what reached disk."""

RECEIVER_DOWN = "receiver_down"
"""The client is fine and the radio is not: work is held and never begun.

The only fault of the five that is injected above the transport rather than
below it, because nothing about the network is wrong. It is what produces MSP
§4.4's ``not_attempted`` — a station that took the work and failed to start,
which the specification is emphatic is an operational failure and not the same
thing as listening and hearing nothing.
"""

HEARTBEAT_DELAYED = "heartbeat_delayed"
"""Only ``POST /heartbeat`` fails; everything else gets through.

What the platform sees of a station whose heartbeats are late: a gap in them and
nothing else. A short gap must read ``stale`` and never ``offline``, and cost the
station nothing it holds; a long one must read ``offline`` within SC-5's ninety
seconds. Which one a window is follows from its length, and the verifier decides
that from the ledger rather than this module predicting it (D-190).
"""

PARTITION = "partition"
"""Part of the fleet loses the platform at once, and the rest does not.

Injected like :data:`NETWORK_DOWN` beneath each member's client, but scheduled
for the fleet rather than per station
(:func:`~meridian_sim.fault_schedule.partition_for`): the point is a
correlated loss, where the scheduler has to move work onto the survivors, and
independent outages that happened to coincide would test that only by accident.
"""

SLOW_API = "slow_api"
"""The platform answers too late: the request lands, the response never does.

Forwarded to the platform, which commits it, and then failed with a read
timeout — so the station believes a request was lost that was in fact stored.
That is the case a slow platform produces that an unreachable one does not, and
the one idempotency exists for: the station resends, and the platform must not
store the observation twice (D-015, D-027, D-191).
"""

CLOCK_DRIFT = "clock_drift"
"""The station's clock runs away from true time, and is corrected when it ends.

The supervisor hands the station's loop a ``now`` that is wrong by an amount
growing each tick
(:meth:`~meridian_sim.fault_schedule.FaultSchedule.clock_error_s`). The station
then stamps its heartbeats and places its captures by that clock, as a real one with
a failed time source would. The window closing is a resync, not a slow return.
"""

DECODER_DEGRADED = "decoder_degraded"
"""The receiver hears the satellite and the decoder cannot make frames of it.

``decoded`` becomes ``signal_no_decode``; nothing else changes. It has no
diagnosis category of its own, which is why Stage 27 uses it as a negative
control: the right answer to it is *undetermined*.
"""

DECLINES = "declines"
"""The station lets go of some work it held, before its window opens.

A decline under MSP §4.2 — held, then absent, window still ahead — so the
platform revokes it and may give the time to another pass (D-003, D-171). Which
assignments go is drawn from the station's seed and the assignment's id
(:func:`~meridian_sim.fault_schedule.declines_assignment`), so a restart
declines the same ones.
"""

SIGNAL_DEGRADATION = "signal_degradation"
"""The receive chain loses signal at a steady rate from an onset, and keeps losing it.

A connector corroding, an LNA failing, water in a cable: every pass after the
onset is received that many decibels weaker, growing by a configured rate in dB
per day (Stage 25). The noise floor does not move, because the loss is ahead of
the receiver's own noise.
"""

OBSTRUCTION = "obstruction"
"""Something new blocks a sector of the sky below an elevation, from an onset.

A tree in leaf, a new building, a neighbour's antenna. It is **never declared in
``horizon_mask``**: a station that knew would have declared it, and the platform
would then have stopped scheduling into it (D-175). Samples inside it hear
nothing.
"""

INTERFERENCE = "interference"
"""The noise floor rises in a sector of the sky and a window of hours each day.

A neighbour's switching supply at night, a pager transmitter in one direction.
Samples inside both the sector and the hours lose SNR by the rise, and the
pass's floor rises with the share of it they are.
"""

SATELLITE_SILENT = "satellite_silent"
"""A transmitter stops, at every station at once, while the catalogue says it is on.

The fault is the satellite's rather than any station's, so it is drawn once for
the fleet and opened on every station, as a partition is. A station listening to
it hears exactly what it would hear with nothing there.
"""

SKY_FAULTS = (SIGNAL_DEGRADATION, OBSTRUCTION, INTERFERENCE, SATELLITE_SILENT)
"""Stage 25's four faults with ground-truth causes (D-253).

They change what a station *measures*, not whether it can talk to the platform,
so none of them is injected in the transport: they act in the executor, on the
evidence of one pass. Kept out of ``chaos``, which is Stage 21's long run of
network and process faults and whose schedules must not move.
"""

SCENARIOS: dict[str, tuple[str, ...]] = {
    "clean": (),
    "network": (NETWORK_DOWN,),
    "upload": (UPLOAD_BLOCKED,),
    "restart": (RESTART,),
    "receiver": (RECEIVER_DOWN,),
    "revoked": (TOKEN_REVOKED,),
    "faulty": (NETWORK_DOWN, UPLOAD_BLOCKED, RESTART, RECEIVER_DOWN),
    "heartbeat": (HEARTBEAT_DELAYED,),
    "partition": (PARTITION,),
    "slow": (SLOW_API,),
    "drift": (CLOCK_DRIFT,),
    "decoder": (DECODER_DEGRADED,),
    "declines": (DECLINES,),
    "chaos": (
        NETWORK_DOWN,
        UPLOAD_BLOCKED,
        RESTART,
        RECEIVER_DOWN,
        HEARTBEAT_DELAYED,
        PARTITION,
        SLOW_API,
        CLOCK_DRIFT,
        DECODER_DEGRADED,
        DECLINES,
    ),
    "degradation": (SIGNAL_DEGRADATION,),
    "obstruction": (OBSTRUCTION,),
    "interference": (INTERFERENCE,),
    "silent": (SATELLITE_SILENT,),
    "sky": SKY_FAULTS,
}
"""Which faults each named scenario may inject.

``faulty`` and ``chaos`` deliberately omit ``token_revoked``. A revoked token
stops the loop permanently and by design (D-024), so including it in a general
scenario would mean every station in a long run eventually stopping — a fleet
that dies of old age tests nothing after the first hour. It gets its own
scenario, where a station stopping is the observation being made.

``faulty`` is Stage 10's set and keeps its exact schedules; ``chaos`` is every
recurring fault, and is what Stage 21's long run injects. ``sky`` is Stage 25's
four faults together, whose ground truth Stage 27's diagnosis is scored against.
"""


@dataclass
class FaultState:
    """What is broken right now, shared between the supervisor and the transport.

    Mutable, and the only mutable thing in this module. The transport beneath a
    station is built once and lives for the station's whole life, while what is
    wrong changes every tick — so the supervisor writes here before each tick and
    the transport reads it during. A callable would hide that handover inside a
    closure; a field named after what it holds does not.
    """

    active: frozenset[str] = field(default_factory=frozenset)
    sky: tuple[ActiveSkyFault, ...] = ()
    """The sky faults in force, each with the instant it came into force.

    Read by the receiver when a pass begins, not by the transport: they change
    what a station measures, never whether it can reach the platform.
    """


class FaultInjectingTransport(httpx.BaseTransport):
    """An HTTP transport that breaks on purpose.

    Args:
        inner: Where a request goes when nothing is wrong. A real connection in
            a deployment, and the platform in-process in a test.
        state: What is currently broken, written by the supervisor each tick.

    Note:
        Beneath the client rather than around it. Everything above — the retry
        policy, the backoff, the distinction between a refusal and an
        unreachable platform — is the reference client's own code running for
        real, which is the only arrangement in which a passing fault test says
        anything about a real station.
    """

    def __init__(self, inner: httpx.BaseTransport, state: FaultState) -> None:
        """Wrap ``inner``, consulting ``state`` on every request."""
        self._inner = inner
        self._state = state

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        """Send one request, unless something is currently wrong with it."""
        active = self._state.active
        path = request.url.path

        if NETWORK_DOWN in active:
            raise httpx.ConnectError("simulated network outage", request=request)
        if PARTITION in active:
            raise httpx.ConnectError("simulated network partition", request=request)
        if UPLOAD_BLOCKED in active and path.endswith("/observations"):
            raise httpx.ConnectError("simulated upload stall", request=request)
        if HEARTBEAT_DELAYED in active and path.endswith("/heartbeat"):
            raise httpx.ConnectError("simulated heartbeat delay", request=request)
        if TOKEN_REVOKED in active:
            request.headers["Authorization"] = "Bearer simulated-revoked-token"

        response = self._inner.handle_request(request)
        if SLOW_API in active:
            # After the platform has committed, not before: the request landed
            # and only the answer is lost, which is what a slow platform does.
            response.close()
            raise httpx.ReadTimeout("simulated slow response", request=request)
        return response

    def close(self) -> None:
        """Close the transport underneath."""
        self._inner.close()
