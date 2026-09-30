"""The questions about a station that went offline: detected, left alone, replanned.

Each starts from the silence around the fault — the last heartbeat before it and
the first after — and asks what the platform did while the station read
``offline`` (D-192). Pure; :mod:`~meridian.reliability.faults` asks them.

Reference: docs/DECISIONS.md D-166, D-171, D-190, D-192, D-196.
"""

from __future__ import annotations

from datetime import datetime

from meridian.registry.liveness import OFFLINE_AFTER_S
from meridian.reliability.fault_ledger import InjectedFault
from meridian.reliability.fault_model import (
    ROUND_READ_GRACE,
    SILENCING,
    Check,
    Silence,
    StationEvidence,
    StationWork,
    offline_span,
)

__all__ = ["detected", "no_new_work", "replanned"]


def detected(fault: InjectedFault, silence: Silence) -> Check:
    """SC-5: offline within ninety seconds of the fault, when it lasted that long."""
    if fault.kind not in SILENCING:
        return Check("detected", None, f"{fault.kind} leaves the station reachable")
    if silence.last is None:
        return Check("detected", None, "the station was never heard before the fault")
    offline_at = silence.offline_at
    if offline_at is None:
        gap = (silence.next - silence.last).total_seconds() if silence.next else 0.0
        return Check(
            "detected",
            True,
            f"silent {gap:.0f} s, short of offline: never offline, correctly (D-190)",
        )
    latency = (offline_at - fault.opened_at).total_seconds()
    return Check(
        "detected",
        latency <= OFFLINE_AFTER_S,
        f"offline {latency:.0f} s after the fault began (SC-5: ≤ {OFFLINE_AFTER_S} s)",
    )


def no_new_work(silence: Silence, evidence: StationEvidence) -> Check:
    """Nothing decided for the station while it read offline (D-166)."""
    span = offline_span(silence)
    if span is None:
        return Check("no_new_work", None, "the station was never offline")
    start, end = span
    given = [
        one.assignment_id
        for one in evidence.work
        if start <= one.decided_at and (end is None or one.decided_at < end)
    ]
    return Check(
        "no_new_work",
        not given,
        "nothing was decided for it while offline"
        if not given
        else f"decided while offline: {', '.join(given)}",
    )


def replanned(silence: Silence, evidence: StationEvidence) -> Check:
    """A round that ran while it was offline revoked the work it had not begun.

    And, for a station that never went offline, that nothing of its was revoked
    as offline in the gap: a heartbeat delay short of ninety seconds must cost
    the station nothing it holds (D-190).
    """
    span = offline_span(silence)
    if span is None:
        return _nothing_revoked_in(silence, evidence)
    start, end = span
    # A round owes an outage a revocation only if the station was still silent
    # when the round had finished reading. One that began offline and read the
    # station after it returned saw it back, and was right to leave its work.
    during = [
        one
        for one in evidence.rounds
        if start <= one
        and (end is None or evidence.round_ends.get(one, one + ROUND_READ_GRACE) < end)
    ]
    if not during:
        return Check("replanned", None, "no scheduling round ran while it was offline")
    first_round = min(during)
    owed = tuple(
        one
        for one in evidence.work
        if one.decided_at < start
        and one.start_at > first_round
        and one.live_before(first_round)
    )
    return _revocations_of(owed, start, end)


def _nothing_revoked_in(silence: Silence, evidence: StationEvidence) -> Check:
    """For a gap short of offline: no work was revoked as offline inside it."""
    if silence.last is None or silence.next is None:
        return Check("replanned", None, "the station was never offline")
    last, heard = silence.last, silence.next
    # Open at both ends. A round at the instant a heartbeat arrived may have
    # judged liveness before it, when the station had been silent ninety
    # seconds — offline, correctly, in the gap before this one.
    early = [
        one.assignment_id
        for one in evidence.work
        if any(last < at < heard for at in one.offline_revocations)
    ]
    if early:
        return Check(
            "replanned", False, f"revoked as offline, never offline: {', '.join(early)}"
        )
    return Check("replanned", None, "never offline, and nothing revoked as offline")


def _revocations_of(
    owed: tuple[StationWork, ...], offline_at: datetime, heard_at: datetime | None
) -> Check:
    """Whether every piece of owed work was revoked as offline inside this outage."""
    revoked_at = {
        one.assignment_id: min(
            (
                at
                for at in one.offline_revocations
                if offline_at <= at and (heard_at is None or at < heard_at)
            ),
            default=None,
        )
        for one in owed
    }
    kept = [name for name, at in revoked_at.items() if at is None]
    if kept:
        return Check(
            "replanned", False, f"not revoked while offline: {', '.join(kept)}"
        )
    if not owed:
        return Check("replanned", True, "it held no unbegun work to revoke")
    first = min(at for at in revoked_at.values() if at is not None)
    decided_again = sum(one.redecided_at is not None for one in owed)
    return Check(
        "replanned",
        True,
        f"{len(owed)} revoked {(first - offline_at).total_seconds():.0f} s after "
        f"going offline; {decided_again} decided again",
    )
