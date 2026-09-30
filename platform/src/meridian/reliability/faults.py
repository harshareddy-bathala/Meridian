"""Whether the platform handled an injected fault, judged from its own records.

A run's fault ledger says what was done and when (D-189). The platform never
reads it while it runs: it proves what it noticed from what it stored — the
heartbeats it received, the work it decided, revoked and decided again, and how
it classified each settled pass. This module joins the two afterwards and
answers, fault by fault, the roadmap's five questions (D-192):

``detected``
    Did the station read ``offline`` within SC-5's ninety seconds of the fault?
``no_new_work``
    Was it given nothing new while it was offline?
``replanned``
    Did a scheduling round that ran while it was offline revoke the work it had
    not begun, so the pass could be decided again?
``no_false_miss``
    Is no pass lost to the fault classified ``confirmed_miss``? Absence is not a
    miss (CLAUDE.md rule 7), and a fault is exactly when absence happens.
``recovered``
    Was the station heard again once the fault ended?

and two that only some faults ask: ``held`` (did the fault really stop the
station's heartbeats, or is the ledger claiming a silence the platform never
saw?) and ``declines_honoured`` (was each assignment a station let go of
revoked as declined?). With a Prometheus address, ``alerted`` reads when
``StationOffline`` first fired.

**Detection is derived, so its ninety seconds are arithmetic, and said so.**
Liveness is computed on read from the last heartbeat (D-054): a station silent
for ninety seconds *is* offline to every reader from that instant. What this
module checks is that the silence the ledger claims is the silence the platform
stored, and that everything downstream of liveness — the scheduler, the
classification — acted on it. The alert is the measured part of SC-5.

A check that does not apply to a fault, or cannot be judged from the evidence
yet, is ``None`` rather than a pass: a verdict counting it as passed would be
the platform grading itself on questions it was not asked.

Pure: the ledger arrives as lines, the evidence as values, and the store reads
are :mod:`meridian.store.fault_evidence`'s.

Reference: docs/DECISIONS.md D-054, D-171, D-181, D-189, D-190, D-192;
docs/PROJECT.md §9 SC-5.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from meridian.reliability.fault_ledger import (
    LEDGER_VERSION,
    FaultLedgerError,
    InjectedFault,
    read_fault_ledger,
)
from meridian.reliability.fault_model import (
    ALERT_VISIBLE_AFTER,
    MISS_SENSITIVE,
    NOT_LISTENED,
    RECOVERY_WITHIN,
    ROUND_RECOVERY_WITHIN,
    SILENCING,
    WHOLLY_SILENCING,
    Check,
    FaultVerdict,
    PlatformEvidence,
    Silence,
    StationEvidence,
    StationWork,
    silenced_until,
)
from meridian.reliability.fault_offline import detected, no_new_work, replanned

__all__ = [
    "LEDGER_VERSION",
    "Check",
    "FaultLedgerError",
    "FaultVerdict",
    "InjectedFault",
    "PlatformEvidence",
    "StationEvidence",
    "StationWork",
    "judge_platform_fault",
    "judge_station_fault",
    "read_fault_ledger",
]


def judge_station_fault(
    fault: InjectedFault,
    evidence: StationEvidence,
    alongside: Sequence[InjectedFault] = (),
) -> FaultVerdict:
    """Answer every question that applies to a fault done to one station.

    Args:
        fault: The fault being judged.
        evidence: What the platform stored about its station.
        alongside: The other faults on the same station. Only recovery reads
            them: a station is not owed a heartbeat when its fault closes if
            another fault is still silencing it.
    """
    silence = Silence.around(fault.opened_at, evidence.heartbeats)
    checks = [
        _held(fault, evidence),
        detected(fault, silence),
        no_new_work(silence, evidence),
        replanned(silence, evidence),
        _no_false_miss(fault, evidence),
        _declines_honoured(fault, evidence),
        _recovered(fault, evidence, alongside),
    ]
    if evidence.alert_asked:
        checks.append(_alerted(fault, silence, evidence))
    return FaultVerdict(fault, tuple(checks))


def judge_platform_fault(
    fault: InjectedFault, evidence: PlatformEvidence
) -> FaultVerdict:
    """Answer the questions a fault done to the platform itself asks."""
    closed = fault.closed_at
    if closed is None:
        recovered = Check("recovered", None, "the fault never closed")
    elif fault.kind == "scheduler_down":
        recovered = _within(
            "scheduling round",
            closed,
            evidence.first_round_after,
            ROUND_RECOVERY_WITHIN,
            evidence.as_of,
        )
    else:
        recovered = _within(
            "heartbeat from any station",
            closed,
            evidence.first_heartbeat_after,
            RECOVERY_WITHIN,
            evidence.as_of,
        )
    misses = evidence.reported_misses
    no_false_miss = Check(
        "no_false_miss",
        not misses,
        "no reported pass during it is a confirmed miss"
        if not misses
        else f"reported, and classified confirmed misses: {', '.join(misses)}",
    )
    return FaultVerdict(fault, (no_false_miss, recovered))


def _held(fault: InjectedFault, evidence: StationEvidence) -> Check:
    """Whether the platform stored the silence the ledger claims."""
    if fault.kind not in WHOLLY_SILENCING:
        return Check("held", None, f"{fault.kind} does not stop heartbeats")
    end = fault.closed_at or evidence.as_of
    inside = [one for one in evidence.heartbeats if fault.opened_at < one < end]
    return Check(
        "held",
        not inside,
        "no heartbeat was stored while it was in force"
        if not inside
        else f"{len(inside)} heartbeat(s) stored inside the window, first at "
        f"{inside[0].isoformat()}",
    )


def _no_false_miss(fault: InjectedFault, evidence: StationEvidence) -> Check:
    """No pass the fault touched is a *false* confirmed miss (CLAUDE.md rule 7).

    A confirmed miss is false when the ground truth contradicts it: the ledger
    says the station did not listen — a dead receiver never began the pass, a
    declining station let it go — or the platform holds a report of the pass
    after all. A station that listened and lost its result, to a restart or a
    queue it never drained, did miss the pass, and saying so is not false.
    """
    if fault.kind not in MISS_SENSITIVE:
        return Check("no_false_miss", None, f"{fault.kind} leaves listening intact")
    end = fault.closed_at or evidence.as_of
    not_listened = set(fault.assignment_ids) if fault.kind in NOT_LISTENED else set()
    touched = {
        one.assignment_id
        for one in evidence.work
        if one.start_at < end and one.end_at > fault.opened_at
    } | set(fault.assignment_ids)
    false = sorted(
        one
        for one in touched
        if evidence.classifications.get(one) == "confirmed_miss"
        and (one in not_listened or one in evidence.reported)
    )
    classified = sum(one in evidence.classifications for one in touched)
    return Check(
        "no_false_miss",
        not false,
        f"{classified} of {len(touched)} touched passes classified, no false miss"
        if not false
        else f"confirmed misses the station did not miss: {', '.join(false)}",
    )


def _declines_honoured(fault: InjectedFault, evidence: StationEvidence) -> Check:
    """Each assignment a declining station let go of was revoked as declined.

    Owed only when the platform heard the station again before the work's
    window opened. A decline the platform first learns of after the window
    has begun is MSP §4.2's "absent, window has passed" — ``expired``, not
    ``revoked`` — and is honoured by never being counted as a miss.
    """
    if fault.kind != "declines":
        return Check("declines_honoured", None, "not a decline")
    if not fault.assignment_ids:
        return Check("declines_honoured", None, "it let go of nothing")
    by_id = {one.assignment_id: one for one in evidence.work}
    owed = [
        by_id[one]
        for one in fault.assignment_ids
        if one in by_id and _heard_before_window(fault, by_id[one], evidence)
    ]
    wrong = [one.assignment_id for one in owed if one.declined_at is None]
    return Check(
        "declines_honoured",
        not wrong,
        f"{len(owed)} of {len(fault.assignment_ids)} declined in time, each "
        "revoked as declined"
        if not wrong
        else f"let go of and not revoked as declined: {', '.join(wrong)}",
    )


def _heard_before_window(
    fault: InjectedFault, work: StationWork, evidence: StationEvidence
) -> bool:
    """Whether a heartbeat after the release reached the platform in time."""
    released = fault.acted_at.get(work.assignment_id, fault.opened_at)
    heard = min((one for one in evidence.heartbeats if one > released), default=None)
    return heard is not None and heard < work.start_at


def _recovered(
    fault: InjectedFault,
    evidence: StationEvidence,
    alongside: Sequence[InjectedFault],
) -> Check:
    """Heard again once the station was no longer silenced.

    Measured from the end of every silencing fault overlapping this one, not
    from this one's close alone: a heartbeat delay that ends inside a network
    outage is not owed a heartbeat until the outage ends too.
    """
    if fault.kind not in SILENCING:
        return Check("recovered", None, f"{fault.kind} leaves the station reachable")
    quiet_until = silenced_until(fault, alongside)
    if quiet_until is None:
        return Check("recovered", None, "the station was still silenced at the end")
    after = min(
        (one for one in evidence.heartbeats if one >= quiet_until), default=None
    )
    return _within("heartbeat", quiet_until, after, RECOVERY_WITHIN, evidence.as_of)


def _alerted(
    fault: InjectedFault, silence: Silence, evidence: StationEvidence
) -> Check:
    """When ``StationOffline`` fired, measured from the fault — SC-5's measured half."""
    offline_at = silence.offline_at
    unattributable = _unattributable(offline_at, silence.next, evidence)
    if unattributable is not None:
        return Check("alerted", None, unattributable)
    fired = evidence.alert_fired_at
    if fired is None or offline_at is None:
        return Check("alerted", False, "StationOffline never fired")
    latency = (fired - fault.opened_at).total_seconds()
    after_offline = (fired - offline_at).total_seconds()
    return Check(
        "alerted",
        True,
        f"StationOffline fired {latency:.0f} s after the fault, "
        f"{after_offline:.0f} s after it read offline",
    )


