"""What a station's reported holdings mean for the assignments it was issued.

Every heartbeat states, in full, which assignments the station currently holds
(MSP §4.2). The platform compares that statement against what it issued and
derives three things: which assignments the station has just confirmed, which one
it has begun executing, and which it named that were never issued to it.

**There is no decline message in MSP.** A decline is absence from the reported
list (D-003), which is why this compares whole sets rather than reading events:
a heartbeat states current truth instead of announcing a change, so a lost
message loses nothing and the next one thirty seconds later says the same thing.

A leaf, like ``registry.capability_match`` and ``registry.liveness``: it imports
nothing from ``meridian``, performs no I/O and reads no clock. Expiry is
deliberately absent — it depends on wall-clock time against each assignment's
window, so it stays in SQL where the comparison happens against one clock.

Reference: docs/MSP-SPEC.md §4.2 (the reconciliation table); docs/DECISIONS.md
D-003, D-008 (the state machine), D-022 (Phase 1 does not reissue), D-067.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "HeldReport",
    "LiveAssignments",
    "Reconciliation",
    "reconcile",
]


@dataclass(frozen=True, slots=True)
class LiveAssignments:
    """What the platform issued to one station, grouped by what it may become.

    Three sets rather than one per state, because reconciliation makes exactly
    two distinctions: an assignment can be confirmed, or it can be started, and
    everything else it needs to know is whether the id belongs to this station at
    all.
    """

    every_id: frozenset[str]
    """Every assignment ever issued to this station, whatever its state.

    The denominator for MSP §4.2's protocol-error row. It includes ``reported``
    and ``expired`` ids on purpose: a station that keeps listing an assignment
    after the platform has closed it is stale, not hostile, and logging that as
    an id the platform never issued would cry wolf on the one signal that is
    supposed to mean a broken implementation.
    """

    awaiting_hold: frozenset[str]
    """The subset in state ``issued`` — offered, not yet confirmed."""

    already_held: frozenset[str]
    """The subset in state ``held`` — confirmed, and eligible to start."""


@dataclass(frozen=True, slots=True)
class HeldReport:
    """One heartbeat's claims about its own work, taken off the wire.

    Both fields come from the station and neither is trusted beyond being
    compared against what the platform issued.
    """

    held_assignment_ids: frozenset[str]
    """MSP §4.2's ``held_assignments``. Empty is meaningful and is not absence:
    it states that the station holds nothing, which is how every assignment it
    was offered gets declined at once."""

    listening_assignment_id: str | None
    """The ``listening`` block's assignment, or ``None`` when the block is
    absent. A station that is idle, slewing or processing sends no block."""


@dataclass(frozen=True, slots=True)
class Reconciliation:
    """The transitions one heartbeat justifies, and the ids it could not place.

    Nothing here has been applied. It is a description of what the caller should
    write, which is what lets the whole comparison be checked on plain strings.
    """

    to_hold: tuple[str, ...]
    """``issued -> held``. Sorted, so a caller's SQL and a test read alike."""

    to_start: str | None
    """``held -> in_progress``, or ``None`` when nothing may start."""

    foreign_ids: tuple[str, ...]
    """Reported, never issued to this station — MSP §4.2's protocol error.

    Returned rather than dropped so the endpoint can log it and a test can assert
    that it changed nothing. The specification's instruction is *log and ignore;
    do not act on it*, and a set the caller never sees cannot be logged.
    """


def reconcile(live: LiveAssignments, reported: HeldReport) -> Reconciliation:
    """Compare what a station says it holds against what it was issued.

    Args:
        live: The platform's own record for this station.
        reported: The station's statement, from one heartbeat.

    Returns:
        The transitions the heartbeat justifies. Applying them is the caller's
        job, and applying ``to_hold`` before ``to_start`` matters — a station may
        confirm an assignment and report listening on it in the same message.

    Note:
        **An issued assignment the station omits is left alone.** MSP §4.2 offers
        "reissue elsewhere or mark expired" for a window still ahead, and D-008's
        state machine has an arc for neither. Phase 1 changes nothing: the
        assignment stays ``issued``, is offered again on the next heartbeat, and
        expires after its window if it is never taken (D-022). That is why this
        function returns no "to release" set — the absence is the whole of the
        decline, and Phase 1's correct response to it is inaction.
    """
    to_hold = live.awaiting_hold & reported.held_assignment_ids
    holds_now = live.already_held | to_hold

    return Reconciliation(
        to_hold=tuple(sorted(to_hold)),
        to_start=_assignment_to_start(reported.listening_assignment_id, holds_now),
        foreign_ids=tuple(sorted(reported.held_assignment_ids - live.every_id)),
    )


def _assignment_to_start(
    listening_assignment_id: str | None, holds_now: frozenset[str]
) -> str | None:
    """The listening block's assignment, if the station also reports holding it."""
    if listening_assignment_id is None:
        return None
    # A station listening on something absent from its own `held_assignments` is
    # contradicting itself in a single message, and a contradiction is not
    # evidence. Ignoring it keeps `in_progress` meaning what D-008 says it means:
    # the station holds this work and is executing it.
    if listening_assignment_id not in holds_now:
        return None
    return listening_assignment_id