def _unattributable(
    offline_at: datetime | None,
    silence_end: datetime | None,
    evidence: StationEvidence,
) -> str | None:
    """Why this fault is owed no alert, or cannot be timed by one; else ``None``.

    ``StationOffline`` is summed over the fleet (D-197), so it times a fault
    only when it rose for this station: one that went offline long enough for
    a scrape and a rule to see it, while the alert was not already firing for
    another, and not before this station read offline.
    """
    if offline_at is None:
        return "never offline, so no alert was owed"
    fired = evidence.alert_fired_at
    reasons = (
        (
            silence_end is not None and silence_end - offline_at < ALERT_VISIBLE_AFTER,
            "offline too briefly for a scrape and a rule to see it",
        ),
        (
            evidence.alert_already_firing,
            "StationOffline was already firing for another station: not attributable",
        ),
        (
            fired is not None and fired < offline_at,
            "StationOffline rose before this station read offline: not attributable",
        ),
    )
    return next((reason for applies, reason in reasons if applies), None)


def _within(
    what: str,
    since: datetime,
    seen: datetime | None,
    limit: timedelta,
    as_of: datetime,
) -> Check:
    """Whether ``what`` was seen within ``limit`` of ``since``."""
    if seen is None:
        if as_of < since + limit:
            return Check("recovered", None, f"no {what} yet, and too soon to tell")
        return Check("recovered", False, f"no {what} after it ended")
    late = (seen - since).total_seconds()
    return Check(
        "recovered",
        seen - since <= limit,
        f"first {what} {late:.0f} s after it ended",
    )
